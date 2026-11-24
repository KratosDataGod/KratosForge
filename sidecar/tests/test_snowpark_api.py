"""The Snowpark API compatibility check (snowpark_api.py, SF-007 option C): a cell's Snowpark calls, methods
and keywords against the pinned signatures, the not-locally-checkable families flagged, and nothing executed."""

from __future__ import annotations

import ast
import json
import re
import tomllib
from pathlib import Path

import pytest

from fabric_desktop import audit, engines_service, snowpark_api
from fabric_desktop.snowpark_api import check

SIDECAR = Path(__file__).resolve().parents[1]
ROOT = SIDECAR.parent


def kinds(result):
    return [(f["kind"], f["name"]) for f in result["findings"]]


def families(code):
    return check(code)["families"]


# ---- the signature file


def test_the_signature_file_is_the_pinned_version_captured_on_the_apps_python():
    data = snowpark_api.api()
    assert data["snowpark"] == snowpark_api.SNOWPARK_VERSION == "1.58.0"
    assert data["python"] == "3.12"
    for cls in ("Session", "SessionBuilder", "DataFrame", "Column", "Table", "DataFrameReader", "DataFrameWriter",
                "RelationalGroupedDataFrame", "Window", "WindowSpec", "FileOperation", "UDFRegistration"):
        assert cls in data["classes"], cls
    functions = data["modules"]["snowflake.snowpark.functions"]
    assert {"col", "lit", "sum", "udf", "sproc", "ai_complete"} <= set(functions)
    assert sum(e["kind"] == "function" for e in functions.values()) > 400
    # Chains carry their types: Session.builder...create() is a Session, a DataFrame's filter a DataFrame.
    assert data["classes"]["Session"]["members"]["builder"] == {"kind": "attribute", "returns": "SessionBuilder"}
    assert data["classes"]["SessionBuilder"]["members"]["create"]["returns"] == "Session"
    assert data["classes"]["DataFrame"]["members"]["filter"]["returns"] == "DataFrame"
    assert data["classes"]["DataFrame"]["getattr"] == "Column"


def test_the_capture_tool_runs_by_hand_in_a_scratch_environment_not_the_sidecars():
    tool = (SIDECAR / "tools" / "capture_snowpark_api.py").read_text(encoding="utf-8")
    assert "never in CI, and never in the sidecar's own environment" in tool
    assert f"snowflake-snowpark-python[localtest]=={snowpark_api.SNOWPARK_VERSION}" in tool
    tree = ast.parse(tool)
    # It reads signatures: no session is created, local or remote.
    assert not any(isinstance(n, ast.Attribute) and n.attr in ("create", "getOrCreate") for n in ast.walk(tree))


def test_the_app_doesnt_depend_on_snowpark_and_ships_the_signature_file():
    for name in ("pyproject.toml", "constraints.txt", "engine-requirements.txt", "ci-constraints.txt"):
        assert "snowflake-snowpark-python" not in (SIDECAR / name).read_text(encoding="utf-8").lower(), name
    data = tomllib.loads((SIDECAR / "pyproject.toml").read_text(encoding="utf-8"))
    assert "snowpark_api.json" in data["tool"]["setuptools"]["package-data"]["fabric_desktop"]


def test_the_checker_imports_nothing_from_snowflake():
    tree = ast.parse(Path(snowpark_api.__file__).read_text(encoding="utf-8"))
    imported = {a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
    imported |= {n.module or "" for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)}
    assert not any(m.startswith("snowflake") for m in imported)


# ---- calls that exist


CLEAN = '''
from snowflake.snowpark import Session, Window
from snowflake.snowpark import functions as F
from snowflake.snowpark.functions import col, lit
from snowflake.snowpark.types import StructType, StructField, IntegerType, StringType

session = Session.builder.configs({"local_testing": True}).create()
schema = StructType([StructField("id", IntegerType()), StructField("name", StringType())])
df = session.create_dataframe([[1, "a"], [2, "b"]], schema=schema)
out = (df.filter(col("id") > 1).with_column("one", lit(1)).group_by("name")
         .agg(F.sum("id").alias("total")).sort(col("total").desc()).limit(10))
w = Window.partition_by("name").order_by(col("id"))
ranked = df.select(col("id"), F.row_number().over(w).alias("rn"))
df.join(ranked, df.id == ranked.id, how="left").union_all(df).show()
df["id"].cast(StringType()).alias("x")
df.write.mode("overwrite").save_as_table("t", table_type="temporary")
session.table("t").collect()
'''


def test_a_cell_of_known_calls_is_compatible_and_says_what_it_checked():
    r = check(CLEAN)
    assert r["findings"] == [] and r["compatible"] is True
    assert r["checked"] >= 25
    assert r["label"] == "API compatibility check against Snowpark Python 1.58.0"
    assert r["check"] == "api_compatibility" and r["snowpark"] == "1.58.0"


def test_get_active_session_and_annotated_arguments_are_sessions_too():
    code = ("from snowflake.snowpark.context import get_active_session\nfrom snowflake.snowpark import Session\n"
            "s = get_active_session()\ns.tabel('x')\n"
            "def load(sess: Session):\n    return sess.tabl('y')\n")
    assert kinds(check(code)) == [("unknown_method", "tabel"), ("unknown_method", "tabl")]


# ---- what it reports


def test_unknown_methods_functions_and_imported_names():
    r = check("from snowflake.snowpark.functions import col, no_such_fn\nimport snowflake.snowpark.functions as F\n"
              "df = session.table('t')\ndf.selec('a')\nF.nope(1)\nsession.read.parquett('x')\n")
    assert kinds(r) == [("unknown_function", "no_such_fn"), ("unknown_method", "selec"), ("unknown_function", "nope"),
                        ("unknown_method", "parquett")]
    assert r["compatible"] is False
    assert r["findings"][1]["message"] == "Table has no selec in Snowpark Python 1.58.0."
    assert r["findings"][1]["line"] == 4 and r["findings"][1]["col"] == 1


def test_a_dataframes_columns_by_name_are_columns_but_calling_one_is_unknown():
    r = check("df = session.table('t')\ndf.amount.alias('a')\ndf.amount('a')\n")
    assert kinds(r) == [("unknown_method", "amount")] and r["findings"][0]["line"] == 3


def test_unknown_keywords_and_too_many_positional_arguments():
    r = check("df = session.table('t')\ndf.sort('a', ascending=False, descending=True)\n"
              "df.limit(1, 2, 3)\ndf.limit(*args)\ndf.select('a', 'b', 'c', 'd')\nsession.table('t', **opts)\n")
    assert kinds(r) == [("unknown_keyword", "descending"), ("too_many_arguments", "limit")]
    assert "Table.sort takes no keyword descending" in r["findings"][0]["message"]
    assert "takes at most 2 positional arguments" in r["findings"][1]["message"]


def test_constructors_are_checked_against_their_init():
    r = check("from snowflake.snowpark.types import StructField, IntegerType\n"
              "StructField('a', IntegerType(), nullable=True, colour='red')\n")
    assert kinds(r) == [("unknown_keyword", "colour")]


def test_a_call_it_cant_place_is_counted_never_guessed():
    r = check("import pandas as pd\npdf = pd.DataFrame()\npdf.filter(items=['a']).selec()\nsomething.group_by('x')\n")
    assert r["findings"] == [] and r["checked"] == 0
    assert r["not_placed"] == 2  # filter and group_by are Snowpark method names; selec isn't


def test_rebinding_session_stops_it_being_a_snowpark_session():
    assert check("session.selec()")["findings"]
    assert check("session = object()\nsession.selec()")["findings"] == []


def test_nested_and_conditional_code_is_checked_in_order():
    code = ("df = session.table('t')\nif df.count() > 0:\n    for r in df.collect():\n        print(r.NAME)\n"
            "try:\n    df.frist()\nexcept Exception:\n    pass\nwith open('x') as f:\n    df.lmit(1)\n"
            "rows = [df.selct('a') for _ in range(2)]\nclass K:\n    x = df.sortt()\n")
    assert [n for _, n in kinds(check(code))] == ["frist", "lmit", "selct", "sortt"]


# ---- the families that need Snowflake's servers


@pytest.mark.parametrize("code", [
    "from snowflake.snowpark.functions import ai_complete",
    "import snowflake.snowpark.functions as F\nF.ai_classify('x', ['a'])",
    "from snowflake.snowpark import functions as F\nF.snowflake_cortex_summarize('t')",
    "from snowflake.snowpark import functions as F\nF.call_function('SNOWFLAKE.CORTEX.COMPLETE', 'm', 'p')",
    "from snowflake.cortex import Complete",
    "df = session.table('t')\ndf.ai.complete('p')",
])
def test_cortex_is_flagged(code):
    assert families(code) == ["cortex"]


@pytest.mark.parametrize("code", [
    "from snowflake.snowpark.functions import udf\n@udf(external_access_integrations=['x'])\ndef f(a): return a",
    "session.udf.register(lambda a: a, secrets={'k': 'v'})",
    "import _snowflake\n_snowflake.get_generic_secret_string('k')",
])
def test_external_access_and_secrets_are_flagged(code):
    assert families(code) == ["external_access"]


@pytest.mark.parametrize("code", [
    "session.file.put('a.csv', '@stage')",
    "session.file.get('@stage/a.csv', 'out')",
    "session.read.csv('@stage/a.csv')",
    "df = session.table('t')\ndf.write.copy_into_location('out/')",
    "session.add_import('lib.zip')",
    "from snowflake.snowpark.functions import sproc\n@sproc(stage_location='st')\ndef p(s): return 1",
])
def test_stages_are_flagged(code):
    assert families(code) == ["stages"]


@pytest.mark.parametrize("code", [
    "import modin.pandas as pd",
    "import snowflake.snowpark.modin.plugin",
    "from modin import pandas",
])
def test_pandas_on_snowflake_is_flagged(code):
    assert families(code) == ["pandas_on_snowflake"]


def test_a_flagged_family_doesnt_make_a_cell_incompatible():
    r = check("session.file.put('a.csv', '@stage')")
    assert r["compatible"] is True and r["findings"][0]["kind"] == "not_locally_checkable"
    assert r["findings"][0]["family"] == "stages"
    assert r["findings"][0]["message"] == snowpark_api.FAMILIES["stages"]


# ---- cells that aren't plain Python


def test_magics_are_skipped_and_line_numbers_kept():
    r = check("%pip install x\n!ls\ndf = session.table('t')\ndf.selec()\n")
    assert kinds(r) == [("unknown_method", "selec")] and r["findings"][0]["line"] == 4


def test_a_cell_magic_or_broken_python_is_said_not_checked():
    r = check("%%sql\nSELECT 1")
    assert r["compatible"] is None and "cell magic" in r["error"]
    r = check("df = (")
    assert r["compatible"] is None and r["error"].startswith("The cell can't be read as Python (line 1")


# ---- wording


def _texts(value):
    if isinstance(value, dict):
        for v in value.values():
            yield from _texts(v)
    elif isinstance(value, list):
        for v in value:
            yield from _texts(v)
    elif isinstance(value, str):
        yield value


def test_it_says_api_compatibility_check_and_never_validated_or_runs():
    every = [CLEAN, "df = session.table('t')\ndf.selec(x=1)\ndf.limit(1, 2, 3)", "%%sql\nx", "df = (",
             ("from snowflake.snowpark.functions import ai_complete, nope\nimport modin.pandas\n"
              "session.file.put('a', '@s')\nsession.udf.register(f, secrets={})")]
    texts = [t for code in every for t in _texts(check(code))]
    texts += list(snowpark_api.FAMILIES.values()) + [snowpark_api.NOTE, snowpark_api.LABEL]
    for text in texts:
        assert not re.search(r"\bvalidat|\bruns\b|\bverif", text, re.I), text
    assert "API compatibility check" in snowpark_api.LABEL


# ---- the engine call


def test_the_engine_call_checks_a_cell_and_is_read_only():
    class Svc(engines_service.EnginesMixin):
        pass

    assert Svc().snowpark_api_check("session.selec()")["findings"][0]["name"] == "selec"
    assert "snowpark_api_check" in engines_service.METHODS and "snowpark_api_check" in audit.READ_ONLY


def test_the_signature_file_is_plain_json_the_check_reads_once():
    assert json.loads(snowpark_api.DATA.read_text(encoding="utf-8")) == snowpark_api.api()
    assert snowpark_api.api() is snowpark_api.api()
