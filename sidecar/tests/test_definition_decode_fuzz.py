"""getDefinition replies that aren't what Fabric documents, read by every module that decodes one itself
(X-ray's staging check, Ossie export and Save TMDL, the connections scan, the opportunities inventory,
the Dataflow Gen2 and Copy job readers, warehouse monitoring, notebookutils on this PC): a null payload,
one that isn't whole base64 (cut, padded wrongly, with junk in it), a payload of another JSON type, no
parts list, parts that aren't objects. Each is refused by name or with the module's own documented
marker ("not_read", problems), or read whole; it is never read as empty or short, and never crashes.
Seeded stdlib fuzzing over fake Fabric clients; nothing reaches the network."""

from __future__ import annotations

import base64
import datetime as dt
import json
import random
from pathlib import Path
from typing import Any, Callable

import pytest

from fabric_desktop import fabric_connections_service as fcs
from fabric_desktop import notebookutils_local as nu
from fabric_desktop import warehouse_monitoring as wm
from fabric_desktop import xray_service
from fabric_desktop.fabric_api import FabricError
from fabric_desktop.opportunities import inventory
from fabric_desktop.orchestration import copyjob, mashup
from tests.test_model_ossie import _bim, svc  # noqa: F401 - svc is a fixture

SEEDS = range(400)
MISSING = object()
REFUSED = object()


def b64(data: bytes | str) -> str:
    return base64.b64encode(data.encode() if isinstance(data, str) else data).decode()


def damaged(good: str) -> list[tuple[str, Any]]:
    """Payloads that aren't ``good`` read whole: each must be refused, never decoded loosely."""
    return [("null", None), ("missing", MISSING), ("not base64", "!!not base64!!"), ("cut", good[:-4] + "!!!!"),
            ("junk inside", good[:4] + "!" + good[4:]), ("space inside", good[:4] + " " + good[4:]),
            ("padding after data", good + "="), ("dropped char", good[1:]), ("number", 7), ("true", True),
            ("list", [good]), ("object", {"payload": good}), ("four items", [good] * 4),
            ("four keys", dict.fromkeys("abcd", good))]


STRUCTURE: list[tuple[str, Callable[[list[Any]], dict[str, Any]]]] = [
    ("no parts key", lambda parts: {"definition": {}}),
    ("parts null", lambda parts: {"definition": {"parts": None}}),
    ("parts text", lambda parts: {"definition": {"parts": "x"}}),
    ("parts number", lambda parts: {"definition": {"parts": 5}}),
    ("definition null", lambda parts: {"definition": None}),
    ("definition a list", lambda parts: {"definition": parts}),
]
NOT_OBJECTS: list[Any] = ["x", 5, None, [], True, ["path", "payload"]]


class Target:
    """One reader: ``run`` returns what it read, or REFUSED when it refused in its own documented way;
    ``refusals`` are the named errors it may raise; ``relevant`` are the parts whose damage must refuse;
    ``documented_empty`` is a result the module documents for a definition with no parts."""

    def __init__(self, name: str, parts: dict[str, bytes], relevant: set[str], run: Callable[[dict[str, Any]], Any],
                 refusals: tuple[type[BaseException], ...], documented_empty: Any = REFUSED):
        self.name, self.parts, self.relevant, self.run = name, parts, relevant, run
        self.refusals, self.documented_empty = refusals, documented_empty

    def outcome(self, definition: dict[str, Any], label: str) -> Any:
        try:
            return self.run(definition)
        except self.refusals:
            return REFUSED
        except Exception as exc:  # noqa: BLE001 - the point: anything else is a crash
            pytest.fail(f"{self.name}, {label}: crashed with {type(exc).__name__}: {exc}")


def definition(parts: dict[str, bytes]) -> list[dict[str, Any]]:
    return [{"path": p, "payload": b64(data), "payloadType": "InlineBase64"} for p, data in parts.items()]


def cases(target: Target, rnd: random.Random | None) -> list[tuple[str, dict[str, Any], bool, bool]]:
    """(label, definition, must_refuse, structural) cases: every fixed damage, or (with ``rnd``) a few random ones."""
    out = []
    for path in target.parts:
        for kind, value in damaged(b64(target.parts[path])):
            parts = definition(target.parts)
            part = next(p for p in parts if p["path"] == path)
            if value is MISSING:
                del part["payload"]
            else:
                part["payload"] = value
            out.append((f"{path} payload {kind}", {"definition": {"parts": parts}}, path in target.relevant, False))
        for odd in NOT_OBJECTS:
            parts = [odd if p["path"] == path else p for p in definition(target.parts)]
            out.append((f"{path} as {odd!r}", {"definition": {"parts": parts}}, path in target.relevant, False))
        parts = [{k: v for k, v in p.items() if k != "path"} if p["path"] == path else p for p in definition(target.parts)]
        out.append((f"{path} without a path", {"definition": {"parts": parts}}, path in target.relevant, False))
    for kind, build in STRUCTURE:
        out.append((kind, build(definition(target.parts)), False, True))
    for odd in NOT_OBJECTS:
        out.append((f"extra part {odd!r}", {"definition": {"parts": [*definition(target.parts), odd]}}, False, False))
    if rnd is None:
        return out
    picked = []
    for _ in range(3):
        label, got, must, structural = rnd.choice(out)
        if not structural and rnd.random() < 0.5:  # a second damage on top
            parts = got["definition"]["parts"]
            if isinstance(parts, list) and parts:
                i = rnd.randrange(len(parts))
                if isinstance(parts[i], dict):
                    kind, value = rnd.choice(damaged(b64(b"another part")))
                    if value is not MISSING:
                        parts[i] = {**parts[i], "payload": value}
                        must = must or parts[i].get("path") in target.relevant
                        label += f" and part {i} payload {kind}"
        picked.append((label, got, must, structural))
    return picked


def check(target: Target, rnd: random.Random | None = None) -> None:
    clean = target.outcome({"definition": {"parts": definition(target.parts)}}, "clean")
    assert clean is not REFUSED, f"{target.name}: the clean definition was refused"
    for label, got, must_refuse, structural in cases(target, rnd):
        result = target.outcome(got, label)
        if result is REFUSED:
            continue
        assert not must_refuse, f"{target.name}, {label}: read {result!r} instead of refusing"
        allowed = [clean] + ([target.documented_empty] if structural else [])
        assert result in allowed, f"{target.name}, {label}: read {result!r}, not the whole definition"


# ---- the readers

MASHUP = 'section Section1;\nshared Sales = let\n    Source = Lakehouse.Contents(null)\nin\n    Source;\n'
META = {"queriesMetadata": {"Sales": {"loadEnabled": True}},
        "connections": [{"kind": "Lakehouse", "path": "Lakehouse", "connectionId": json.dumps(
            {"ClusterId": "c1", "DatasourceId": "8b5c4b8e-0d7a-4a43-9f43-2f1e0c3b7d11"})}]}
DATAFLOW = {"mashup.pq": MASHUP.encode(), "queryMetadata.json": json.dumps(META).encode(),
            ".platform": b'{"metadata": {"type": "Dataflow"}}'}
COPYJOB = {"copyjob-content.json": json.dumps({"properties": {
    "jobMode": "Batch", "source": {"type": "LakehouseTable"}, "destination": {"type": "LakehouseTable"}},
    "activities": []}).encode(), ".platform": b"{}"}
QUERYSET = {"RealTimeQueryset.json": json.dumps({"queryset": {
    "version": "1.0.0", "dataSources": [{"id": "theirs"}], "tabs": [{"id": "their tab", "title": "Mine"}]}}).encode(),
    ".platform": b"{}"}
NOTEBOOK_PY = {"notebook-content.py": (
    '# Fabric notebook source\n# METADATA ********************\n# META {"dependencies": {"lakehouse": '
    '{"default_lakehouse": "lh-1"}}}\n# CELL ********************\nspark.read.table("sales")\n'
    '# CELL ********************\nprint(1)\n').encode(), ".platform": b"{}"}
NOTEBOOK_IPYNB = {"notebook-content.ipynb": json.dumps({
    "metadata": {"dependencies": {"lakehouse": {"default_lakehouse": "lh-2"}}},
    "cells": [{"cell_type": "code", "source": ["x = 1\n", "y = 2"]}, {"cell_type": "markdown", "source": "# t"}]}).encode(),
    ".platform": b"{}"}
CONNECTIONS = {"pipeline-content.json": json.dumps({"properties": {"activities": [
    {"externalReferences": {"connection": "3f1c7a9e-5b2d-4e6f-8a1b-9c0d2e4f6a8b"}}]}}).encode(),
    ".platform": b'{"config": {}}'}
UDF_SOURCE = b'import fabric.functions as fn\nudf = fn.UserDataFunctions()\n\n@udf.function()\ndef hello(name: str) -> str:\n    return name\n'
UDF = {"function_app.py": UDF_SOURCE, "definition.json": b"{}"}


def describe_refused(out: dict[str, Any]) -> Any:
    return REFUSED if out["problems"] else out


def inventory_run(got: dict[str, Any]) -> Any:
    w = inventory.workloads({"id": "ws"}, [{"id": "n1", "type": "Notebook", "displayName": "NB"}], {"n1": got},
                            {}, now=dt.datetime(2026, 1, 1, tzinfo=dt.timezone.utc), days=30)[0]
    return REFUSED if w["definition"] == "not_read" else (w["cells"], w["default_lakehouse"])


class _Fabric:
    def __init__(self) -> None:
        self.got: dict[str, Any] = {}

    def list_notebooks(self, ws: str) -> list[dict[str, Any]]:
        return [{"displayName": "Load sales", "id": "nb1", "workspaceId": ws}]

    def list_items(self, ws: str) -> list[dict[str, Any]]:
        return [{"id": "u1", "displayName": "Funcs", "type": "UserDataFunction", "workspaceId": ws}]

    def get_item_definition(self, ws: str, item: str, fmt: Any = None) -> dict[str, Any]:
        return self.got


@pytest.fixture
def fake(monkeypatch: pytest.MonkeyPatch) -> _Fabric:
    client = _Fabric()
    monkeypatch.setattr(nu, "_client", lambda host: client)
    return client


def plain_targets(fake: _Fabric) -> list[Target]:
    host = nu.Host(token=lambda scope: "tok")
    notebook, udf = nu._make_notebook(host), nu._make_udf(host)

    def via(fn: Callable[[], Any]) -> Callable[[dict[str, Any]], Any]:
        def run(got: dict[str, Any]) -> Any:
            fake.got = got
            return fn()
        return run

    named = (ValueError, FabricError)
    return [
        Target("xray definition text", DATAFLOW, set(DATAFLOW), xray_service._definition_text, named),
        Target("connections parts", CONNECTIONS, set(CONNECTIONS), fcs.definition_parts, named),
        Target("connections dataflow ids", DATAFLOW, {"mashup.pq", "queryMetadata.json"}, fcs.dataflow_connection_ids, named),
        Target("mashup describe", DATAFLOW, {"mashup.pq", "queryMetadata.json"},
               lambda got: describe_refused(mashup.describe(got)), ()),
        Target("copy job describe", COPYJOB, {"copyjob-content.json"}, lambda got: describe_refused(copyjob.describe(got)), ()),
        Target("warehouse queryset", QUERYSET, {"RealTimeQueryset.json"}, wm.read_queryset, named,
               documented_empty={"version": "1.0.0", "dataSources": [], "tabs": []}),
        Target("inventory notebook .py", NOTEBOOK_PY, {"notebook-content.py"}, inventory_run, ()),
        Target("inventory notebook .ipynb", NOTEBOOK_IPYNB, {"notebook-content.ipynb"}, inventory_run, ()),
        Target("notebookutils getDefinition", NOTEBOOK_IPYNB, {"notebook-content.ipynb"},
               via(lambda: notebook.getDefinition("Load sales", "ws1")), (LookupError, *named)),
        Target("notebookutils udf getFunctions", UDF, {"function_app.py"},
               via(lambda: object.__getattribute__(udf.getFunctions("Funcs", "ws1"), "functionDetails")), (LookupError, *named)),
    ]


def ossie_targets(svc: Any) -> list[Target]:  # noqa: F811
    folder = Path(svc._project().root) / "semantic" / "SM_Gold.SemanticModel"
    tmdl = {"definition/model.tmdl": b"model Model\n", "definition/tables/fact_sales.tmdl": b"table fact_sales\n",
            "definition.pbism": b"{}"}
    named = (RuntimeError, ValueError, FabricError)
    tmsl = {"model.bim": json.dumps(_bim()).encode(), "definition.pbism": b"{}"}

    def snapshot() -> dict[str, bytes]:
        return {p.relative_to(folder).as_posix(): p.read_bytes() for p in sorted(folder.rglob("*")) if p.is_file()}

    def save(got: dict[str, Any]) -> Any:
        svc.fake.get_item_definition = lambda ws, item, fmt=None: got
        before = snapshot()
        try:
            svc.meta_model_tmdl_save("test", "SM_Gold")
        except named:
            assert snapshot() == before, "a refused save changed the folder"
            raise
        return snapshot()  # read whole: the folder holds the whole model

    def bim(got: dict[str, Any]) -> Any:
        svc.fake.get_item_definition = lambda ws, item, fmt=None: got
        return svc._model_bim("ws-test", {"id": "m1", "displayName": "SM_Gold"})

    return [Target("ossie model.bim", tmsl, {"model.bim"}, bim, named),
            Target("save TMDL", tmdl, set(tmdl), save, named)]


def test_every_reader_refuses_each_damaged_definition_or_reads_it_whole(fake: _Fabric, svc: Any) -> None:  # noqa: F811
    for target in plain_targets(fake) + ossie_targets(svc):
        check(target)


def test_seeded_combinations_are_refused_or_read_whole(fake: _Fabric) -> None:
    targets = plain_targets(fake)
    for seed in SEEDS:
        rnd = random.Random(seed)
        check(rnd.choice(targets), rnd)


def test_save_tmdl_keeps_the_folder_as_it_was_when_a_part_is_refused(svc: Any) -> None:  # noqa: F811
    """A save that is refused leaves every file, so the folder never holds an empty or short model."""
    target = ossie_targets(svc)[1]
    clean = target.run({"definition": {"parts": definition(target.parts)}})
    for kind, value in damaged(b64(target.parts["definition/tables/fact_sales.tmdl"])):
        parts = definition(target.parts)
        parts[1]["payload"] = value if value is not MISSING else None
        with pytest.raises(FabricError):
            svc.fake.get_item_definition = lambda ws, item, fmt=None, parts=parts: {"definition": {"parts": parts}}
            svc.meta_model_tmdl_save("test", "SM_Gold")
        assert target.run({"definition": {"parts": definition(target.parts)}}) == clean, kind


# ---- regressions: each read the damaged part loosely before

def test_xray_never_reads_a_null_or_cut_dataflow_part_as_text_without_the_lakehouse():
    with pytest.raises(FabricError):
        xray_service._definition_text({"definition": {"parts": [{"path": "mashup.pq", "payload": None}]}})
    cut = b64(MASHUP + 'Lakehouse.Contents("staging-lh")')[:-8] + "!!!!!!!!"
    with pytest.raises(FabricError):
        xray_service._definition_text({"definition": {"parts": [{"path": "mashup.pq", "payload": cut}]}})


def test_save_tmdl_never_writes_an_empty_file_for_a_null_part(svc):  # noqa: F811
    good = svc.fake.get_item_definition("ws", "m1", "TMDL")
    svc.meta_model_tmdl_save("test", "SM_Gold")
    folder = Path(svc._project().root) / "semantic" / "SM_Gold.SemanticModel"
    good["definition"]["parts"][1]["payload"] = None
    svc.fake.get_item_definition = lambda ws, item, fmt=None: good
    with pytest.raises(FabricError, match="definition/tables/fact_sales.tmdl"):
        svc.meta_model_tmdl_save("test", "SM_Gold")
    assert (folder / "definition" / "tables" / "fact_sales.tmdl").read_text() == "table fact_sales\n"


@pytest.mark.parametrize("payload", ["", "IA=="], ids=["empty", "one-space"])
def test_save_tmdl_never_blanks_a_file_for_an_empty_part(svc, payload):  # noqa: F811
    """Fifth quality on #3241: "" is valid base64 of nothing, and was written over the model's file."""
    good = svc.fake.get_item_definition("ws", "m1", "TMDL")
    svc.meta_model_tmdl_save("test", "SM_Gold")
    folder = Path(svc._project().root) / "semantic" / "SM_Gold.SemanticModel"
    good["definition"]["parts"][1]["payload"] = payload
    svc.fake.get_item_definition = lambda ws, item, fmt=None: good
    with pytest.raises(RuntimeError, match="wasn't saved, because Fabric sent definition/tables/fact_sales.tmdl empty"):
        svc.meta_model_tmdl_save("test", "SM_Gold")
    assert (folder / "definition" / "tables" / "fact_sales.tmdl").read_text() == "table fact_sales\n"


def test_an_empty_model_bim_is_refused(svc):  # noqa: F811
    svc.fake.get_item_definition = lambda ws, item, fmt=None: {"definition": {"parts": [
        {"path": "model.bim", "payload": "", "payloadType": "InlineBase64"}]}}
    with pytest.raises(RuntimeError, match="didn't return SM_Gold's model.bim"):
        svc._model_bim("ws-test", {"id": "m1", "displayName": "SM_Gold"})


def test_ossie_model_bim_with_junk_in_its_base64_is_refused(svc):  # noqa: F811
    svc.fake.get_item_definition = lambda ws, item, fmt=None: {"definition": {"parts": [
        {"path": "model.bim", "payload": b64(json.dumps(_bim()))[:8] + "!" + b64(json.dumps(_bim()))[8:]}]}}
    with pytest.raises(FabricError):
        svc._model_bim("ws-test", {"id": "m1", "displayName": "SM_Gold"})


def test_the_connections_scan_counts_a_null_part_as_missed_not_as_using_none():
    with pytest.raises(FabricError):
        fcs.definition_parts({"definition": {"parts": [{"path": "pipeline-content.json", "payload": None}]}})


def test_a_dataflow_whose_query_metadata_cant_be_read_says_so():
    parts = definition(DATAFLOW)
    parts[1]["payload"] = None
    out = mashup.describe({"definition": {"parts": parts}})
    assert out["problems"] == [("queryMetadata.json couldn't be read because Fabric didn't send it as whole base64 "
                               "UTF-8 text, so the dataflow can't be shown in full. Open it in Fabric to check it "
                               "still loads.")]
    with pytest.raises(ValueError, match="queryMetadata.json couldn't be read"):
        fcs.dataflow_connection_ids({"definition": {"parts": parts}})
    parts[1] = "queryMetadata.json"
    assert mashup.describe({"definition": {"parts": parts}})["problems"][0].startswith("A part couldn't be read")


def test_a_copy_job_with_junk_in_its_base64_is_not_read():
    parts = definition(COPYJOB)
    parts[0]["payload"] = parts[0]["payload"][:4] + "!" + parts[0]["payload"][4:]
    assert copyjob.describe({"definition": {"parts": parts}})["problems"]


def test_a_queryset_part_that_isnt_an_object_or_text_is_refused_not_a_crash():
    for parts in (["x"], [{"path": "RealTimeQueryset.json", "payload": 7}], "x"):
        with pytest.raises((ValueError, FabricError)):
            wm.read_queryset({"definition": {"parts": parts}})
    junk = b64(QUERYSET["RealTimeQueryset.json"])
    with pytest.raises(FabricError, match="RealTimeQueryset.json wasn't whole base64, so nothing was saved"):
        wm.read_queryset({"parts": [{"path": "RealTimeQueryset.json", "payload": junk[:4] + "!" + junk[4:]}]})


def test_inventory_marks_a_notebook_with_a_null_payload_not_read_not_empty():
    parts = definition(NOTEBOOK_PY)
    parts[0]["payload"] = None
    assert inventory_run({"definition": {"parts": parts}}) is REFUSED
    w = inventory.workloads({"id": "ws"}, [{"id": "n1", "type": "Notebook", "displayName": "NB"}],
                            {"n1": {"definition": {"parts": parts}}}, {}, now=dt.datetime(2026, 1, 1, tzinfo=dt.timezone.utc), days=30)[0]
    assert w["problem"] == ("The definition couldn't be read: the notebook's content was left out because Fabric "
                            "didn't send it as whole base64. Open the notebook in Fabric to check it still loads.")


def test_notebookutils_get_definition_refuses_a_null_payload_by_name(fake):
    fake.got = {"definition": {"parts": [{"path": "notebook-content.ipynb", "payload": None}]}}
    with pytest.raises(FabricError):
        nu._make_notebook(nu.Host(token=lambda scope: "tok")).getDefinition("Load sales", "ws1")
    fake.got = {"definition": None}
    with pytest.raises(LookupError):
        nu._make_notebook(nu.Host(token=lambda scope: "tok")).getDefinition("Load sales", "ws1")


def test_notebookutils_udf_refuses_a_null_function_file(fake):
    fake.got = {"definition": {"parts": [{"path": "function_app.py", "payload": None}]}}
    with pytest.raises(FabricError):
        nu._make_udf(nu.Host(token=lambda scope: "tok")).getFunctions("Funcs", "ws1")
