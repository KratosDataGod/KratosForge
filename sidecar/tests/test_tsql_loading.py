"""WR-220: loading data. COPY INTO, BULK INSERT and OPENROWSET(BULK ...) read files from a URL (Azure Storage or
OneLake), as Fabric Warehouse documents them. The local engine reads no URLs, so each is refused by its T-SQL name,
whatever its file type, options, error-row settings or column list, and nothing in the script runs: no partial load.
On a warehouse, a read and a change both refuse them by name before anything is sent.
"""

from __future__ import annotations

import pytest

from fabric_desktop import sql_exec, warehouse
from fabric_desktop.localdata import LocalData
from fabric_desktop.localdata.tsql import translate

URL = "https://acct.blob.core.windows.net/sales/2026/*.csv"
LAKE = "https://onelake.dfs.fabric.microsoft.com/ws-id/lh-id/Files/orders.parquet"

COPY_INTO = [
    f"COPY INTO dbo.orders FROM '{URL}'",
    (f"COPY INTO dbo.orders FROM '{URL}' WITH (FILE_TYPE = 'CSV', FIRSTROW = 2, FIELDTERMINATOR = ';', "
     "ROWTERMINATOR = '0x0A', FIELDQUOTE = '\"', ENCODING = 'UTF8', DATEFORMAT = 'dmy')"),
    f"COPY INTO dbo.orders FROM '{LAKE}' WITH (FILE_TYPE = 'PARQUET')",
    "COPY INTO dbo.orders FROM 'https://acct.blob.core.windows.net/c/a.jsonl' WITH (FILE_TYPE = 'JSONL')",
    f"COPY INTO dbo.orders FROM '{URL}' WITH (MAXERRORS = 10, ERRORFILE = 'https://acct.blob.core.windows.net/err')",
    f"COPY INTO dbo.orders (id DEFAULT 0 1, boxes 3) FROM '{URL}' WITH (FILE_TYPE = 'CSV')",
    f"COPY INTO dbo.orders FROM '{URL}' WITH (CREDENTIAL = (IDENTITY = 'Shared Access Signature', SECRET = 'x'))",
    f"COPY INTO dbo.orders FROM '{URL}', 'https://acct.blob.core.windows.net/sales/b.csv'",
    f"COPY INTO dbo.orders FROM '{URL}' WITH (COMPRESSION = 'GZIP', MATCH_COLUMN_COUNT = 'ON', IDENTITY_INSERT = 'OFF')",
    f"copy into orders from N'{URL}'",
    f"SELECT 1 AS a; COPY INTO dbo.orders FROM '{URL}'",
    f"SELECT * FROM dbo.orders\nCOPY INTO dbo.orders FROM '{URL}'",  # no ; between statements, as T-SQL allows
]
BULK_INSERT = [
    f"BULK INSERT dbo.orders FROM '{URL}'",
    f"BULK INSERT dbo.orders FROM '{URL}' WITH (FORMAT = 'CSV', FIRSTROW = 2, FIELDTERMINATOR = ',', MAXERRORS = 0)",
    f"BULK INSERT dbo.orders FROM '{LAKE}' WITH (FORMAT = 'PARQUET')",
    f"bulk insert orders from '{URL}' WITH (ERRORFILE = 'https://acct.blob.core.windows.net/err', TABLOCK)",
]
OPENROWSET = [
    f"SELECT * FROM OPENROWSET(BULK '{LAKE}') AS r",
    f"SELECT * FROM OPENROWSET(BULK '{URL}', FORMAT = 'CSV', HEADER_ROW = TRUE) AS r",
    f"SELECT * FROM OPENROWSET(BULK '{URL}', FORMAT = 'CSV', FIRSTROW = 2) WITH (id INT 1, boxes INT 2) AS r",
    ("SELECT r.filepath(1), r.filename() FROM OPENROWSET(BULK 'https://acct.blob.core.windows.net/c/*/a.jsonl', "
     "FORMAT = 'JSONL') AS r"),
    f"INSERT INTO dbo.orders SELECT id, boxes FROM OPENROWSET(BULK '{LAKE}') AS r",
    f"WITH f AS (SELECT * FROM OPENROWSET(BULK '{LAKE}') AS r) SELECT COUNT(*) FROM f",
    f"SELECT o.id FROM dbo.orders AS o JOIN OPENROWSET(BULK '{LAKE}') AS r ON r.id = o.id",
    "SELECT * FROM OPENROWSET(BULK 'C:/data/orders.csv', FORMAT = 'CSV') AS r",  # a path on this PC too
    f"SELECT * FROM openrowset (BULK '{LAKE}') AS r",
]
LOADS = [("COPY INTO", q) for q in COPY_INTO] + [("BULK INSERT", q) for q in BULK_INSERT] + \
    [("OPENROWSET", q) for q in OPENROWSET]


@pytest.mark.parametrize("word, sql", LOADS)
def test_each_load_is_refused_by_its_name(word, sql):
    with pytest.raises(ValueError) as err:
        translate(sql)
    message = str(err.value)
    assert message.startswith("This T-SQL can't be run on the local engine: " + word + " isn't mapped yet")
    assert "so nothing was loaded" in message and "Fabric Warehouse" in message
    assert "Could not read" not in message and "COPY reads or writes files" not in message


@pytest.mark.parametrize("sql", [
    "SELECT [openrowset], [bulk], copy FROM dbo.orders",
    "SELECT 'COPY INTO dbo.orders FROM ''x''' AS a, N'BULK INSERT t' AS b, 'OPENROWSET(BULK ''u'')' AS c",
    "-- COPY INTO dbo.orders FROM 'x'\n/* BULK INSERT t FROM 'x' */ SELECT 1 AS a",
    "SELECT 1 copy INTO dbo.copied FROM dbo.orders",  # copy as an alias: SELECT ... INTO, not COPY INTO
    "SELECT [COPY INTO] FROM dbo.orders",
])
def test_the_words_as_names_strings_or_comments_are_not_refused(sql):
    translate(sql)


def test_the_local_check_names_the_load_rather_than_duckdbs_copy():
    with pytest.raises(ValueError, match="COPY INTO isn't mapped yet"):
        sql_exec.check_local(f"COPY INTO dbo.orders FROM '{URL}'", "tsql")
    with pytest.raises(ValueError, match="OPENROWSET isn't mapped yet"):
        sql_exec.check_local(f"SELECT * FROM OPENROWSET(BULK '{LAKE}') AS r", "tsql")


@pytest.fixture(scope="module")
def engine(tmp_path_factory):
    local = LocalData(tmp_path_factory.mktemp("app") / "local-data")
    e = local.engine(local.create("TSQL loading")["id"])
    e.execute("CREATE TABLE dbo.orders AS SELECT * FROM (VALUES (1, 2), (2, 2)) v(id, boxes)")
    yield e
    local.close_all()


@pytest.mark.parametrize("load", [
    f"COPY INTO dbo.orders FROM '{URL}' WITH (MAXERRORS = 100)",
    f"BULK INSERT dbo.orders FROM '{URL}'",
    f"INSERT INTO dbo.orders SELECT id, boxes FROM OPENROWSET(BULK '{LAKE}') AS r",
])
def test_a_script_with_a_load_runs_nothing_so_nothing_loads_partly(engine, load):
    script = f"INSERT INTO dbo.orders VALUES (9, 9);\n{load};\nDELETE FROM dbo.orders WHERE id = 1"
    with pytest.raises(Exception, match="isn't mapped yet") as err:
        engine.execute(script, dialect="tsql")
    assert "local engine" in str(err.value)
    rows = engine.execute("SELECT id, boxes FROM dbo.orders ORDER BY id", dialect="tsql")["rows"]
    assert [list(r) for r in rows] == [[1, 2], [2, 2]]


@pytest.mark.parametrize("word, sql", LOADS)
def test_a_warehouse_read_refuses_each_load_by_name(word, sql):
    found = warehouse.verdicts(sql)
    assert found[0].kind == "fabric_mutation"
    assert found[0].reason in (warehouse._LOADS.format(word), f"{word} reads outside the warehouse")


@pytest.mark.parametrize("word, sql", LOADS)
def test_a_warehouse_change_refuses_each_load_by_name(word, sql):
    found = warehouse.verdicts(sql, writes=True)
    refused = [v for v in found if v.kind == "fabric_mutation"]
    assert refused, found
    assert refused[0].reason in (warehouse._LOADS.format(word), f"{word} reads outside the warehouse")


@pytest.mark.parametrize("sql", [
    "SELECT [bulk], 'COPY INTO x' AS c FROM dbo.orders",
    "SELECT [openrowset], [OpenQuery] FROM dbo.orders",
    'SELECT "openrowset" FROM dbo.orders',
    "SELECT 'OPENROWSET(BULK ''u'')' AS a",
])
def test_a_warehouse_read_still_runs_the_words_as_names_or_text(sql):
    found = warehouse.verdicts(sql)
    assert [v.kind for v in found] == ["read_only"]
