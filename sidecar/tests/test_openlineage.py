"""OpenLineage export: one run event per entity's last recorded run, with column lineage, never made up."""

import json
from pathlib import Path
import random
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
    secret_url = "abfss://raw@acct.dfs.core.windows.net/orders?sv=2024-01-01&sig=SECRETSIG"  # pragma: allowlist secret (a made-up SAS the test checks is removed)
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
    ("abfss://raw:key@acct.dfs.core.windows.net/p", "abfss://acct.dfs.core.windows.net/p"),  # pragma: allowlist secret (made-up)
    ("https://ann:pw@host/path?code=1#f", "https://host/path"),  # pragma: allowlist secret (made-up)
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
    ("https://api.contoso.com:8443/v1/orders?key=1", "https://api.contoso.com:8443/v1/orders"),
    ("https://h/app;jsessionid=0AB1C2/orders;v=2/x.csv", "https://h/app/orders/x.csv"),
    ("http://[2001:db8::1]:8080/feed", "http://[2001:db8::1]:8080/feed"),
    ("https://h:99999/x", "https://h/x"),
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


def test_the_last_run_is_the_latest_by_time_not_by_text(svc):
    runs = [{"run_id": "late", "entity": "gold.fact_sales", "status": "ok", "started_at": "2026-11-14T08:30:00+00:00"},
            {"run_id": "early", "entity": "gold.fact_sales", "status": "failed", "started_at": "2026-11-14T09:00:00+01:00"},
            {"run_id": "bad", "entity": "gold.fact_sales", "status": "failed", "started_at": "soon"}]
    for order in (runs, runs[::-1]):
        ev = by_job(events(svc, order))["gold.fact_sales"]
        assert ev["eventType"] == "COMPLETE", order  # 08:30Z is after 09:00+01:00 (08:00Z)


# ---- property-style checks: sources and expressions built from random parts, with planted values that must
# never leave. Seeded, so a failure repeats; every planted value is made up at run time.

class Planted:
    """Random parts for one source or expression, and every planted value that must not come out."""

    WORDS = ("orders", "sales", "landing", "raw", "dbo", "erp", "crm", "data", "v1", "exports")
    HOSTS = ("db.contoso.com", "api.example.org", "acct.dfs.core.windows.net", "onelake.dfs.fabric.microsoft.com",
             "10.0.0.5", "[2001:db8::1]", "bucket.s3.amazonaws.com")

    def __init__(self, rng):
        self.rng, self.values = rng, []

    def plant(self, kind):
        # lower-case letters only, so no sanitizer splits it, behind a prefix no fixed word has
        value = f"zq{kind}" + "".join(self.rng.choice("bcdfghjkmnpqrstvwx") for _ in range(10))
        self.values.append(value)
        return value

    def word(self):
        return self.rng.choice(self.WORDS)

    def email(self):
        return f"{self.plant('mail')}.{self.plant('mail')}@{self.plant('dom')}.com"

    def file_name(self):
        return f"{self.word()}_{self.rng.randint(1, 99)}." + self.rng.choice(("csv", "parquet", "json"))

    def folders(self):
        return [self.plant("dir") for _ in range(self.rng.randint(1, 3))]

    def path(self):
        """A path on this PC: its folders are planted, its last part (a file name) may stay."""
        r, f = self.rng, self.file_name()
        kind = r.choice(("win", "win/", "unc", "posix", "home", "drive", "up", "email_name", "file"))
        if kind == "win":
            return "C:\\" + "\\".join(["Users", *self.folders()]) + "\\" + f
        if kind == "win/":
            return "D:/" + "/".join(self.folders()) + "/" + f
        if kind == "unc":
            return "\\\\" + "\\".join([self.plant("host"), *self.folders()]) + "\\" + f
        if kind == "posix":
            return "/" + "/".join([r.choice(("home", "Users", "srv", "mnt")), *self.folders()]) + "/" + f
        if kind == "home":
            return "~/" + "/".join(self.folders()) + "/" + f
        if kind == "drive":
            return "C:" + "\\".join(self.folders()) + "\\" + f
        if kind == "up":
            return "../../" + "/".join(self.folders()) + "/" + f
        if kind == "email_name":
            return "C:\\" + "\\".join(self.folders()) + "\\" + self.email() + ".csv"
        lead = r.choice(("file:///", "FILE://", "file:/", "File:///"))
        start = r.choice(("C:/", "home/", "Users/"))
        tail = r.choice(("", f"?{self.plant('q')}={self.plant('q')}", f"#{self.plant('frag')}"))
        return lead + start + "/".join(self.folders()) + "/" + f + tail

    def query(self):
        r = self.rng
        keys = ("sig", "code", "key", "sv", "X-Amz-Signature", "q", "user")
        return "?" + "&".join(f"{r.choice(keys)}={self.plant('q')}" for _ in range(r.randint(1, 3)))

    def url(self):
        r = self.rng
        scheme = r.choice(("http", "https", "HTTPS", "abfss", "wasbs", "s3", "jdbc:postgresql", "jdbc:mysql"))
        web = scheme.lower() in ("http", "https")
        auth = r.choice(("", "user", "userpass"))
        info = ""
        if auth == "user" and scheme not in ("abfss", "wasbs"):  # before @ on a storage URL is its container
            info = self.plant("user") + "@"
        elif auth == "userpass":
            info = f"{self.plant('user')}:{self.plant('pw')}@"
        segs = [self.word() for _ in range(r.randint(0, 3))]
        if web and r.random() < 0.4:
            segs.insert(r.randrange(len(segs) + 1), f"{self.word()};jsessionid={self.plant('q')}")
        if web and r.random() < 0.2:
            segs.append(self.email())
        url = f"{scheme}://{info}{r.choice(self.HOSTS)}{r.choice(('', ':1433', ':8443', ':99999'))}/" + "/".join(segs)
        if r.random() < 0.6:
            url += self.query()
        if r.random() < 0.3:
            url += "#" + self.plant("frag")
        return url

    def jdbc(self):
        r = self.rng
        if r.random() < 0.3:
            return f"jdbc:oracle:thin:{self.plant('user')}/{self.plant('pw')}@db.contoso.com:1521:orcl"
        if r.random() < 0.3:  # Oracle forms with no ://, so urlsplit never sees their query
            return r.choice((f"jdbc:oracle:thin:@//db.contoso.com:1521/svc?wallet_password={self.plant('pw')}",
                             f"jdbc:oracle:thin:@db:1521:orcl?oracle.net.wallet_password={self.plant('pw')}",
                             f"jdbc:oracle:thin:@db:1521:orcl#{self.plant('frag')}"))
        parts = [f"user={self.plant('user')}", f"password={self.plant('pw')}", "databaseName=sales", "encrypt=true"]
        r.shuffle(parts)
        return f"jdbc:sqlserver://db.contoso.com{r.choice(('', ':1433'))};" + ";".join(parts)

    def connection_string(self):
        """ADO.NET or ODBC: user names with spaces, {braced} and "quoted" values holding a ;, keys and SAS."""
        r = self.rng
        eq = r.choice(("=", " = ", "= "))
        user = self.plant("user") + r.choice(("", " " + self.plant("user")))
        user = r.choice((user, "{" + user + "}"))
        pw = r.choice((self.plant("pw"), "{" + self.plant("pw") + ";" + self.plant("pw") + "}",
                       '"' + self.plant("pw") + ";" + self.plant("pw") + '"', self.plant("pw") + " " + self.plant("pw")))
        parts = [f"Server{eq}tcp:db.contoso.com,1433", f"Database{eq}sales",
                 f"{r.choice(('User ID', 'User Id', 'uid', 'UID', 'user', 'username', 'User Name'))}{eq}{user}",
                 f"{r.choice(('Password', 'pwd', 'PWD'))}{eq}{pw}"]
        if r.random() < 0.4:
            parts.append(f"AccountKey{eq}{self.plant('key')}==")
        if r.random() < 0.4:
            parts.append(f"SharedAccessSignature{eq}sv=2024-01-01&sr=c&sig={self.plant('sig')}")
        if r.random() < 0.3:
            parts.insert(0, "Driver={ODBC Driver 18 for SQL Server}")
        if r.random() < 0.3:  # Snowflake-style keys with a prefix
            parts.append(f"{r.choice(('private_key_pwd', 'oauth_token', 'client_secret', 'wallet_password'))}{eq}"
                         f"{self.plant('pw')}")
        r.shuffle(parts)
        return ";".join(parts) + r.choice(("", ";"))

    def source(self):
        return self.rng.choice((self.url, self.url, self.path, self.path, self.jdbc, self.connection_string,
                                lambda: f"exports from {self.email()}"))()


def _check_clean(out, planted):
    low = out.lower()  # a URL's host comes out lower case, so a planted value is looked for in any case
    for v in planted:
        assert v not in low, (v, out)


def test_safe_origin_never_lets_a_planted_value_out_and_never_raises():
    rng = random.Random(20261118)
    for _ in range(3000):
        p = Planted(rng)
        value = p.source() if rng.random() < 0.7 else [p.source() for _ in range(rng.randint(1, 4))]
        _check_clean(openlineage.safe_origin(value), p.values)


def test_safe_origin_never_raises_on_odd_input():
    rng = random.Random(7)
    alphabet = ":/\\@?#;=[]{}%~.\"' \t\nAbc019-_"
    odd = [None, 0, 1.5, b"x", {"password": "zqpwbcdf"}, [], (), [["a"]], "", " ", "://", "http://[bad",  # pragma: allowlist secret (made-up)
           "file:", "abfss://@/", "https://h:/", "jdbc:", "\\\\", "~", "C:", "jdbc:oracle:thin:@"]
    odd += ["".join(rng.choice(alphabet) for _ in range(rng.randint(1, 30))) for _ in range(2000)]
    for value in odd:
        assert isinstance(openlineage.safe_origin(value), str)


REGEX_LITERALS = (r"^https?://([^/]+)", r"https://([^/]+)/(.*)", r"file:(\d+)", r"..\d+", r"/a/(\w+)/",
                  r"[A-Z]{3}-\d+", r"http://example\.com/.*", r"(?i)^C:\\", r"\s+$", r"^/data/[0-9]+\.csv$")
PLAIN_LITERALS = ("returned", "a/b", "..", "N/A", "abc", "", "2026-11-18", "x;y", "50%")


def _expression(p):
    """A derive-like expression whose quoted literals are paths and URLs (with planted values), regexes and
    plain text (which must come out unchanged), maybe with a comment naming a secret or an e-mail address."""
    r = p.rng
    keep, terms = [], []
    for _ in range(r.randint(1, 5)):
        kind = r.choice(("path", "url", "regex", "plain"))
        q = r.choice("'\"") if kind in ("path", "url") else "'"
        if kind == "path":
            lit = p.path()
        elif kind == "url":
            lit = p.url()
        else:
            lit = r.choice(REGEX_LITERALS if kind == "regex" else PLAIN_LITERALS)
            keep.append(f"'{lit}'")
        terms.append(r.choice(("coalesce(c, {})", "regexp_like(c, {})", "concat(c, {})", "c = {}")).format(q + lit + q))
    text = " || ".join(terms)
    if r.random() < 0.5:
        text += r.choice((f" -- password={p.plant('pw')}", f" /* token={p.plant('pw')} */", f" -- ask {p.email()}"))
    return text, keep


def test_safe_text_removes_planted_values_and_keeps_patterns_and_plain_text():
    rng = random.Random(4242)
    for _ in range(2000):
        p = Planted(rng)
        text, keep = _expression(p)
        out = openlineage.safe_text(text)
        _check_clean(out, p.values)
        for lit in keep:
            assert lit in out, (lit, text, out)


@pytest.mark.parametrize("text", [f"regexp_like(c, '{x}')" for x in REGEX_LITERALS])
def test_a_pattern_literal_comes_out_unchanged(text):
    assert openlineage.safe_text(text) == text


@pytest.mark.parametrize("text,want", [
    ("c = '../../ann/x.csv'", "c = '<this PC>/x.csv'"),
    ("c = 'C:ann\\x.csv'", "c = '<this PC>/x.csv'"),
    ("c = 'https://h/x?q=ann'", "c = 'https://h/x'"),
    ("c = 'http://[2001:db8::1]:80/x?q=ann'", "c = 'http://[2001:db8::1]:80/x'"),
])
def test_paths_and_urls_the_property_tests_found_in_literals(text, want):
    assert openlineage.safe_text(text) == want


def test_a_double_quoted_path_is_a_column_to_sql_and_is_cleaned_as_one(svc, tmp_path):
    f = tmp_path / "crm" / "metadata" / "entities" / "silver.orders.toml"
    expr = 'amount + 0 * length(\\"C:\\\\\\\\Users\\\\\\\\ann\\\\\\\\x.csv\\") + 0 * length(\\"https://h/x?k=zqv\\")'
    f.write_text(f.read_text(encoding="utf-8").replace('expr = "amount - coalesce(discount, 0)"', f'expr = "{expr}"'),
                 encoding="utf-8")
    runs = [{"run_id": "a", "entity": "silver.orders", "status": "failed", "started_at": "2026-11-14T10:00:00Z"}]
    ev = by_job(events(svc, runs))["silver.orders"]
    fields = {f["field"] for f in ev["outputs"][0]["facets"]["columnLineage"]["fields"]["net_amount"]["inputFields"]}
    assert fields == {"amount", "<this PC>/x.csv", "https://h/x"}
    text = json.dumps(ev)
    assert "ann" not in text and "zqv" not in text


@pytest.mark.parametrize("value,want", [
    # each of these let a planted value out before
    ("file:///C:/x/y.csv?user=ann&k=v#frag", "<this PC>/y.csv"),
    ("C:\\x\\ann@contoso.com.csv", "<this PC>/[email]"),
    ("jdbc:postgresql://ann:pw@10.0.0.5:5432/db", "jdbc:postgresql://10.0.0.5:5432/db"),  # pragma: allowlist secret (made-up)
    ("Driver={ODBC};UID={ann lee};PWD={pa;ss}", "Driver={ODBC};UID=[removed];PWD=[removed]"),  # pragma: allowlist secret (made-up)
    ("Data Source=s;User Id = ann lee;Password = two words", "Data Source=s;User Id = [removed];Password = [removed]"),  # pragma: allowlist secret (made-up)
    ('Server=s;Password="a;b"', "Server=s;Password=[removed]"),  # pragma: allowlist secret (made-up)
    ("jdbc:oracle:thin:scott/tiger@db.contoso.com:1521:orcl", "jdbc:oracle:thin:[removed]@db.contoso.com:1521:orcl"),  # pragma: allowlist secret (made-up)
    ("Server=s;SharedAccessSignature=sv=2024-01-01&sr=c&sig=abc;AccountKey=k==", "Server=s;SharedAccessSignature=[removed];AccountKey=[removed]"),  # pragma: allowlist secret (made-up)
])
def test_safe_origin_closes_what_the_property_tests_found(value, want):
    assert openlineage.safe_origin(value) == want


# ---- end to end: every planted kind in a project's sources and expressions, through every event

GUID_WS = "5b9c1d2e-3f4a-4b5c-8d6e-7f8091a2b3c4"


def _plant_project(root, rng):
    p = Planted(rng)
    ents = root / "metadata" / "entities"

    def edit(name, old, new):
        f = ents / f"{name}.toml"
        text = f.read_text(encoding="utf-8")
        assert old in text, (name, old)
        f.write_text(text.replace(old, new), encoding="utf-8")

    files = [p.path() for _ in range(6)] + [p.url(), f"exports from {p.email()}", "landing/orders/*.csv"]
    edit("bronze.orders", 'files = "landing/orders/*.csv"', "files = " + json.dumps(files))
    table = [p.connection_string(), p.jdbc(), p.url()]
    edit("bronze.customers", 'files = "landing/customers/*.csv"', "table = " + json.dumps(table))
    expr = (f"amount - coalesce(discount, 0) + 0 * length(concat(status, '{p.path()}', \"{p.url()}\", "
            f"'{REGEX_LITERALS[1]}')) -- pwd={p.plant('pw')} {p.email()}")
    edit("silver.orders", 'expr = "amount - coalesce(discount, 0)"', "expr = " + json.dumps(expr))
    return p


@pytest.mark.parametrize("status", ["ok", "failed"])
def test_no_planted_value_is_in_any_event_and_every_event_still_validates(svc, spec, tmp_path, status):
    p = _plant_project(tmp_path / "crm", random.Random(99))
    runs = [{"run_id": f"r-{e}", "entity": e, "status": status, "started_at": "2026-11-14T10:00:00+01:00", "seconds": 2,
             "message": f"password={p.plant('pw')}"} for e in svc._project().entities]
    for ws in (None, GUID_WS):
        out = events(svc, runs, ws)
        assert len(out["events"]) == 6 and out["left_out"] == []
        _check_clean(json.dumps(out), p.values)
        for ev in out["events"]:
            assert ev["eventType"] == openlineage._STATES[status]
            spec(ev, ev["schemaURL"])
            for facet in _all_facets(ev):
                spec(facet, facet["_schemaURL"])
        ev = by_job(out)
        # the planted sources and expression did reach the events, cleaned rather than dropped
        assert ev["bronze.orders"]["inputs"][0]["name"].count("<this PC>/") == 6
        assert ev["bronze.customers"]["inputs"][0]["name"].count("[removed]") >= 4
        net = ev["silver.orders"]["outputs"][0]["facets"]["columnLineage"]["fields"]["net_amount"]["inputFields"]
        desc = net[0]["transformations"][0]["description"]
        assert "<this PC>/" in desc and "pwd=[removed] [email]" in desc and f"'{REGEX_LITERALS[1]}'" in desc
        if ws:
            assert out["namespace"] == f"abfss://{GUID_WS}@onelake.dfs.fabric.microsoft.com"


# ---- run_events edge cases

ALL_HAVENT_RUN = {e: "hasn't run in Dev" for e in ("bronze.customers", "bronze.orders", "gold.dim_customer",
                                                   "gold.fact_sales", "silver.customers", "silver.orders")}


def _why(out):
    return {x["entity"]: x["why"] for x in out["left_out"]}


def test_no_runs_leaves_every_entity_out(svc):
    out = events(svc, [])
    assert out["events"] == [] and _why(out) == ALL_HAVENT_RUN and out["env"] == "dev"


def test_runs_for_entities_the_project_does_not_have_are_ignored(svc):
    runs = [{"run_id": "u", "entity": e, "status": "ok", "started_at": "2026-11-14T10:00:00"}
            for e in ("silver.gone", "", None, "gold.FACT_SALES")]
    runs.append({"run_id": "v", "status": "ok", "started_at": "2026-11-14T10:00:00"})  # no entity at all
    out = events(svc, runs)
    assert out["events"] == [] and _why(out) == ALL_HAVENT_RUN


def test_duplicate_run_ids(svc):
    runs = [{"run_id": "same", "entity": "gold.fact_sales", "status": "failed", "started_at": "2026-11-14T09:00:00Z"},
            {"run_id": "same", "entity": "gold.fact_sales", "status": "ok", "started_at": "2026-11-14T10:00:00Z"},
            {"run_id": "same", "entity": "silver.orders", "status": "ok", "started_at": "2026-11-14T08:00:00Z"}]
    for order in (runs, runs[::-1]):
        ev = by_job(events(svc, order))
        assert ev["gold.fact_sales"]["eventType"] == "COMPLETE"  # the later of the two rows, in either order
        # one run id on two entities still gives each job a run of its own
        assert ev["gold.fact_sales"]["run"]["runId"] != ev["silver.orders"]["run"]["runId"]
        assert ev["gold.fact_sales"]["run"]["runId"] == str(uuid.uuid5(openlineage._ID_NAMESPACE, "same/gold.fact_sales"))
    no_id = [{"run_id": None, "entity": "gold.fact_sales", "status": "ok", "started_at": "2026-11-14"}]
    first, again = (by_job(events(svc, no_id))["gold.fact_sales"]["run"]["runId"] for _ in range(2))
    assert first == again and uuid.UUID(first)  # no run id: still a UUID, the same every export


@pytest.mark.parametrize("status,why", [("running", "its last run is running"), ("OK", "its last run is OK"),
                                        ("skipped", "its last run is skipped"), (None, "its last run is not finished"),
                                        ("", "its last run is not finished"), (3, "its last run is 3")])
def test_a_status_other_than_ok_or_failed_is_left_out_with_it(svc, status, why):
    runs = [{"run_id": "a", "entity": "gold.fact_sales", "status": "ok", "started_at": "2026-11-14T09:00:00Z"},
            {"run_id": "b", "entity": "gold.fact_sales", "status": status, "started_at": "2026-11-14T10:00:00Z"}]
    out = events(svc, runs)
    assert out["events"] == []  # the last run is the one that counts, not an earlier finished one
    assert _why(out)["gold.fact_sales"] == why


@pytest.mark.parametrize("seconds", [-5, -0.5, "3", None, "soon", [1], float("nan"), float("inf"), 1e20])
def test_seconds_that_are_not_a_duration_leave_the_event_time_at_the_start(svc, spec, seconds):
    run = {"run_id": "a", "entity": "gold.fact_sales", "status": "ok", "started_at": "2026-11-14T10:00:00+00:00",
           "seconds": seconds}
    ev = by_job(events(svc, [run]))["gold.fact_sales"]
    assert ev["eventTime"] == "2026-11-14T10:00:00+00:00"
    spec(ev, ev["schemaURL"])


def test_a_duration_past_the_end_of_time_does_not_stop_the_export(svc):
    run = {"run_id": "a", "entity": "gold.fact_sales", "status": "ok", "started_at": "9999-12-31T23:59:59+00:00",
           "seconds": 10}
    assert by_job(events(svc, [run]))["gold.fact_sales"]["eventTime"] == "9999-12-31T23:59:59+00:00"


@pytest.mark.parametrize("started,want", [
    ("2026-11-14T10:00:00", "2026-11-14T10:00:02+00:00"),  # naive: read as UTC
    ("2026-11-14T10:00:00Z", "2026-11-14T10:00:02+00:00"),
    ("2026-11-14T10:00:00+05:30", "2026-11-14T10:00:02+05:30"),  # an offset is kept as given
    ("2026-11-14", "2026-11-14T00:00:02+00:00"),
])
def test_naive_and_offset_start_times(svc, spec, started, want):
    run = {"run_id": "a", "entity": "gold.fact_sales", "status": "ok", "started_at": started, "seconds": 2}
    ev = by_job(events(svc, [run]))["gold.fact_sales"]
    assert ev["eventTime"] == want
    spec(ev, ev["schemaURL"])


def test_naive_and_offset_starts_compare_as_times(svc):
    runs = [{"run_id": "naive", "entity": "gold.fact_sales", "status": "ok", "started_at": "2026-11-14T08:30:00"},
            {"run_id": "offset", "entity": "gold.fact_sales", "status": "failed", "started_at": "2026-11-14T09:00:00+01:00"}]
    for order in (runs, runs[::-1]):
        assert by_job(events(svc, order))["gold.fact_sales"]["eventType"] == "COMPLETE"  # 08:30Z is after 08:00Z


@pytest.mark.parametrize("started", [None, "", "soon", "2026-13-40T10:00:00", 12345])
def test_an_unreadable_start_is_left_out_with_why(svc, started):
    out = events(svc, [{"run_id": "a", "entity": "gold.fact_sales", "status": "ok", "started_at": started}])
    assert out["events"] == [] and _why(out)["gold.fact_sales"] == "its last run has no start time"


@pytest.mark.parametrize("value", [
    "jdbc:oracle:thin:@//h:1521/svc?wallet_password=WPWLEAK",  # pragma: allowlist secret (made-up)
    "jdbc:oracle:thin:@h:1521:orcl?oracle.net.wallet_password=WPWLEAK",  # pragma: allowlist secret (made-up)
    "account=acme;private_key_pwd=WPWLEAK;warehouse=wh",  # pragma: allowlist secret (made-up)
    "odbc:snowflake;oauth_token=WPWLEAK",  # pragma: allowlist secret (made-up)
])
def test_secrets_with_a_prefixed_key_or_in_a_driver_query_are_removed(value):
    out = openlineage.safe_origin(value)
    assert "WPWLEAK" not in out, out


def test_a_long_value_is_cleaned_in_linear_time():
    import time
    start = time.perf_counter()
    openlineage.safe_origin("a_" * 5000)  # redact.py has slow patterns of its own on longer text
    assert time.perf_counter() - start < 1.0
