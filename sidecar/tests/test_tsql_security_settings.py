"""WR-217: security statements and session settings in local T-SQL.

- GRANT, DENY, REVOKE, EXECUTE AS, REVERT, row-level security, dynamic data masking, users, roles and logins are
  refused by name. None of them may run as a no-op: a script must never look as if it applied permissions.
- SET options are honoured with a Fabric Warehouse's default values (NOCOUNT, ANSI_NULLS ON, DATEFORMAT mdy,
  LANGUAGE us_english, ...) and refused by name otherwise."""
from __future__ import annotations

import pytest

from fabric_desktop.localdata import LocalData

SECURITY = "isn't applied on this PC, because the local engine has no users, roles or permissions"


@pytest.fixture()
def local(tmp_path):
    store = LocalData(tmp_path / "app" / "local-data")
    yield store
    store.close_all()


@pytest.fixture()
def engine(local):
    e = local.engine(local.create("Security")["id"])
    e.execute("CREATE TABLE dbo.t (id INT, masked INT, [user] VARCHAR(5))", dialect="tsql")
    e.execute("INSERT INTO dbo.t VALUES (1, 2, 'u')", dialect="tsql")
    return e


def run(engine, sql):
    return [list(r) for r in engine.execute(sql, dialect="tsql")["rows"]]


@pytest.mark.parametrize(("sql", "what"), [
    ("GRANT SELECT ON dbo.t TO alice", r"GRANT \(permissions\)"),
    ("GRANT SELECT ON OBJECT::dbo.t TO [alice@contoso.com]", r"GRANT \(permissions\)"),
    ("GRANT UNMASK TO alice", r"GRANT \(permissions\)"),
    ("DENY SELECT ON dbo.t TO bob", r"DENY \(permissions\)"),
    ("REVOKE SELECT ON dbo.t FROM bob", r"REVOKE \(permissions\)"),
    ("EXECUTE AS USER = 'alice'", r"EXECUTE AS \(impersonation\)"),
    ("EXEC AS LOGIN = 'alice'", r"EXEC AS \(impersonation\)"),
    ("REVERT", r"REVERT \(impersonation\)"),
    ("CREATE SECURITY POLICY p ADD FILTER PREDICATE sec.fn(id) ON dbo.t WITH (STATE = ON)",
     r"CREATE SECURITY POLICY \(row-level security\)"),
    ("ALTER SECURITY POLICY p WITH (STATE = OFF)", r"ALTER SECURITY POLICY \(row-level security\)"),
    ("DROP SECURITY POLICY p", r"DROP SECURITY POLICY \(row-level security\)"),
    ("ALTER TABLE dbo.t ALTER COLUMN id ADD MASKED WITH (FUNCTION = 'default()')",
     r"MASKED WITH \(dynamic data masking\)"),
    ("CREATE TABLE dbo.m (s VARCHAR(10) MASKED WITH (FUNCTION = 'email()'))", r"MASKED WITH \(dynamic data masking\)"),
    ("ALTER TABLE dbo.t ALTER COLUMN id DROP MASKED", r"MASKED WITH \(dynamic data masking\)"),
    ("CREATE USER bob WITHOUT LOGIN", "CREATE USER"),
    ("DROP USER bob", "DROP USER"),
    ("CREATE ROLE readers", "CREATE ROLE"),
    ("ALTER ROLE db_datareader ADD MEMBER bob", "ALTER ROLE"),
    ("CREATE LOGIN bob WITH PASSWORD = 'x'", "CREATE LOGIN"),
    ("ALTER AUTHORIZATION ON SCHEMA::dbo TO bob", "ALTER AUTHORIZATION"),
    ("SELECT 1;\nGRANT SELECT ON dbo.t TO alice", r"GRANT \(permissions\)"),
])
def test_security_statements_are_refused_never_skipped(engine, sql, what):
    with pytest.raises(ValueError, match=f"{what} {SECURITY}"):
        engine.execute(sql, dialect="tsql")


def test_nothing_before_a_refused_grant_runs(engine):
    with pytest.raises(ValueError, match="GRANT"):
        engine.execute("INSERT INTO dbo.t VALUES (2, 0, 'v');\nGRANT SELECT ON dbo.t TO alice", dialect="tsql")
    assert run(engine, "SELECT COUNT(*) FROM dbo.t") == [[1]]


def test_the_words_as_names_or_text_are_left_alone(engine):
    assert run(engine, "SELECT masked, [user], 'GRANT SELECT TO x' FROM dbo.t") == [[2, "u", "GRANT SELECT TO x"]]
    assert run(engine, "UPDATE dbo.t SET masked = 3") == [[1]]
    assert run(engine, "SELECT t.masked FROM dbo.t AS t") == [[3]]
    run(engine, "ALTER TABLE dbo.t ADD masked2 INT")
    run(engine, "CREATE TABLE dbo.m (id INT)")
    run(engine, "ALTER TABLE dbo.m ADD masked INT")
    assert run(engine, "SELECT COUNT(masked) FROM dbo.m") == [[0]]


@pytest.mark.parametrize("op", ["+", "-", "*", "/", "%", "|", "&", "^"])
def test_a_compound_assignment_is_never_called_a_session_option(engine, op):
    try:
        engine.execute(f"UPDATE dbo.t SET masked {op}= 1", dialect="tsql")
    except ValueError as exc:
        assert "isn't mapped yet. Remove it" not in str(exc)


@pytest.mark.parametrize("setting", [
    "SET NOCOUNT ON", "SET NOCOUNT OFF", "SET XACT_ABORT ON", "SET XACT_ABORT OFF", "SET ANSI_NULLS ON",
    "SET QUOTED_IDENTIFIER ON", "SET ANSI_WARNINGS ON", "SET ANSI_PADDING ON", "SET ARITHABORT ON",
    "SET CONCAT_NULL_YIELDS_NULL ON", "SET NUMERIC_ROUNDABORT OFF", "SET DATEFORMAT mdy", "SET LANGUAGE us_english",
    "SET LANGUAGE English", "SET LANGUAGE N'us_english'", "SET DATEFIRST 7",
])
def test_a_warehouses_default_settings_are_honoured(engine, setting):
    assert run(engine, f"{setting};\nSELECT id FROM dbo.t") == [[1]]


@pytest.mark.parametrize(("setting", "words"), [
    ("SET ANSI_NULLS OFF", "SET ANSI_NULLS OFF isn't mapped yet: the local engine runs as SET ANSI_NULLS ON"),
    ("SET QUOTED_IDENTIFIER OFF", "SET QUOTED_IDENTIFIER OFF isn't mapped yet"),
    ("SET ANSI_WARNINGS OFF", "SET ANSI_WARNINGS OFF isn't mapped yet"),
    ("SET ANSI_PADDING OFF", "SET ANSI_PADDING OFF isn't mapped yet"),
    ("SET ARITHABORT OFF", "SET ARITHABORT OFF isn't mapped yet"),
    ("SET CONCAT_NULL_YIELDS_NULL OFF", "SET CONCAT_NULL_YIELDS_NULL OFF isn't mapped yet"),
    ("SET NUMERIC_ROUNDABORT ON", "SET NUMERIC_ROUNDABORT ON isn't mapped yet"),
    ("SET DATEFORMAT dmy", "SET DATEFORMAT DMY isn't mapped yet: the local engine runs as SET DATEFORMAT MDY"),
    ("SET LANGUAGE British", "SET LANGUAGE BRITISH isn't mapped yet"),
    ("SET DATEFIRST 1", r"counts weeks from Sunday \(SET DATEFIRST 7"),
    ("SET ROWCOUNT 1", "SET ROWCOUNT isn't mapped yet. Remove it, or run it in a Fabric Warehouse."),
    ("SET STATISTICS IO ON", "SET STATISTICS IO isn't mapped yet"),
    ("SET STATISTICS TIME ON", "SET STATISTICS TIME isn't mapped yet"),
    ("SET SHOWPLAN_XML ON", "SET SHOWPLAN_XML isn't mapped yet"),
    ("SET NOEXEC ON", "SET NOEXEC isn't mapped yet"),
    ("SET FMTONLY ON", "SET FMTONLY isn't mapped yet"),
    ("SET LOCK_TIMEOUT 100", "SET LOCK_TIMEOUT isn't mapped yet"),
    ("SET IMPLICIT_TRANSACTIONS ON", "SET IMPLICIT_TRANSACTIONS isn't mapped yet"),
    ("SET CONTEXT_INFO 0x01", "SET CONTEXT_INFO isn't mapped yet"),
])
def test_other_settings_are_refused_by_name(engine, setting, words):
    with pytest.raises(ValueError, match=words):
        engine.execute(f"{setting};\nSELECT id FROM dbo.t", dialect="tsql")
