"""Versioned schema contracts (stage 8.2): port history, the versions a link accepts, deprecation dates."""

import datetime as dt
import sqlite3
from contextlib import closing

import pytest

from fabric_desktop.central import core
from fabric_desktop.central.client import LocalPlatform
from fabric_desktop.meta import mesh
from fabric_desktop.meta.project import GateError, Project, validate
from fabric_desktop.service import Service

from test_meta import edit_entity, write

TODAY = dt.datetime.now(dt.timezone.utc).date()  # the platform's date is UTC's (UDF_Platform runs in UTC)


@pytest.fixture(autouse=True)
def _same_day(monkeypatch):
    """The platform's and the mesh's clocks stay on TODAY, so a run that crosses midnight UTC can't put setup
    and checks on different days."""
    monkeypatch.setattr(core, "_today", lambda: TODAY)
    monkeypatch.setattr(mesh, "utc_today", lambda: TODAY)
READS_TEST = {"dev": "test", "test": "test", "prod": "prod"}
PORT = [{"port": "customers", "entity": "silver.customers", "table": "customers",
         "columns": {"customer_id": "int64", "email": "string"}}]


@pytest.fixture
def platform(tmp_path):
    return LocalPlatform(tmp_path / "platform.sqlite", lambda: "chris@example.com")


def _product(**kw):
    return {"project": "crm", "name": "customers", "access": "public", "status": "published", "version": "1"} | kw


@pytest.fixture
def svc(tmp_path):
    s = Service(root=tmp_path / "app")
    s.meta_init(str(tmp_path / "crm"), "3")
    s.mesh_save_product({"name": "customers", "access": "public", "status": "published", "version": "1",
                         "ports": [{"name": "customers", "entity": "silver.customers"}]})
    cols = s._project().entities["silver.customers"].columns
    # As the last promote to Test left it in the catalog.
    s._catalog().call("register_product", product=_product(),
                      ports=[{"port": "customers", "entity": "silver.customers", "table": "customers", "columns": cols}],
                      env="test")
    return s


def _link(svc, consumer="finance", alias="customers", **kw):
    svc._catalog().call("register_link", consumer=consumer, alias=alias, product_id="crm/customers", port="customers",
                        reads=READS_TEST, **kw)


def _version(svc, version, **kw):
    svc.mesh_save_product({"name": "customers", "access": "public", "status": "published", "version": version,
                           "ports": [{"name": "customers", "entity": "silver.customers"}]} | kw)


def _drop_email(svc):
    edit_entity(svc._project().root, "silver.customers", 'email = "string"\n', "")
    svc._project().reload()


def _breaking(svc, today=TODAY):
    return mesh.breaking_changes(svc._project(), svc._catalog().call("list_products"), "test", today)


# --------------------------------------------------------------------------- port history


def test_history_gets_a_row_only_when_the_version_or_columns_change(platform):
    def publish(env="prod", version="1", columns=None):
        ports = [dict(PORT[0], columns=columns or PORT[0]["columns"])]
        return platform.call("register_product", product=_product(version=version), ports=ports, env=env)

    assert publish()["history"] == ["customers"]
    assert publish()["history"] == []  # the same again: nothing new
    assert publish(columns={"customer_id": "int64"})["history"] == ["customers"]
    assert publish(version="2", columns={"customer_id": "int64"})["history"] == ["customers"]
    assert publish(env="test")["history"] == ["customers"]  # each environment has its own history
    rows = platform.call("product_history", product_id="crm/customers")
    assert [(r["env"], r["version"], sorted(r["columns"])) for r in rows] == [
        ("test", "1", ["customer_id", "email"]), ("prod", "2", ["customer_id"]), ("prod", "1", ["customer_id"]),
        ("prod", "1", ["customer_id", "email"])]
    assert {r["published_by"] for r in rows} == {"chris@example.com"} and rows[0]["port"] == "customers"
    assert platform.call("product_history", product_id="crm/nothing") == []


# --------------------------------------------------------------------------- accepts


def test_accepts_is_one_version_a_range_or_a_version_and_later():
    assert mesh.parse_accepts("2") == (2, 2) and mesh.parse_accepts("2-3") == (2, 3)
    assert mesh.parse_accepts("2+") == (2, None) and mesh.parse_accepts(2) == (2, 2)
    assert [mesh.accepts_version("2-3", v) for v in ("1", "2", "3", "4")] == [False, True, True, False]
    assert mesh.accepts_version("2+", "40") and not mesh.accepts_version("2", "3")
    assert mesh.accepts_version("", "9") and mesh.accepts_version(None, "9")  # no accepts: any version, as before
    assert not mesh.accepts_version("2+", "2.1")  # not a whole number: can't be checked, so not accepted
    for bad, words in (("two", "a version"), ("2-", "a version"), ("3-2", "runs backwards"), (True, "a version")):
        with pytest.raises(ValueError, match=words):
            mesh.parse_accepts(bad)


def test_bad_accepts_fails_the_save_gate(svc):
    root = svc._project().root
    write(root, "metadata/links/a.toml", 'alias = "a"\nproduct = "sales/orders"\nport = "orders"\naccepts = "2..3"\n')
    write(root, "metadata/links/b.toml", 'alias = "b"\nproduct = "sales/orders"\nport = "orders"\naccepts = "4-2"\n')
    write(root, "metadata/links/c.toml", 'alias = "c"\nproduct = "sales/orders"\nport = "orders"\naccepts = "2+"\n')
    errors = validate(Project(root))
    assert any(e.startswith("links/a: accepts is a version") for e in errors)
    assert any(e.startswith("links/b: accepts '4-2' runs backwards") for e in errors)
    assert not any(e.startswith("links/c") for e in errors)
    with pytest.raises(GateError, match="accepts is a version"):
        svc.mesh_save_link({"alias": "d", "product": "crm/customers", "port": "customers", "accepts": "latest"})


def test_a_new_link_accepts_the_products_current_version(svc):
    svc._catalog().call("register_product", product=_product(project="sales", name="orders", version="3"),
                        ports=[dict(PORT[0], port="orders")], env="prod")
    svc.mesh_save_link({"alias": "orders", "product": "sales/orders", "port": "orders",
                        "columns": {"customer_id": "int64"}})
    link = svc._project().links["orders"]
    assert link.accepts == "3" and 'accepts = "3"' in link.path.read_text(encoding="utf-8")
    stored = next(p for p in svc._catalog().call("list_products") if p["id"] == "sales/orders")["consumers"][0]
    assert stored["accepts"] == "3" and stored["columns"] == {"customer_id": "int64"}
    # Given by the consumer, it's kept as given.
    svc.mesh_save_link({"alias": "orders_any", "product": "sales/orders", "port": "orders", "accepts": "2+"})
    assert svc._project().links["orders_any"].accepts == "2+"
    # An older link saved again without accepts stays without: it behaves as links did before.
    write(svc._project().root, "metadata/links/old.toml", 'alias = "old"\nproduct = "sales/orders"\nport = "orders"\n')
    svc._project().reload()
    svc.mesh_save_link({"alias": "old", "product": "sales/orders", "port": "orders"})
    assert svc._project().links["old"].accepts == ""



def _stored(svc, alias):
    return next(c for p in svc._catalog().call("list_products") if p["id"] == "sales/orders"
                for c in p["consumers"] if c["alias"] == alias)


def test_saving_a_link_again_from_new_link_keeps_its_accepts_and_columns(svc):
    svc._catalog().call("register_product", product=_product(project="sales", name="orders", version="3"),
                        ports=[dict(PORT[0], port="orders")], env="prod")
    svc.mesh_save_link({"alias": "orders", "product": "sales/orders", "port": "orders", "accepts": "3-4",
                        "columns": {"customer_id": "int64"}})
    # The New link dialog sends neither accepts nor columns.
    svc.mesh_save_link({"alias": "orders", "product": "sales/orders", "port": "orders"})
    assert svc._project().links["orders"].accepts == "3-4"
    assert _stored(svc, "orders")["accepts"] == "3-4" and _stored(svc, "orders")["columns"] == {"customer_id": "int64"}
    # Sent on purpose as "", it reads any version.
    svc.mesh_save_link({"alias": "orders", "product": "sales/orders", "port": "orders", "accepts": ""})
    assert svc._project().links["orders"].accepts == "" and _stored(svc, "orders")["accepts"] == ""


def test_accepts_widened_in_the_link_file_reaches_the_catalog_on_publish(svc):
    svc._catalog().call("register_product", product=_product(project="sales", name="orders", version="3"),
                        ports=[dict(PORT[0], port="orders")], env="prod")
    svc.mesh_save_link({"alias": "orders", "product": "sales/orders", "port": "orders"})
    path = svc._project().links["orders"].path
    path.write_text(path.read_text(encoding="utf-8").replace('accepts = "3"', 'accepts = "3-4"'), encoding="utf-8")
    svc._project().reload()
    assert _stored(svc, "orders")["accepts"] == "3", "editing the file alone doesn't reach the catalog"
    _version(svc, "1", access="private")  # private: publishing needs no catalog text
    links = lambda: len([a for a in svc._catalog().call("audit") if a["action"] == "link"])  # noqa: E731
    out = svc.mesh_publish("dev")
    assert _stored(svc, "orders")["accepts"] == "3-4" and not out.get("problems")
    once = links()
    svc.mesh_publish("dev")
    assert links() == once, "a second publish finds nothing different and sends nothing"


def test_a_link_the_catalog_refuses_on_publish_is_a_problem_not_a_failed_publish(svc):
    svc._catalog().call("register_product", product=_product(project="sales", name="orders", access="private"),
                        ports=[dict(PORT[0], port="orders")], env="prod")
    write(svc._project().root, "metadata/links/orders.toml", 'alias = "orders"\nproduct = "sales/orders"\nport = "orders"\n')
    svc._project().reload()
    _version(svc, "1", access="private")
    out = svc.mesh_publish("dev")
    assert out["published"] == ["customers"]
    assert any("links/orders isn't in the catalog as its file says" in p and "private" in p for p in out["problems"])


# --------------------------------------------------------------------------- the promote check


def test_a_new_breaking_version_is_blocked_by_a_link_that_doesnt_accept_it(svc):
    _link(svc, accepts="1")
    _drop_email(svc)
    _version(svc, "2")
    errors, notes = _breaking(svc)
    assert errors == ["products/customers: port customers drops email, and 1 other project reads it in test (finance). "
                      "Version 2 is outside what 1 link accepts: finance (link customers accepts 1). Its project can "
                      "widen accepts in the link's file (for example accepts = \"1-2\") and promote, once it reads the new version, or "
                      "keep the column"]
    _link(svc, accepts="1-2")  # widened by its project
    errors, notes = _breaking(svc)
    assert errors == [] and notes[0].endswith("publishing it as version 2")
    _version(svc, "1")  # the same version again: the drop blocks, as before versions were declared
    with pytest.raises(GateError, match="drops email"):
        svc.meta_promote("dev", "test")


def test_a_link_without_accepts_keeps_todays_note(svc):
    _link(svc)
    _drop_email(svc)
    _version(svc, "2")
    assert _breaking(svc) == ([], ["products/customers: port customers drops email, and 1 other project reads it in "
                                   "test (finance); publishing it as version 2"])


def test_a_passed_deprecation_date_turns_the_block_into_a_note(svc):
    _link(svc, accepts="1")
    stops = TODAY - dt.timedelta(days=1)
    svc._catalog().call("register_product", product=_product(status="deprecated", deprecated_on=stops.isoformat()),
                        ports=[], env="test")
    _drop_email(svc)
    _version(svc, "2")
    errors, notes = _breaking(svc)
    assert errors == [] and f"deprecated on {stops.isoformat()}, so its reader had notice" in notes[0]
    # A date still ahead: the readers haven't had their notice yet.
    errors, _ = _breaking(svc, today=stops - dt.timedelta(days=1))
    assert errors and "outside what 1 link accepts" in errors[0]


def test_a_reader_that_listed_its_columns_isnt_broken_by_another_column(svc):
    _link(svc, columns={"customer_id": "int64"})
    _drop_email(svc)
    assert _breaking(svc) == ([], ["products/customers: port customers drops email, which none of the 1 other "
                                   "project reading it in test relies on (finance)"])
    _link(svc, consumer="billing", columns={"email": "string"})  # this one relies on it
    errors, _ = _breaking(svc)
    assert errors and "1 other project reads it in test (billing)" in errors[0]


# --------------------------------------------------------------------------- deprecation


def test_a_new_link_is_refused_once_the_deprecation_date_comes(platform):
    platform.call("register_product", product=_product(status="deprecated", deprecated_on=TODAY.isoformat(),
                                                       owner="ana@example.com"), ports=PORT, env="prod")
    with pytest.raises(core.PlatformError, match=f"deprecated on {TODAY.isoformat()} and takes no new links. "
                                                 r"Ask its owner \(ana@example.com\)"):
        platform.call("register_link", consumer="finance", alias="c", product_id="crm/customers", port="customers",
                      reads=READS_TEST)
    with pytest.raises(core.PlatformError, match="accepts is a version"):
        platform.call("register_link", consumer="finance", alias="c", product_id="crm/customers", port="customers",
                      reads=READS_TEST, accepts="v2")
    ahead = (TODAY + dt.timedelta(days=30)).isoformat()
    platform.call("register_product", product=_product(status="deprecated", deprecated_on=ahead), ports=PORT, env="prod")
    platform.call("register_link", consumer="finance", alias="c", product_id="crm/customers", port="customers",
                  reads=READS_TEST)  # the date is still ahead
    platform.call("register_product", product=_product(status="deprecated", deprecated_on=TODAY.isoformat()),
                  ports=PORT, env="prod")
    platform.call("register_link", consumer="finance", alias="c", product_id="crm/customers", port="customers",
                  reads={"dev": "prod"})  # a link already there can still be saved; only new ones are refused


def test_a_link_to_a_product_past_its_deprecation_date_blocks_the_consumers_promote(svc):
    def deprecate(days):
        svc._catalog().call("register_product", product=_product(project="sales", name="orders", status="deprecated",
                                                                  deprecated_on=(TODAY + dt.timedelta(days=days)).isoformat()),
                            ports=[dict(PORT[0], port="orders")], env="prod")

    deprecate(10)
    svc.mesh_save_link({"alias": "orders", "product": "sales/orders", "port": "orders"})
    ahead = (TODAY + dt.timedelta(days=10)).isoformat()
    check = svc.meta_promote_check("dev", "test")
    assert not [e for e in check["errors"] if e.startswith("links/")]
    assert f"links/orders: sales/orders is deprecated and stops on {ahead}; move the link to what replaces it " \
           "before then" in check["notes"]
    deprecate(-1)
    gone = (TODAY - dt.timedelta(days=1)).isoformat()
    check = svc.meta_promote_check("dev", "test")
    assert f"links/orders: sales/orders was deprecated on {gone} and its owner no longer supports it. Move the link " \
           "to what replaces it, or remove it, before promoting" in check["errors"]
    with pytest.raises(GateError, match="links/orders: sales/orders was deprecated"):
        svc.meta_promote("dev", "test")


def test_deprecating_a_product_tells_its_readers_once_and_a_failed_notice_doesnt_fail_the_publish(svc, monkeypatch):
    for env in ("dev", "test"):
        svc._catalog().call("register_product", product=_product(), ports=[], env=env)
    svc._catalog().call("register_link", consumer="finance", alias="crm_customers", product_id="crm/customers",
                        port="customers", reads={"dev": "dev"})
    sent = []
    monkeypatch.setattr(svc, "platform_notify", lambda events: sent.append(events) or {"sent": len(events)})
    stops = (TODAY + dt.timedelta(days=60)).isoformat()
    _version(svc, "1", access="private", status="deprecated", deprecated_on=stops)
    out = svc.mesh_publish("dev")
    assert out["published"] == ["customers"] and "problems" not in out
    assert out["told"] == [{"product": "crm/customers", "project": "finance", "alias": "crm_customers",
                            "deprecated_on": stops}]
    [[event]] = sent
    assert event["kind"] == "product.deprecated" and event["dedupe"] == f"product.deprecated:crm/customers:{stops}"
    assert event["to"]["roles"] == ["project:finance"] and event["open"]["tab"] == "links"
    assert "finance (crm_customers)" in event["detail"]
    svc.mesh_publish("dev")  # deprecated already, with that date: nobody is told again
    assert len(sent) == 1
    later = (TODAY + dt.timedelta(days=90)).isoformat()
    _version(svc, "1", access="private", status="deprecated", deprecated_on=later)

    def down(events):
        raise ConnectionError("Teams unreachable")
    monkeypatch.setattr(svc, "platform_notify", down)
    out = svc.mesh_publish("dev")  # a new date is news again; the notice fails, the publish stands
    assert out["published"] == ["customers"] and out["told"][0]["deprecated_on"] == later
    assert out["problems"] == ["The Teams notice wasn't sent: Teams unreachable"]
    assert next(p for p in svc._catalog().call("list_products") if p["id"] == "crm/customers")["deprecated_on"] == later


def test_a_prod_deprecation_is_told_once_not_again_as_a_product_change(svc, monkeypatch):
    svc.meta_set_environment("prod", target="local")
    monkeypatch.setattr(svc, "_user", lambda: "chris@example.com")
    svc._local_platforms = {}  # the catalog asks who's signed in again
    svc._catalog().call("register_link", consumer="finance", alias="crm_customers", product_id="crm/customers",
                        port="customers", reads={"prod": "prod"})
    svc._catalog().call("add_admin", user="ops@example.com")  # ops publishes a product they don't own
    monkeypatch.setattr(svc, "_user", lambda: "bo@example.com")  # follows, with no link of their own
    svc._local_platforms = {}
    svc.platform_follow("crm/customers")
    monkeypatch.setattr(svc, "_user", lambda: "ops@example.com")  # promotes; the promoter isn't told
    svc._local_platforms = {}
    sent = []
    monkeypatch.setattr(svc, "platform_notify", lambda events: sent.extend(events) or {"sent": len(events)})
    _version(svc, "1", access="private")
    svc.mesh_publish("prod")  # Prod's first publish: nothing to compare with yet
    sent.clear()
    stops = (TODAY + dt.timedelta(days=60)).isoformat()
    _version(svc, "2", access="private", status="deprecated", deprecated_on=stops)
    out = svc.mesh_publish("prod")
    assert out["told"][0]["project"] == "finance"
    [deprecated] = [e for e in sent if e["kind"] == "product.deprecated"]
    assert deprecated["to"]["people"] == ["chris@example.com"]
    changed = {tuple(e["to"]["people"]): e for e in sent if e["kind"] == "product.changed"}
    assert changed[("bo@example.com",)]["detail"] == f"Deprecated, stops {stops}; Version 1 to 2", "a follower hears it"
    assert changed[("chris@example.com",)]["detail"] == "Version 1 to 2", "the deprecation isn't posted to them twice"
    assert len({e["dedupe"] for e in sent}) == len(sent) == 3


def test_the_deprecation_event_fits_the_platform_notice_shape():
    from fabric_desktop import governance_notices as notices
    from fabric_desktop.workloads.platform_notify import message, validate as check
    event = notices.product_deprecated({"id": "crm/customers", "project": "crm", "deprecated_on": "2026-12-31",
                                        "domain_id": "sales"},
                                       [{"consumer_project": "finance", "alias": "c", "updated_by": "Ana@Example.com"},
                                        {"consumer_project": "crm", "alias": "self"}])
    title, lines = message(check(event))
    assert title == "Data product deprecated: crm/customers stops on 2026-12-31"
    assert lines[0] == "For ana@example.com, the team of project finance." and "Data mesh > Products and links > Links" in lines[-1]
    assert notices.product_deprecated({"id": "crm/customers", "project": "crm"}, [{"consumer_project": "crm"}]) is None


# --------------------------------------------------------------------------- a platform not yet repaired


def test_a_platform_made_before_versioned_contracts_still_works(tmp_path):
    path = tmp_path / "old.sqlite"
    platform = LocalPlatform(path, lambda: "chris@example.com")
    with closing(sqlite3.connect(path)) as conn, conn:  # catalog.link and no port history, as a platform has them before Finish or repair
        conn.execute('DROP TABLE "catalog__port_version"')
        conn.execute('ALTER TABLE "catalog__link" DROP COLUMN accepts')
        conn.execute('ALTER TABLE "catalog__link" DROP COLUMN columns')
    platform.call("register_project", project={"name": "finance"})
    platform.call("register_product", product=_product(), ports=PORT, env="prod")
    with platform._db() as db:  # publishing makes the history table on first use
        assert db.exists("catalog", "port_version")
        db.run('DROP TABLE "catalog__port_version"')
        db.commit()
    assert platform.call("product_history", product_id="crm/customers") == []
    with closing(sqlite3.connect(path)) as conn, conn:
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    assert "catalog__port_version" not in tables, "a read made a table"
    products = platform.call("list_products")
    assert products[0]["consumers"] == []
    platform.call("register_link", consumer="finance", alias="c", product_id="crm/customers", port="customers",
                  reads=READS_TEST, accepts="1+")
    with closing(sqlite3.connect(path)) as conn, conn:
        cols = {r[1] for r in conn.execute('PRAGMA table_info("catalog__link")')}
    assert {"accepts", "columns"} <= cols
    assert platform.call("list_products")[0]["consumers"][0]["accepts"] == "1+"


class _OlderFunction:
    """UDF_Platform before versioned contracts, as the app sees it: register_link refuses arguments it
    doesn't know, and register_product answers without a history."""

    def __init__(self, inner):
        self.inner, self.calls = inner, []

    def call(self, op, **kw):
        self.calls.append((op, sorted(kw)))
        if op == "register_link" and ({"accepts", "columns"} & set(kw)):
            raise core.PlatformError("The platform couldn't register link: unexpected keyword argument 'accepts'")
        out = self.inner.call(op, **kw)
        if op == "register_product":
            out = {k: v for k, v in out.items() if k != "history"}
        return out


def test_an_older_function_still_publishes_and_links_and_says_to_repair(svc, monkeypatch):
    older = _OlderFunction(svc._catalog())
    monkeypatch.setattr(svc, "_catalog", lambda: older)
    out = svc.mesh_save_link({"alias": "cust", "product": "crm/customers", "port": "customers", "accepts": "1"})
    assert any("Finish or repair" in p and "versions it accepts" in p for p in out["problems"])
    assert ("register_link", ["alias", "consumer", "port", "product_id", "reads"]) in older.calls
    assert svc._project().links["cust"].accepts == "1"  # the file still says it; the catalog gets it after repair
    _version(svc, "1", access="private")
    out = svc.mesh_publish("dev")
    assert out["published"] == ["customers"] and "Finish or repair" in out["problems"][0]
    # A real refusal is still a refusal.
    with pytest.raises(core.PlatformError, match="isn't a published data product"):
        svc.mesh_save_link({"alias": "nope", "product": "crm/nothing", "port": "x", "accepts": "1"})


def test_only_an_unknown_argument_is_retried_without_accepts(svc, monkeypatch):
    calls = []

    class Refusing:
        def call(self, op, **kw):
            calls.append(op)
            raise core.PlatformError("The platform couldn't register link: crm/customers was deprecated on "
                                     "2026-01-01 and takes no new links.")

    monkeypatch.setattr(svc, "_catalog", lambda: Refusing())
    with pytest.raises(core.PlatformError, match="takes no new links"):
        svc.mesh_save_link({"alias": "late", "product": "crm/customers", "port": "customers", "accepts": "1"})
    assert calls == ["register_link"]  # the catalog's refusal stands: no second call without accepts
    assert "late" not in svc._project().links


def test_two_publishes_in_the_same_microsecond_keep_both_history_rows(platform, monkeypatch):
    import datetime as real
    from types import SimpleNamespace
    fixed = real.datetime(2026, 10, 25, 12, 0, 0, 123456, tzinfo=real.timezone.utc)
    monkeypatch.setattr(core, "dt", SimpleNamespace(datetime=SimpleNamespace(now=lambda tz=None: fixed),
                                                    timezone=real.timezone, date=real.date))
    with platform._db() as db:  # as two PCs publishing at once would: the same port, the same instant
        core.ensure_contracts(db)
        assert core._record_port_version(db, "p/x", "main", "dev", "1", {"a": "int"}, "ada")
        assert core._record_port_version(db, "p/x", "main", "dev", "2", {"a": "int"}, "linus")
        rows = core._port_versions(db, "p/x")
    assert len({r["id"] for r in rows}) == 2 and sorted(r["version"] for r in rows) == ["1", "2"]


def test_a_deprecated_product_without_a_date_says_none_is_set(tmp_path):
    from types import SimpleNamespace
    from fabric_desktop.meta import mesh as m
    link = m.Link("cust", {"product": "crm/customers", "port": "customers"}, tmp_path / "cust.toml")
    project = SimpleNamespace(links={"cust": link})
    _, notes = m.deprecated_links(project, [{"id": "crm/customers", "status": "deprecated", "deprecated_on": ""}])
    assert notes == ["links/cust: crm/customers is deprecated, with no stop date set yet; ask its owner what replaces it"]
    _, notes = m.deprecated_links(project, [{"id": "crm/customers", "status": "deprecated", "deprecated_on": "soon"}])
    assert "its stop date soon can't be read" in notes[0]


def _deprecate_with_a_reader(svc, monkeypatch):
    svc._catalog().call("register_product", product=_product(), ports=[], env="dev")
    svc._catalog().call("register_link", consumer="finance", alias="crm_customers", product_id="crm/customers",
                        port="customers", reads={"dev": "dev"})
    sent = []
    monkeypatch.setattr(svc, "platform_notify", lambda events: sent.extend(events) or {"sent": len(events)})
    _version(svc, "1", access="private", status="deprecated", deprecated_on=(TODAY + dt.timedelta(days=60)).isoformat())
    return sent


def test_a_notice_that_cant_be_made_doesnt_fail_the_publish(svc, monkeypatch):
    from fabric_desktop import governance_notices as notices
    sent = _deprecate_with_a_reader(svc, monkeypatch)

    def broken(product, consumers):
        raise KeyError("id")
    monkeypatch.setattr(notices, "product_deprecated", broken)
    out = svc.mesh_publish("dev")
    assert out["published"] == ["customers"] and sent == []
    assert out["problems"] == ["products/customers is published, but its Teams notice couldn't be made: KeyError: 'id'"]


def test_notices_for_products_already_published_still_go_when_a_later_one_fails(svc, monkeypatch):
    sent = _deprecate_with_a_reader(svc, monkeypatch)
    svc.mesh_save_product({"name": "zz_orders", "access": "private", "status": "published", "version": "1",
                           "ports": [{"name": "orders", "entity": "silver.customers"}]})
    catalog = svc._catalog()
    real = catalog.call

    def call(method, **kw):
        if method == "register_product" and kw["product"]["name"] == "zz_orders":
            raise core.PlatformError("register_product failed")
        return real(method, **kw)
    monkeypatch.setattr(catalog, "call", call)
    monkeypatch.setattr(svc, "_catalog", lambda: catalog)
    assert list(svc._project().products) == ["customers", "zz_orders"]
    with pytest.raises(core.PlatformError, match="register_product failed"):
        svc.mesh_publish("dev")
    # customers is in the catalog as deprecated now, so the next publish wouldn't tell its reader again.
    assert [e["kind"] for e in sent] == ["product.deprecated"]
