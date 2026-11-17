"""OpenLineage export: one run event per entity's last recorded run, with column lineage, never made up."""

import json
from pathlib import Path
import uuid

import pytest

from fabric_desktop import known_folders
from fabric_desktop.meta import openlineage
from fabric_desktop.service import Service


@pytest.fixture
def svc(tmp_path):
    s = Service(root=tmp_path / "app")
    s.meta_init(str(tmp_path / "crm"), "3")
    return s


RUNS = [
    {"run_id": "r1", "entity": "gold.fact_sales", "status": "ok", "started_at": "2026-11-14T10:00:00+00:00", "seconds": 3.5},
    {"run_id": "r0", "entity": "gold.fact_sales", "status": "failed", "started_at": "2026-11-13T10:00:00+00:00", "seconds": 1},
    {"run_id": str(uuid.UUID(int=7)), "entity": "silver.orders", "status": "failed", "started_at": "2026-11-14T09:00:00"},
    {"run_id": "r9", "entity": "bronze.orders", "status": "running", "started_at": "2026-11-14T08:00:00"},
    {"run_id": "r8", "entity": "silver.nope", "status": "ok", "started_at": "2026-11-14T08:00:00"},
]


def events(svc, runs=RUNS, workspace=None):
    return openlineage.run_events(svc._project(), runs, "dev", workspace)


def by_job(out):
    return {e["job"]["name"]: e for e in out["events"]}


def test_one_event_per_entity_for_its_last_run(svc):
    out = events(svc)
    ev = by_job(out)
    assert set(ev) == {"gold.fact_sales", "silver.orders"}
    sales = ev["gold.fact_sales"]
    assert sales["eventType"] == "COMPLETE" and sales["eventTime"] == "2026-11-14T10:00:03.500000+00:00"
    assert sales["schemaURL"].startswith("https://openlineage.io/spec/2-0-2/")
    assert sales["job"]["namespace"] == "kratosforge://crm"
    assert ev["silver.orders"]["eventType"] == "FAIL"
    assert ev["silver.orders"]["run"]["runId"] == str(uuid.UUID(int=7))  # a UUID run id is kept
    assert uuid.UUID(sales["run"]["runId"]) == uuid.uuid5(openlineage._ID_NAMESPACE, "r1/gold.fact_sales")
    assert {x["entity"]: x["why"] for x in out["left_out"]} == {
        "bronze.customers": "hasn't run in Dev", "bronze.orders": "its last run is running",
        "gold.dim_customer": "hasn't run in Dev", "silver.customers": "hasn't run in Dev"}


def test_column_lineage_names_inputs_and_how_each_column_is_made(svc):
    sales = by_job(events(svc))["gold.fact_sales"]
    out = sales["outputs"][0]
    assert (out["namespace"], out["name"]) == ("kratosforge://crm/dev", "LH_Gold.dbo.fact_sales")
    assert {i["name"] for i in sales["inputs"]} == {"LH_Silver.dbo.orders", "LH_Gold.dbo.dim_customer"}
    fields = out["facets"]["columnLineage"]["fields"]
    order_id = fields["order_id"]["inputFields"]
    assert order_id == [{"namespace": "kratosforge://crm/dev", "name": "LH_Silver.dbo.orders", "field": "order_id",
                         "transformations": [{"type": "DIRECT", "subtype": "IDENTITY", "masking": False}]}]
    sk = {(f["name"], f["field"]): f["transformations"][0] for f in fields["customer_sk"]["inputFields"]}
    assert sk[("LH_Gold.dbo.dim_customer", "customer_sk")]["type"] == "DIRECT"
    assert sk[("LH_Silver.dbo.orders", "customer_id")] == {"type": "INDIRECT", "subtype": "JOIN", "masking": False}
    assert sk[("LH_Silver.dbo.orders", "order_date")]["subtype"] == "JOIN"
    assert "_run_id" not in fields  # framework columns have no inputs
    assert {"name": "order_id", "type": "int64"} in out["facets"]["schema"]["fields"]


def test_own_columns_trace_back_to_where_they_start(svc):
    orders = by_job(events(svc))["silver.orders"]
    fields = orders["outputs"][0]["facets"]["columnLineage"]["fields"]
    net = {(f["name"], f["field"]) for f in fields["net_amount"]["inputFields"]}
    assert net == {("LH_Bronze.dbo.orders", "amount"), ("LH_Bronze.dbo.orders", "discount")}
    assert {f["transformations"][0]["description"] for f in fields["net_amount"]["inputFields"]} == {"amount - coalesce(discount, 0)"}
    # status is cleaned in silver, so it reads bronze's as it is
    assert fields["status"]["inputFields"][0]["transformations"][0]["subtype"] == "IDENTITY"


def test_files_are_an_input_of_their_own(svc):
    runs = [{"run_id": "b", "entity": "bronze.orders", "status": "ok", "started_at": "2026-11-14T08:00:00"}]
    bronze = by_job(events(svc, runs))["bronze.orders"]
    assert [(i["namespace"], i["name"]) for i in bronze["inputs"]] == [("kratosforge://crm/files", "landing/orders/*.csv")]


def test_an_environment_in_fabric_uses_onelake_paths(svc):
    sales = by_job(events(svc, workspace="ws-123"))["gold.fact_sales"]
    out = sales["outputs"][0]
    assert out["namespace"] == "abfss://ws-123@onelake.dfs.fabric.microsoft.com"
    assert out["name"] == "LH_Gold.Lakehouse/Tables/dbo/fact_sales"


def test_a_run_without_a_start_time_is_left_out(svc):
    out = events(svc, [{"run_id": "x", "entity": "gold.fact_sales", "status": "ok", "started_at": "soon"}])
    assert out["events"] == []
    assert {"entity": "gold.fact_sales", "why": "its last run has no start time"} in out["left_out"]


def test_export_writes_a_new_file_of_events_and_preview_writes_nothing(svc, tmp_path, monkeypatch):
    monkeypatch.setattr(known_folders, "downloads", lambda: tmp_path / "Downloads")
    pre = svc.meta_openlineage_preview("dev")
    assert pre["events"] == 0 and len(pre["left_out"]) == 6
    with pytest.raises(ValueError, match="Nothing to export"):
        svc.meta_openlineage_export("dev")
    monkeypatch.setattr(openlineage, "run_events", lambda project, runs, env, ws=None: {
        "env": env, "namespace": "n", "events": [{"a": 1}, {"b": 2}], "left_out": []})
    first = svc.meta_openlineage_export("dev")
    second = svc.meta_openlineage_export("dev")
    assert first["path"] != second["path"] and first["events"] == 2
    lines = open(first["path"], encoding="utf-8").read().splitlines()
    assert [json.loads(x) for x in lines] == [{"a": 1}, {"b": 2}]
    assert first["path"].endswith("crm-dev-openlineage.jsonl")


# ---- against the OpenLineage spec's own JSON schemas (tests/fixtures/openlineage, copied unchanged)

SPEC = Path(__file__).parent / "fixtures" / "openlineage"


@pytest.fixture(scope="module")
def spec():
    from jsonschema import Draft202012Validator
    from referencing import Registry, Resource

    docs = [json.loads(p.read_text(encoding="utf-8")) for p in SPEC.glob("*.json")]
    registry = Registry().with_resources((d["$id"], Resource.from_contents(d)) for d in docs)

    def validate(instance, url):
        base, _, pointer = url.partition("#")
        Draft202012Validator({"$ref": url}, registry=registry).validate(instance)
        assert base in {d["$id"] for d in docs}, f"{base} is not one of the copied schemas"
        return pointer

    return validate


def _all_facets(event):
    yield from event["run"]["facets"].values()
    yield from event["job"]["facets"].values()
    for ds in event["inputs"] + event["outputs"]:
        yield from ds["facets"].values()


@pytest.mark.parametrize("status,kind", [("ok", "COMPLETE"), ("failed", "FAIL")])
def test_every_event_type_validates_against_the_spec(svc, spec, status, kind):
    runs = [{"run_id": f"r-{e}", "entity": e, "status": status, "started_at": "2026-11-14T10:00:00+00:00", "seconds": 2}
            for e in svc._project().entities]
    for ws in (None, "ws-123"):
        out = events(svc, runs, ws)
        assert len(out["events"]) == 6 and out["left_out"] == []
        for ev in out["events"]:
            assert ev["eventType"] == kind
            spec(ev, ev["schemaURL"])
            for facet in _all_facets(ev):
                spec(facet, facet["_schemaURL"])
    assert set(openlineage._STATES.values()) == {"COMPLETE", "FAIL"}  # every type the export emits is covered here


def test_no_secrets_connection_strings_or_paths_on_this_pc_leave_in_events(svc, tmp_path):
    root = tmp_path / "crm"
    text = (root / "metadata" / "entities" / "bronze.orders.toml").read_text(encoding="utf-8")
    secret_url = "abfss://raw@acct.dfs.core.windows.net/orders?sv=2024-01-01&sig=SECRETSIG"
    (root / "metadata" / "entities" / "bronze.orders.toml").write_text(
        text.replace('files = "landing/orders/*.csv"', f'table = "{secret_url}"'), encoding="utf-8")
    text = (root / "metadata" / "entities" / "bronze.customers.toml").read_text(encoding="utf-8")
    (root / "metadata" / "entities" / "bronze.customers.toml").write_text(
        text.replace('files = "landing/customers/*.csv"', 'files = "C:\\\\Users\\\\ana\\\\exports\\\\customers.csv"'),
        encoding="utf-8")
    text = (root / "metadata" / "entities" / "silver.orders.toml").read_text(encoding="utf-8")
    (root / "metadata" / "entities" / "silver.orders.toml").write_text(
        text.replace('expr = "amount - coalesce(discount, 0)"', 'expr = "amount - coalesce(discount, 0) -- password=hunter2"'),
        encoding="utf-8")
    runs = [{"run_id": "x", "entity": e, "status": "ok", "started_at": "2026-11-14T10:00:00", "message": "token=abc C:\\Users\\ana"}
            for e in ("bronze.orders", "bronze.customers", "silver.orders")]
    out = json.dumps(events(svc, runs))
    for leak in ("SECRETSIG", "sig=", "sv=", "ana", "hunter2", "token=abc", "Users"):
        assert leak not in out, leak
    assert "abfss://raw@acct.dfs.core.windows.net/orders" in out
    assert "<this PC>/customers.csv" in out


@pytest.mark.parametrize("value,want", [
    ("landing/orders/*.csv", "landing/orders/*.csv"),
    ("abfss://ws@onelake.dfs.fabric.microsoft.com/LH.Lakehouse/Tables/x?sig=abc", "abfss://ws@onelake.dfs.fabric.microsoft.com/LH.Lakehouse/Tables/x"),
    ("abfss://raw:key@acct.dfs.core.windows.net/p", "abfss://acct.dfs.core.windows.net/p"),
    ("https://ann:pw@host/path?code=1#f", "https://host/path"),
    ("/home/ann/x.csv", "<this PC>/x.csv"),
    ("~/x.csv", "<this PC>/x.csv"),
    ("erp.dbo.orders;Password=abc", "erp.dbo.orders;Password=[removed]"),
    (["a", "b"], "a, b"),
    ("file:///C:/Users/chris/Secret/data.csv", "<this PC>/data.csv"),
    ("FILE://c:/x/y.csv", "<this PC>/y.csv"),
    (["https://h/x?sig=1", "C:/Users/chris/y.csv", "landing/z.csv"], "https://h/x, <this PC>/y.csv, landing/z.csv"),
    ("Server=s;User ID=a;Password=p;Database=d", "Server=s;User ID=[removed];Password=[removed];Database=d"),
    ("x;user=sa;uid=b", "x;user=[removed];uid=[removed]"),
    ("C:data\\q.csv", "<this PC>/q.csv"),
    ("../../secret/a.csv", "<this PC>/a.csv"),
    ("jdbc:sqlserver://db.contoso.com:1433;user=sa;password=x", "jdbc:sqlserver://db.contoso.com:1433;user=[removed];password=[removed]"),
    ("exports from ann@contoso.com", "exports from [email]"),
    ("https://h/inbox/ann@contoso.com/x", "https://h/inbox/[email]/x"),
])
def test_safe_origin(value, want):
    assert openlineage.safe_origin(value) == want


def test_the_spec_check_refuses_a_broken_event(svc, spec):
    from jsonschema import ValidationError

    ev = by_job(events(svc))["gold.fact_sales"]
    for broken, url in ((dict(ev, eventTime=None), ev["schemaURL"]), (dict(ev, eventType="DONE"), ev["schemaURL"]),
                        ({**ev["job"]["facets"]["jobType"], "processingType": 1}, ev["job"]["facets"]["jobType"]["_schemaURL"])):
        with pytest.raises(ValidationError):
            spec(broken, url)


def test_paths_in_an_expression_keep_only_their_last_part():
    text = "coalesce(path, 'C:\\\\Users\\\\chris\\\\x.csv') || 'abc' || \"/home/ann/q\" || 'file:///D:/s/t.csv' -- password=1 ann@contoso.com"
    out = openlineage.safe_text(text)
    assert out == "coalesce(path, '<this PC>/x.csv') || 'abc' || \"<this PC>/q\" || '<this PC>/t.csv' -- password=[removed] [email]"


def test_patterns_in_an_expression_are_not_read_as_paths():
    for text in ("regexp_replace(code, '..\\\\d+', '')", "regexp_extract(p, '/a/(\\\\w+)/', 1)", "'a/b'", "'..'"):
        assert openlineage.safe_text(text) == text
