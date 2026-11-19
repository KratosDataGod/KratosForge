"""SQL compatibility rules (sqlcompat/rules.py) that the uncapped mutation run (batch 171,
docs/quality/mutation-testing.md) showed no test checked: the exact text each finding points at, words
read as names or as keywords, statements that end early, and the details a finding's title is built from.
Some statements are not valid SQL on purpose: the rules must read them without failing."""

import pytest

from fabric_desktop.sqlcompat import analyze


def _found(sql, target):
    """(rule, the SQL text the finding points at) for each finding, in report order."""
    return [(d["rule"], sql[d["from"]:d["to"]]) for d in analyze(sql, target)["diagnostics"]]


CASES = [
    # ALTER DATABASE is a write; CURRENT_TIMESTAMP without () is still the clock
    ('spark', 'ALTER DATABASE [Sales snapshot] SET TIMESTAMP = CURRENT_TIMESTAMP',
     [('LOC-001', 'ALTER'), ('LOC-004', 'CURRENT_TIMESTAMP')]),
    # a GO line's count belongs to the separator; a call with no arguments, a ) with no (, a [ after it
    ('spark', 'GO 5 -- again', [('SYN-007', '5')]),
    ('spark', 'SELECT date_format() FROM t', [('SYN-007', ')')]),
    ('spark', 'SELECT a) [b] FROM t', [('SYN-007', ')')]),
    # a MERGE inside BEGIN ... END is still read as a statement
    ('warehouse', 'BEGIN MERGE INTO t USING s ON t.a = s.a WHEN MATCHED THEN DELETE; END', [('DML-006', 'BEGIN')]),
    # a name ending in _on followed by ( is a table-valued function, not an unknown call
    ('spark', "SELECT id FROM dbo.orders_on('2026-11-01') AS o", []),
    # every date function with a Java-style pattern, and the same functions without one
    ('spark', ("SELECT date_format(d, 'yyyy'), to_date(s, 'yyyy'), to_timestamp(s, 'yyyy'), from_unixtime(n, 'yyyy'), "
               "unix_timestamp(s, 'yyyy'), to_unix_timestamp(s, 'yyyy'), try_to_timestamp(s, 'yyyy') FROM t"),
     [('LOC-003', 'date_format'), ('LOC-003', 'to_date'), ('LOC-003', 'to_timestamp'), ('LOC-003', 'from_unixtime'),
      ('LOC-003', 'unix_timestamp'), ('LOC-003', 'to_unix_timestamp'), ('LOC-003', 'try_to_timestamp')]),
    ('spark', 'SELECT date_format(d), to_date(s) FROM t', [('SYN-007', ')')]),
    # CREATE STATISTICS on a bracketed name; MASKED WITH (...); brackets next to each other and after TOP (...)
    ('duckdb', 'CREATE STATISTICS [st 2] ON [dbo].[t] ([s]) WITH SAMPLE 50 PERCENT',
     [('LOC-008', 'CREATE'), ('SYN-003', '[t]')]),
    ('warehouse', "CREATE TABLE dbo.m (s VARCHAR(10) MASKED WITH (FUNCTION = 'email()'))", [('SYN-007', 'MASKED')]),
    ('spark', '[a] [b]', [('SYN-003', '[a]')]),
    ('spark', 'SELECT TOP (abs(5)) [a] FROM t', [('SYN-002', 'TOP'), ('SYN-003', '[a]')]),
    # GO on its own line, and with a count, after a statement
    ('spark', 'SELECT 1;\nGO', [('SYN-005', 'GO')]),
    ('spark', 'SELECT 1;\nGO 5', [('SYN-007', '5')]),
    # CLUSTERED VIEW isn't a view; / in a bracketed name
    ('warehouse', 'CREATE CLUSTERED VIEW v AS SELECT 1', [('SYN-007', 'VIEW')]),
    ('warehouse', 'DROP TABLE [a/b]', [('DES-001', 'DROP TABLE')]),
    ('warehouse', 'CREATE TABLE [a/b] (x int)', [('DDL-008', '[a/b]')]),
    # BEGIN TRAN with a name; an endpoint UPDATE points at UPDATE t
    ('warehouse', 'BEGIN TRAN t1', [('TRX-002', 'BEGIN TRAN t1'), ('TRX-004', 'BEGIN TRAN')]),
    ('endpoint', 'UPDATE t SET a = 1', [('EP-001', 'UPDATE t'), ('DES-003', 'UPDATE')]),
    # statements inside IF ... ELSE and BEGIN ... END
    ('spark', 'IF @a = 1 SELECT 1 ELSE MERGE', [('SYN-004', '@a'), ('LOC-001', 'MERGE')]),
    ('spark', 'IF 1 = 1 BEGIN UPDATE t SET a = 1 END', [('DES-003', 'UPDATE'), ('LOC-001', 'UPDATE')]),
    # each call is reported once; (money) in a CTAS is a column, not a type
    ('spark', 'SELECT strlen(a), strlen(b) FROM t', [('FN-001', 'strlen'), ('FN-001', 'strlen')]),
    ('warehouse', 'CREATE TABLE t AS SELECT (money) AS m FROM s', []),
    # a column named of is a fine name; RENAME COLUMN with quoted names
    ('sqldb', 'CREATE TABLE dbo.c (clone INT, [of] INT)', []),
    ('warehouse', 'ALTER TABLE "dbo"."t" RENAME COLUMN "a" TO "b"', [('DDL-007', 'RENAME')]),
    # DECIMAL with no division or product; WITH ... DELETE; UPDATE ... WITH (hint)
    ('spark', 'CREATE TABLE d (n DECIMAL(4, 1))', [('LOC-001', 'CREATE')]),
    ('spark', 'WITH a AS (SELECT * FROM t WHERE id = 3) DELETE FROM a', [('DES-003', 'DELETE'), ('LOC-001', 'DELETE')]),
    ('spark', "UPDATE t WITH (TABLOCK) SET s = 'z' WHERE id = 2", [('LOC-001', 'UPDATE')]),
    # only the first comparison with a string is told about case
    ('warehouse', "SELECT * FROM t WHERE a = 'X' AND b = 'Y'", [('SEM-002', "= 'X'")]),
    # the body after AS in a procedure is a statement; db..t keeps three parts
    ('spark', 'CREATE PROCEDURE p AS UPDATE t SET a = 1',
     [('LOC-001', 'CREATE'), ('DES-003', 'UPDATE'), ('LOC-001', 'UPDATE')]),
    ('warehouse', 'SELECT * FROM a.b.. WHERE 1 = 1', [('DML-007', 'a.b.'), ('SYN-007', 'WHERE')]),
    # DuckDB reads SELECT [a] as a list; CURRENT_DATE in parentheses; RLIKE at the very end
    ('duckdb', 'SELECT [a] FROM t', [('LOC-008', 'SELECT')]),
    ('spark', 'SELECT (CURRENT_DATE)', [('LOC-004', 'CURRENT_DATE')]),
    ('spark', 'SELECT * FROM t WHERE a RLIKE', [('LOC-002', 'RLIKE'), ('SYN-007', 'RLIKE')]),
    # INTO inside parentheses doesn't make a SELECT INTO
    ('warehouse', 'SELECT CAST(a AS money) FROM t WHERE b IN (INTO)', [('SYN-007', 'INTO')]),
    # each key's own NOT ENFORCED counts, and NOT NULL isn't NOT ENFORCED
    ('warehouse', 'CREATE TABLE t (a int PRIMARY KEY, b int UNIQUE NONCLUSTERED NOT ENFORCED)',
     [('DDL-004', 'PRIMARY KEY')]),
    ('warehouse', 'CREATE TABLE t (a int PRIMARY KEY NONCLUSTERED NOT NULL)', [('DDL-004', 'PRIMARY KEY')]),
    # NOT as the last word of ALTER TABLE ADD; a self-referencing MATERIALIZED CTE
    ('spark', 'ALTER TABLE t ADD a int NOT', [('LOC-001', 'ALTER')]),
    ('spark', 'WITH c AS MATERIALIZED (SELECT * FROM c) SELECT * FROM c', [('DML-002', 'c')]),
    # BEGIN TRAN followed by a statement or a number isn't a named transaction
    ('warehouse', 'BEGIN TRAN SELECT 1', [('TRX-004', 'BEGIN TRAN'), ('SYN-007', 'SELECT')]),
    ('warehouse', 'BEGIN TRAN 5', [('TRX-004', 'BEGIN TRAN')]),
    # a view named temp isn't a temporary view; * after SELECT isn't a product; a hint's INDEX(...) call
    ('spark', 'DROP VIEW temp', [('DES-001', 'DROP VIEW'), ('LOC-001', 'DROP')]),
    ('spark', 'SELECT *, CAST(a AS DECIMAL(5, 1)) FROM t', []),
    ('spark', 'SELECT * FROM t WITH (INDEX(ix))', [('FN-001', 'INDEX')]),
    # TOP as the first word; GROUP BY ALL marked whole; calls in parentheses keep their arguments
    ('spark', 'TOP (5) [a]', [('FN-001', 'TOP'), ('SYN-003', '[a]')]),
    ('sqldb', 'SELECT a, count(*) FROM t GROUP BY ALL', [('SYN-010', 'GROUP BY ALL')]),
    ('spark', 'SELECT (ISNULL(a, b)) FROM t', [('FN-002', 'ISNULL')]),
    ('spark', "SELECT (split(s, '.')) FROM t", [('LOC-002', 'split')]),
    ('spark', "SELECT (date_format(d, 'yyyy')) FROM t", [('LOC-003', 'date_format')]),
    # a table named rlike; a CTAS with a column list; / in VIEW and SCHEMA names; ## alone
    ('spark', 'CREATE TABLE rlike (a int)', [('LOC-001', 'CREATE'), ('SYN-007', 'rlike')]),
    ('warehouse', 'CREATE TABLE t (a money) AS SELECT CAST(1 AS money) AS a',
     [('TYP-001', 'money'), ('TYP-002', 'money')]),
    ('warehouse', 'CREATE VIEW [a/b] AS SELECT 1', [('DDL-008', '[a/b]')]),
    ('warehouse', 'CREATE SCHEMA [a/b]', [('DDL-008', '[a/b]')]),
    ('warehouse', 'CREATE TABLE ##', [('SYN-007', '#')]),
    # the whole construct is marked: FOR XML, CTE self-references, SAVE TRAN, DISTRIBUTED, WITH MARK
    ('warehouse', 'SELECT a FROM t FOR XML PATH', [('DML-001', 'FOR XML')]),
    ('spark', 'WITH c AS NOT MATERIALIZED (SELECT * FROM c) SELECT * FROM c', [('DML-002', 'c')]),
    ('warehouse', 'WITH [c] AS (SELECT * FROM c) SELECT * FROM c', [('DML-002', '[c]')]),
    ('warehouse', 'SAVE TRAN s1', [('TRX-002', 'SAVE TRAN')]),
    ('warehouse', 'BEGIN DISTRIBUTED TRAN', [('TRX-003', 'BEGIN DISTRIBUTED TRAN')]),
    ('warehouse', 'BEGIN TRAN t1 WITH MARK',
     [('TRX-002', 'BEGIN TRAN t1'), ('TRX-004', 'BEGIN TRAN'), ('TRX-002', 'WITH MARK')]),
    # NOT IN (SELECT at the end; SET in a second statement's UPDATE; INSERT OVERWRITE; TEMP VIEW
    ('spark', 'SELECT 1 FROM t WHERE a NOT IN (SELECT', [('SEM-004', 'NOT IN'), ('SYN-007', 'SELECT')]),
    ('spark', 'SELECT 1; UPDATE statistics SET a = 1', [('DES-003', 'UPDATE'), ('LOC-001', 'UPDATE')]),
    ('spark', 'INSERT OVERWRITE t SELECT 1', [('DES-004', 'INSERT OVERWRITE'), ('LOC-001', 'INSERT')]),
    ('spark', 'CREATE TEMP VIEW v AS SELECT 1', [('LOC-007', 'CREATE')]),
    # a bracketed name with a space after DEFAULT isn't a column; LIMIT after a call; [c 1] after SELECT
    ('sqldb', 'ALTER TABLE t ADD a int DEFAULT [my col] + 1', []),
    ('warehouse', 'SELECT a FROM t ORDER BY sum(c) LIMIT 2', [('SYN-001', 'LIMIT 2')]),
    ('spark', 'SELECT [c 1] FROM t', [('SYN-003', '[c 1]')]),
    # CREATE OR REPLACE TABLE, BULK INSERT, SET ROWCOUNT, SET TRANSACTION ISOLATION marked whole
    ('endpoint', 'CREATE OR REPLACE TABLE dbo.parts AS SELECT 3 AS s',
     [('EP-001', 'CREATE OR REPLACE TABLE'), ('SYN-009', 'CREATE OR REPLACE TABLE')]),
    ('spark', 'CREATE OR REPLACE TABLE dbo.parts AS SELECT 3 AS s',
     [('DES-004', 'CREATE OR REPLACE TABLE'), ('LOC-001', 'CREATE')]),
    ('warehouse', "BULK INSERT dbo.orders FROM 'a.csv'", [('DML-001', 'BULK INSERT')]),
    ('warehouse', 'SET ROWCOUNT 1', [('DML-001', 'SET ROWCOUNT')]),
    ('warehouse', 'SET TRANSACTION ISOLATION LEVEL SNAPSHOT',
     [('TRX-001', 'SET TRANSACTION ISOLATION'), ('SYN-007', 'LEVEL')]),
    # EXEC AS, WITH RECURSIVE, TRUNCATE TABLE; a plain view is a write; FN-001 isn't dropped by a later finding
    ('sqldb', "EXEC AS LOGIN = 'alice'", [('DB-001', 'EXEC AS')]),
    ('spark', 'WITH RECURSIVE n AS (SELECT 1 AS i) SELECT i FROM n', [('DML-002', 'WITH RECURSIVE')]),
    ('spark', 'TRUNCATE TABLE dbo.sales', [('DES-002', 'TRUNCATE TABLE'), ('LOC-001', 'TRUNCATE')]),
    ('spark', 'CREATE VIEW v AS SELECT 1', [('LOC-001', 'CREATE')]),
    ('spark', "SELECT strlen(a) FROM t WHERE b RLIKE 'x'", [('FN-001', 'strlen'), ('LOC-002', 'RLIKE')]),
    # CREATE (UNIQUE) INDEX marked as written; datetime2(7) as the last word; REFERENCES a table named foreign
    ('warehouse', 'CREATE INDEX i ON t (a)', [('DDL-012', 'CREATE INDEX')]),
    ('warehouse', 'CREATE UNIQUE INDEX i ON t (a)', [('DDL-003', 'CREATE UNIQUE')]),
    ('warehouse', 'ALTER TABLE t ADD a datetime2(7)', [('TYP-003', 'datetime2(7)')]),
    ('warehouse', 'CREATE TABLE t (a int REFERENCES foreign (a))', [('DDL-004', 'REFERENCES')]),
    # ADD CONSTRAINT c DEFAULT at the end; FOR JSON in a subquery; TABLE x.y.z inside parentheses
    ('warehouse', 'ALTER TABLE t ADD CONSTRAINT c DEFAULT',
     [('DDL-007', 'ADD CONSTRAINT c DEFAULT'), ('DDL-012', 'DEFAULT')]),
    ('warehouse', 'SELECT (SELECT a FROM t FOR JSON PATH) AS j', [('DML-001', 'FOR JSON')]),
    ('warehouse', 'SELECT * FROM t WHERE a IN (TABLE x.y.z)', [('DML-007', 'x.y.z'), ('SYN-007', 'x')]),
    # [7] isn't a number; a [ at the very end; t.rlike is a column
    ('warehouse', 'SELECT [7] / 2 FROM t', []),
    ('spark', 'SELECT f(x) [', [('SYN-007', '[')]),
    ('spark', 'SELECT t.rlike FROM t', []),
    # NOT as the last word of a key; DROP COLUMN and ALTER COLUMN marked whole; OPTION (...) inside parentheses
    ('spark', 'CREATE TABLE t (a int PRIMARY KEY NOT', [('LOC-001', 'CREATE'), ('SYN-007', 'NOT')]),
    ('spark', 'ALTER TABLE t DROP COLUMN c', [('LOC-001', 'ALTER'), ('DES-005', 'DROP COLUMN')]),
    ('warehouse', 'ALTER TABLE dbo.t ALTER COLUMN c int', [('DDL-007', 'ALTER COLUMN')]),
    ('warehouse', 'SELECT (OPTION (MAXDOP 1))', [('DML-005', 'OPTION (MAXDOP 1)'), ('SYN-007', 'OPTION')]),
    # 7.5 / 2 isn't whole-number division; x.limit isn't LIMIT
    ('spark', 'SELECT 7.5 / 2', []),
    ('warehouse', 'SELECT x.limit 5 FROM x', [('SYN-007', '5')]),
    # TABLE as the fourth word isn't read as a table write (a Spark statement); external as a table's alias
    ('endpoint', 'CREATE OR REPLACE TEMPORARY TABLE t (a int)', []),
    ('endpoint', 'CREATE TABLE t external (a int)', [('EP-001', 'CREATE TABLE')]),
    # a decimal in a CTAS whose only * follows SELECT; ORDER read as a name before WITH (...) isn't a table
    ('spark', 'CREATE TABLE dbo.bases AS SELECT * FROM (VALUES (CAST(2.5 AS DECIMAL(5, 1)))) v(d)',
     [('LOC-001', 'CREATE')]),
    ('warehouse', 'SELECT a FROM t ORDER WITH (NOLOCK)', []),
    # a #temp table on the endpoint; a four-part name; CREATE UNIQUE INDEX after a CREATE TABLE in one batch
    ('endpoint', 'CREATE TABLE #t (a int)', []),
    ('warehouse', 'SELECT * FROM srv.db.dbo.t', [('DML-008', 'srv.db.dbo.t')]),
    ('warehouse', 'CREATE TABLE t (a int) CREATE UNIQUE INDEX i ON t (a)', [('DDL-003', 'CREATE UNIQUE')]),
    # a CTE named N that reads [n]; WITH ... INSERT INTO; sys.. at the end
    ('warehouse', 'WITH N AS (SELECT 1 AS i UNION ALL SELECT i + 1 FROM [n] WHERE i < 5) SELECT i FROM N',
     [('DML-002', 'N')]),
    ('endpoint', 'WITH a AS (SELECT 1 AS id) INSERT INTO t SELECT id FROM a', [('EP-001', 'INSERT INTO t')]),
    ('warehouse', 'SELECT * FROM dbo.t JOIN sys..', [('SYN-007', '.')]),
    # a CTE named materialized; TOP as the last word; a name that ends in a dot
    ('spark', 'WITH materialized AS MATERIALIZED (SELECT 1 AS a) SELECT * FROM materialized', []),
    ('spark', '(5) [a] TOP', []),
    ('warehouse', 'ALTER TABLE t.', [('DDL-007', '.')]),
]


@pytest.mark.parametrize("target,sql,expected", CASES)
def test_findings_point_at_this_text(target, sql, expected):
    assert _found(sql, target) == expected


_LONG_COLUMN = "a column name with spaces that runs on past sixty-four characters ok"
_LONG_COLLATION = "Latin1_General_100_CI_AS_KS_WS_SC_UTF8_and_some_more_words_to_pass_64_chars"

TITLES = [
    # the kind of object dropped, PROC spelled out
    ("warehouse", "DROP PROC p", "DES-001", "DROP procedure deletes it"),
    ("warehouse", "DROP TABLE t", "DES-001", "DROP table deletes it"),
    # the digits are the ones inside the parentheses
    ("warehouse", "ALTER TABLE t ADD a datetime2(7)", "TYP-003", "datetime2(7) keeps more than 6 digits of seconds"),
    # the constraint kind after ADD CONSTRAINT name
    ("warehouse", "ALTER TABLE t ADD CONSTRAINT c CHECK (a > 0)", "DDL-007", "ADD CHECK isn't supported"),
    # long names are cut to 64 characters
    ("sqldb", f"CREATE TABLE t ([{_LONG_COLUMN}] int)", "DDL-014", f"Column name '{_LONG_COLUMN[:64]}' isn't allowed"),
    ("warehouse", f"SELECT a COLLATE {_LONG_COLLATION} FROM t", "SEM-006", f"Collation {_LONG_COLLATION[:64]}"),
]


@pytest.mark.parametrize("target,sql,rule,title", TITLES)
def test_titles_name_what_was_found(target, sql, rule, title):
    titles = [d["title"] for d in analyze(sql, target)["diagnostics"] if d["rule"] == rule]
    assert titles == [title]


def test_whole_number_division_shows_the_result():
    (d,) = [d for d in analyze("SELECT 7 / 2", "warehouse")["diagnostics"] if d["rule"] == "SEM-003"]
    assert "(7 / 2 = 3)" in d["message"]
