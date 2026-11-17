"""Edges of the landing Copy job request check that the request tests don't pin (orchestration/copyjob_build.py)."""

import json

import pytest

from fabric_desktop.orchestration import copyjob, copyjob_build

CONN = "0f0e0d0c-0b0a-0908-0706-050403020100"


def ask(**change):
    return {"connection_id": CONN, "tables": ["dbo.Customer"]} | change


@pytest.mark.parametrize("copy", [["dbo.Customer"], "dbo.Customer", None])
def test_a_request_that_isnt_an_object_is_refused_plainly(copy):
    with pytest.raises(copyjob_build.CopyRequestError, match="Say which connection"):
        copyjob_build.check(copy)


def test_fifty_tables_is_the_most_one_request_copies():
    tables = [f"dbo.T{i}" for i in range(copyjob_build.MAX_TABLES)]
    assert len(copyjob_build.check(ask(tables=tables))["tables"]) == 50
    with pytest.raises(copyjob_build.CopyRequestError, match="50 tables at most"):
        copyjob_build.check(ask(tables=tables + ["dbo.Extra"]))


def test_a_bad_table_name_is_quoted_and_a_blank_one_says_blank():
    with pytest.raises(copyjob_build.CopyRequestError, match=r"^Customer: name a table as schema\.table"):
        copyjob_build.check(ask(tables=["Customer"]))
    with pytest.raises(copyjob_build.CopyRequestError, match=r"^\(blank\): name a table"):
        copyjob_build.check(ask(tables=[{"table": "  "}]))


def test_the_connection_name_is_kept_trimmed_to_200_characters():
    assert copyjob_build.check(ask(connection_name=" ERP "))["connection_name"] == "ERP"
    assert copyjob_build.check(ask(connection_name="x" * 300))["connection_name"] == "x" * 200


def test_the_job_never_retries_and_its_part_is_two_space_json():
    job = copyjob_build.content(copyjob_build.check(ask()), "erp", "ws", "lh")
    assert job["properties"]["policy"] == {"timeout": "0.12:00:00", "retry": 0}
    text = copyjob_build.parts(job)[copyjob.CONTENT]
    assert text == json.dumps(job, indent=2)
    assert text.splitlines()[1].startswith('  "properties"')
