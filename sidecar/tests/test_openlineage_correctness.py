"""OpenLineage export, checked as a lineage consumer reads it: every event and facet against the vendored spec,
times in RFC 3339 with an offset, the same output for the same input, one name per dataset whichever event
names it, column lineage that only names what the event has, and nothing private in any name.

Seeded, so a failure repeats; offline (the spec's JSON schemas are copied under tests/fixtures/openlineage)."""

from __future__ import annotations

import datetime as dt
import json
import random
import re
import uuid
from urllib.parse import urlsplit

import pytest

from fabric_desktop.meta import openlineage
from fabric_desktop.service import Service
from tests.test_lineage_scale import validate_all, write_project
from tests.test_openlineage import SPEC, Planted, spec  # noqa: F401 - the fixture is used by name

WS = "5b9c1d2e-3f4a-4b5c-8d6e-7f8091a2b3c4"
ONELAKE = "onelake.dfs.fabric.microsoft.com"
# RFC 3339 section 5.6: a full date, T, a full time, and an offset of Z or +hh:mm (no seconds in it).
RFC3339 = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})$")
# ColumnLineageDatasetFacet 1-2-0: the transformation types and the subtypes each allows.
SUBTYPES = {"DIRECT": {"IDENTITY", "TRANSFORMATION", "AGGREGATION"},
            "INDIRECT": {"JOIN", "GROUP_BY", "FILTER", "SORT", "WINDOW", "CONDITIONAL"}}
SCHEMA_IDS = {json.loads(p.read_text(encoding="utf-8"))["$id"]: json.loads(p.read_text(encoding="utf-8"))
              for p in SPEC.glob("*.json")}


@pytest.fixture(scope="module")
def sample(tmp_path_factory):
    root = tmp_path_factory.mktemp("olc")
    s = Service(root=root / "app")
    s.meta_init(str(root / "crm"), "3")
    return s._project()


def runs_for(project, status="ok", started="2026-11-14T10:00:00+00:00", seconds=2):
    return [{"run_id": f"r-{e}", "entity": e, "status": status, "started_at": started, "seconds": seconds}
            for e in sorted(project.entities)]


def by_job(out):
    return {e["job"]["name"]: e for e in out["events"]}


def facets(event):
    for where in (event["run"], event["job"]):
        yield from where["facets"].items()
    for ds in event["inputs"] + event["outputs"]:
        yield from ds["facets"].items()


def check_events(spec, out):  # noqa: F811 - spec is the fixture's function
    """Every rule a consumer relies on, for one export."""
    events = out["events"]
    validate_all(spec, events)
    outputs = {}
    run_ids = [e["run"]["runId"] for e in events]
    assert len(set(run_ids)) == len(run_ids), "two jobs share a run id"
    for ev in events:
        assert RFC3339.match(ev["eventTime"]), ev["eventTime"]
        assert str(uuid.UUID(ev["run"]["runId"])) == ev["run"]["runId"]
        for url in (ev["producer"], ev["schemaURL"]):
            assert urlsplit(url).scheme == "https" and urlsplit(url).netloc
        for key, facet in facets(ev):
            assert facet["_producer"] == openlineage.PRODUCER
            base = facet["_schemaURL"].partition("#")[0]
            assert base in SCHEMA_IDS, base
            if "facets" in base:  # a facet's own schema says which key it is filed under
                assert key in SCHEMA_IDS[base]["properties"], (key, base)
        [output] = ev["outputs"]
        outputs[ev["job"]["name"]] = (output["namespace"], output["name"])
        inputs = {(i["namespace"], i["name"]): {f["name"] for f in i["facets"]["schema"]["fields"]} for i in ev["inputs"]}
        assert len(inputs) == len(ev["inputs"]), "an input is listed twice"
        assert (output["namespace"], output["name"]) not in inputs
        for ns, name in list(inputs) + [(output["namespace"], output["name"])]:
            assert ns and name, (ns, name)
        schema = [f["name"] for f in output["facets"]["schema"]["fields"]]
        assert len(set(schema)) == len(schema), "a column is listed twice in the schema"
        lineage = output["facets"].get("columnLineage", {}).get("fields", {})
        for col, entry in lineage.items():
            assert col in schema, (ev["job"]["name"], col)
            seen = set()
            for f in entry["inputFields"]:
                key = (f["namespace"], f["name"], f["field"])
                assert key not in seen, ("listed twice", ev["job"]["name"], col, key)
                seen.add(key)
                assert key[:2] in inputs, ("not an input of the event", key)
                assert f["field"] in inputs[key[:2]], ("not in the input's schema", key)
                kinds = [json.dumps(t, sort_keys=True) for t in f["transformations"]]
                assert kinds and len(set(kinds)) == len(kinds), f["transformations"]
                for t in f["transformations"]:
                    assert t["subtype"] in SUBTYPES[t["type"]], t
                    assert isinstance(t["masking"], bool)
    return outputs


# ---- 1, 2. every status, odd start times and durations: valid, RFC 3339, end never before start ------------

STARTS = ["2026-11-14T10:00:00", "2026-11-14T10:00:00Z", "2026-11-14T10:00:00+05:30", "2026-11-14T10:00:00-08:00",
          "2026-11-14T10:00:00.123456+01:00", "2026-11-14 10:00:00", "2026-11-14", "2026-11-14T10:00:00+05:30:15",
          "2026-11-14T10:00:00-00:00:30.5", "0001-01-02T00:00:00+01:00", "2026-11-14T23:59:59-23:59"]
SECONDS = [None, 0, 2, 0.25, -3, 10 ** 6, 1e20, float("nan"), float("inf"), "4", True, False]


def _instant(text):
    t = dt.datetime.fromisoformat(text)
    return t if t.tzinfo else t.replace(tzinfo=dt.timezone.utc)


@pytest.mark.parametrize("status", sorted(openlineage._STATES))
@pytest.mark.parametrize("started", STARTS)
def test_every_status_and_start_validates_and_ends_after_it_starts(sample, spec, status, started):  # noqa: F811
    for seconds in SECONDS:
        out = openlineage.run_events(sample, runs_for(sample, status, started, seconds), "dev")
        check_events(spec, out)
        assert len(out["events"]) == len(sample.entities) and out["left_out"] == []
        for ev in out["events"]:
            assert ev["eventType"] == openlineage._STATES[status]
            end, start = _instant(ev["eventTime"]), _instant(started)
            duration = isinstance(seconds, (int, float)) and not isinstance(seconds, bool) and 0 <= seconds < 1e9
            assert end == start + dt.timedelta(seconds=seconds if duration else 0), (started, seconds, ev["eventTime"])


def test_an_offset_with_seconds_is_written_in_utc_and_a_plain_one_as_given():
    run = {"started_at": "2026-11-14T10:00:00+05:30:15", "seconds": 1}
    assert openlineage._event_time(run) == "2026-11-14T04:29:46+00:00"
    assert openlineage._event_time({**run, "started_at": "2026-11-14T10:00:00+05:30"}) == "2026-11-14T10:00:01+05:30"


def test_true_is_not_a_duration():
    assert openlineage._event_time({"started_at": "2026-11-14T10:00:00Z", "seconds": True}) == "2026-11-14T10:00:00+00:00"


def test_missing_or_unreadable_starts_are_left_out_never_made_up(sample):
    for started in (None, "", "soon", 5):
        runs = [dict(r, started_at=started) for r in runs_for(sample)]
        for r in runs[:2]:
            del r["started_at"]
        out = openlineage.run_events(sample, runs, "dev")
        assert out["events"] == [] and {x["why"] for x in out["left_out"]} == {"its last run has no start time"}


def test_the_same_runs_in_any_order_give_byte_identical_events(sample):
    rng = random.Random(2026)
    starts = ["2026-11-14T10:00:00+00:00", "2026-11-14T11:00:00+01:00", "2026-11-14T10:00:00", "2026-11-13T09:00:00Z"]
    runs = [{"run_id": rng.choice([f"r{j}", str(uuid.UUID(int=j)), None]), "entity": e, "status": rng.choice(["ok", "failed"]),
             "started_at": rng.choice(starts), "seconds": rng.choice([1, 1.0, "1", None])}
            for e in sorted(sample.entities) for j in range(4)]
    want = {}
    for _ in range(25):
        rng.shuffle(runs)
        for ws in (None, WS):
            text = json.dumps(openlineage.run_events(sample, [dict(r) for r in runs], "dev", ws), sort_keys=False)
            assert want.setdefault(ws, text) == text


def test_one_uuid_run_id_on_two_entities_gives_each_job_its_own_run(sample, spec):  # noqa: F811
    shared = str(uuid.UUID(int=42))
    runs = [dict(r, run_id=shared) for r in runs_for(sample)]
    out = openlineage.run_events(sample, runs, "dev")
    check_events(spec, out)
    ids = {e["job"]["name"]: e["run"]["runId"] for e in out["events"]}
    assert ids == {e: str(uuid.uuid5(openlineage._ID_NAMESPACE, f"{shared}/{e}")) for e in sample.entities}
    # a UUID run id only one entity has is kept as it is
    one = openlineage.run_events(sample, [dict(runs[0], run_id="{" + shared.upper() + "}")], "dev")["events"]
    assert one[0]["run"]["runId"] == shared


# ---- 3. one name per dataset: a table's name as its own job's output is the one its readers use --------------

def _identity_project(root, ws_case=str.lower):
    ws = ws_case(WS)
    ents = {
        "bronze.x": {"source": {"files": "landing/x/*.csv"}, "contract": {"columns": {"id": "int64", "a": "int64"}}},
        "bronze.x2": {"source": {"files": "landing/x/*.csv"}, "contract": {"columns": {"id": "int64"}}},
        "silver.y": {"source": {"entity": "bronze.x"}, "contract": {"columns": {"id": "int64", "a": "int64"}}},
        "silver.dbo.z": {"source": {"entity": ["silver.y", "bronze.x2"]}, "contract": {"columns": {"id": "int64"}}},
        # the same tables, read by their OneLake address (abfss, and the https form OneLake shows)
        "gold.by_url": {"source": {"table": f"abfss://{ws}@{ONELAKE}/LH_Silver.Lakehouse/Tables/dbo/y"},
                        "contract": {"columns": {"id": "int64"}}},
        "gold.by_url_slash": {"source": {"table": f"ABFSS://{ws}@{ONELAKE.upper()}/LH_Silver.Lakehouse/Tables/dbo/z/"},
                              "contract": {"columns": {"id": "int64"}}},
        "gold.by_https": {"source": {"table": f"https://{ONELAKE}/{ws}/LH_Silver.Lakehouse/Tables/dbo/y"},
                          "contract": {"columns": {"id": "int64"}}},
        "gold.by_account": {"source": {"table": "abfss://raw@acct.dfs.core.windows.net/delta/orders?sig=zqsig"},  # pragma: allowlist secret (made-up)
                            "contract": {"columns": {"id": "int64"}}},
        "gold.by_account2": {"source": {"table": "abfss://raw@ACCT.dfs.core.windows.net/delta/orders/#v1"},
                             "contract": {"columns": {"id": "int64"}}},
        "gold.by_jdbc": {"source": {"table": "jdbc:sqlserver://db.contoso.com:1433;user=sa;password=zqpw"},  # pragma: allowlist secret (made-up)
                         "contract": {"columns": {"id": "int64"}}},
        "gold.by_jdbc2": {"source": {"table": "jdbc:sqlserver://db.contoso.com:1433;user=ann;password=zqpw2"},  # pragma: allowlist secret (made-up)
                          "contract": {"columns": {"id": "int64"}}},
    }
    return write_project(root, ents)


@pytest.mark.parametrize("ws_case", [str.lower, str.upper])
def test_a_table_is_named_alike_as_an_output_and_as_an_input(tmp_path, spec, ws_case):  # noqa: F811
    project = _identity_project(tmp_path / "p", ws_case)
    runs = runs_for(project)
    for ws in (None, WS, WS.upper()):
        out = openlineage.run_events(project, runs, "dev", ws)
        outputs = check_events(spec, out)
        ev = by_job(out)
        ins = {job: {(i["namespace"], i["name"]) for i in e["inputs"]} for job, e in ev.items()}
        assert ins["silver.y"] == {outputs["bronze.x"]}
        assert ins["silver.dbo.z"] == {outputs["silver.y"], outputs["bronze.x2"]}
        assert ins["bronze.x"] == ins["bronze.x2"]  # one folder of files is one dataset
        assert ins["gold.by_jdbc"] == ins["gold.by_jdbc2"]  # who signs in doesn't make it another table
        assert ins["gold.by_account"] == ins["gold.by_account2"] == {("abfss://raw@acct.dfs.core.windows.net", "delta/orders")}
        lake = (f"abfss://{WS}@{ONELAKE}", "LH_Silver.Lakehouse/Tables/dbo/")
        assert ins["gold.by_url"] == ins["gold.by_https"] == {(lake[0], lake[1] + "y")}
        assert ins["gold.by_url_slash"] == {(lake[0], lake[1] + "z")}
        if ws:  # in Fabric, the address is the table another entity writes
            assert out["namespace"] == lake[0]
            assert ins["gold.by_url"] == {outputs["silver.y"]} and ins["gold.by_url_slash"] == {outputs["silver.dbo.z"]}
        else:
            assert outputs["silver.y"] == ("kratosforge://p/dev", "LH_Silver.dbo.y")


def test_a_list_of_sources_and_plain_names_keep_the_project_namespace(tmp_path):
    project = write_project(tmp_path / "p", {
        "bronze.l": {"source": {"table": [f"abfss://{WS}@{ONELAKE}/LH/Tables/a", "landing/b.csv"]},
                     "contract": {"columns": {"id": "int64"}}},
        "bronze.f": {"source": {"files": "landing/x/*.csv"}, "contract": {"columns": {"id": "int64"}}},
        "bronze.local": {"source": {"table": "C:\\Users\\zqann\\delta\\orders"}, "contract": {"columns": {"id": "int64"}}},
    })
    ev = by_job(openlineage.run_events(project, runs_for(project), "dev"))
    assert [(i["namespace"], i["name"]) for i in ev["bronze.l"]["inputs"]] == [
        ("kratosforge://p/table", f"abfss://{WS}@{ONELAKE}/LH/Tables/a, landing/b.csv")]
    assert [(i["namespace"], i["name"]) for i in ev["bronze.f"]["inputs"]] == [("kratosforge://p/files", "landing/x/*.csv")]
    assert [(i["namespace"], i["name"]) for i in ev["bronze.local"]["inputs"]] == [("kratosforge://p/table", "<this PC>/orders")]


# ---- 4. column lineage: only what the event has, each input field once, transformations the spec allows ----

def _random_project(root, rng):
    """Bronze from files or a OneLake table, silver with renames, cleans and derives over each other (some
    reading one column twice over), dimensions, and facts looking them up (some dimensions missing)."""
    ents = {}
    nb = rng.randint(1, 3)
    for i in range(nb):
        src = rng.choice([{"files": f"landing/b{i}/*.csv"}, {"table": f"abfss://{WS}@{ONELAKE}/LH_Raw.Lakehouse/Tables/b{i}"}])
        ents[f"bronze.b{i}"] = {"source": src, "contract": {"columns": {c: "int64" for c in ("id", "a", "b", "c")}}}
    for i in range(rng.randint(1, 3)):
        up = sorted(rng.sample([f"bronze.b{j}" for j in range(nb)], rng.randint(1, nb)))
        cols = {"id": "int64", "a": "int64", "b2": "int64", "c": "int64"}
        derives = []
        for j in range(rng.randint(0, 4)):
            names = list(cols) + [d["column"] for d in derives]
            used = [rng.choice(names) for _ in range(rng.randint(1, 3))]
            derives.append({"column": f"d{j}", "expr": " + ".join(used)})
            cols[f"d{j}"] = "int64"
        if rng.random() < 0.3:  # a column derived twice, the second time from itself
            derives.append({"column": "a", "expr": "a * 2 + c"})
        ents[f"silver.s{i}"] = {"source": {"entity": up if len(up) > 1 else up[0], "rename": {"b": "b2"}},
                                "contract": {"columns": cols}, "derive": derives,
                                "clean": [{"column": "c", "trim": True}]}
    silvers = [n for n in ents if n.startswith("silver.")]
    ents["gold.dim"] = {"source": {"entity": silvers[0]}, "load": {"mode": rng.choice(["scd1", "scd2"])},
                        "scd": {"surrogate_key": "dim_sk"},
                        "contract": {"keys": ["id"], "columns": {"dim_sk": "int64", "id": "int64", "a": "int64"}}}
    for i in range(rng.randint(1, 3)):
        lookups, cols = [], {"id": "int64", "a": "int64"}
        for k in range(rng.randint(1, 2)):
            dim = rng.choice(["gold.dim", "gold.dim", "gold.gone", silvers[0]])
            lk = {"dim": dim, "on": rng.choice(["id", "a"]), "as": f"sk{k}"}
            if rng.random() < 0.5:
                lk["at"] = rng.choice(["a", "id"])
            lookups.append(lk)
            cols[f"sk{k}"] = "int64"
        ents[f"gold.f{i}"] = {"source": {"entity": rng.choice(silvers)}, "contract": {"columns": cols}, "lookup": lookups}
    return write_project(root, ents)


def test_column_lineage_of_random_projects_names_only_what_the_event_has(tmp_path, spec):  # noqa: F811
    for seed in range(30):
        project = _random_project(tmp_path / f"p{seed}", random.Random(seed))
        for ws in (None, WS):
            outputs = check_events(spec, openlineage.run_events(project, runs_for(project), "dev", ws))
            assert set(outputs) == set(project.entities), seed


def test_the_sample_project_passes_every_rule(sample, spec):  # noqa: F811
    for ws in (None, WS):
        for status in openlineage._STATES:
            check_events(spec, openlineage.run_events(sample, runs_for(sample, status), "dev", ws))


def test_a_column_read_twice_through_its_own_columns_is_one_input_field(tmp_path):
    project = write_project(tmp_path / "p", {
        "bronze.x": {"source": {"files": "landing/x/*.csv"}, "contract": {"columns": {"a": "int64"}}},
        "silver.y": {"source": {"entity": "bronze.x"}, "contract": {"columns": {"a": "int64", "p": "int64", "q": "int64",
                                                                                "r": "int64"}},
                     "derive": [{"column": "p", "expr": "a + 1"}, {"column": "q", "expr": "a * 2"},
                                {"column": "r", "expr": "p + q + a"}]},
    })
    ev = by_job(openlineage.run_events(project, runs_for(project), "dev"))["silver.y"]
    assert ev["outputs"][0]["facets"]["columnLineage"]["fields"]["r"]["inputFields"] == [
        {"namespace": "kratosforge://p/dev", "name": "LH_Bronze.dbo.x", "field": "a",
         "transformations": [{"type": "DIRECT", "subtype": "TRANSFORMATION", "masking": False, "description": "p + q + a"}]}]


def test_a_lookup_into_a_missing_dimension_still_only_joins_on_its_columns(tmp_path):
    project = write_project(tmp_path / "p", {
        "silver.y": {"source": {"files": "landing/y/*.csv"}, "contract": {"columns": {"id": "int64", "d": "date"}}},
        "gold.f": {"source": {"entity": "silver.y"}, "contract": {"columns": {"id": "int64", "sk": "int64"}},
                   "lookup": [{"dim": "gold.gone", "on": "id", "as": "sk", "at": "d"}]},
    })
    ev = by_job(openlineage.run_events(project, runs_for(project), "dev"))["gold.f"]
    sk = ev["outputs"][0]["facets"]["columnLineage"]["fields"]["sk"]["inputFields"]
    assert {(f["field"], f["transformations"][0]["type"], f["transformations"][0]["subtype"]) for f in sk} == {
        ("id", "INDIRECT", "JOIN"), ("d", "INDIRECT", "JOIN")}


# ---- 5. nothing private in any name: job, dataset, field, type or description --------------------------------

def test_no_planted_value_leaves_in_a_job_dataset_field_or_type_name(tmp_path, spec):  # noqa: F811
    rng = random.Random(5150)
    for _ in range(6):
        p = Planted(rng)
        email = p.email()
        user = p.plant("user")
        names = [f"silver.{email}", f"silver.password={p.plant('pw')}.t",  # pragma: allowlist secret (made-up)
                 f"silver.{user}@{p.plant('host')}.t", "silver.ghp_" + "a1" * 18]  # pragma: allowlist secret (made-up)
        ents = {"bronze.x": {"source": {"files": "landing/x/*.csv"},
                             "contract": {"columns": {"a": "int64", email: f"token={p.plant('tok')}"}}}}  # pragma: allowlist secret (made-up)
        for n in names:
            ents[n] = {"source": {"entity": "bronze.x"}, "contract": {"columns": {"a": "int64", email: "string"}}}
        ents["gold.g"] = {"source": {"entity": names[0]}, "contract": {"columns": {"a": "int64"}}}
        root = tmp_path / user
        project = write_project(root, ents)
        (root / "project.toml").write_text(f'[project]\nname = "{p.email()}"\n', encoding="utf-8")
        project = type(project)(root)
        for ws in (None, WS):
            out = openlineage.run_events(project, runs_for(project), "dev", ws)
            check_events(spec, out)
            # job names that clean to the same text may meet; the tables they write are listed by event
            outputs = {(e["outputs"][0]["namespace"], e["outputs"][0]["name"]) for e in out["events"]}
            text = json.dumps(out["events"]).lower()
            for v in p.values:
                assert v not in text, v
            assert "ghp_" not in text
            assert len(out["events"]) == len(ents)  # every entity still has its event
            # cleaned the same way on both sides, so the edge still meets
            [read] = by_job(out)["gold.g"]["inputs"]
            assert (read["namespace"], read["name"]) in outputs


def test_an_object_key_that_looks_like_a_local_path_keeps_the_users_name_on_the_pc():
    # Security QC: gs://b/C:/Users/jane/x went out as the dataset name, user name and all.
    ns, name = openlineage._store_dataset("gs://b/C:/Users/jane/x")
    assert ns == "gs://b" and "jane" not in name
    ns, name = openlineage._store_dataset("https://onelake.dfs.fabric.microsoft.com/ws1/LH.Lakehouse/Files/C:/Users/bob/x.csv")
    assert ns == "abfss://ws1@onelake.dfs.fabric.microsoft.com" and "bob" not in name
    assert openlineage._store_dataset("abfss://ws@acct.dfs.core.windows.net/LH.Lakehouse/Tables/dbo/y")[1] == "LH.Lakehouse/Tables/dbo/y"
