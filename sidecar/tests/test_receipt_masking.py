"""Receipt exports mask remembered secrets.

A value a notebook read with dbutils.secrets shows as [REDACTED] in a receipt's Markdown and HTML
export: in its parameters, results, notes, limitations and every other text the export shows,
escaped and encoded forms included. The receipt itself is not changed.
"""

from __future__ import annotations

import html
import json
import random
import string
from urllib.parse import quote

import pytest

from fabric_desktop import receipts, secret_redaction
from fabric_desktop.secret_redaction import MIN_CHARS, REDACTED

# Made-up values, only for these tests.
SECRET = "Q1W2Z3X4J5K6Q"  # pragma: allowlist secret (made up)
ACCENTED = "Q1W2Z3X4é5J6K7V8Y9"  # pragma: allowlist secret (made up)
FIRST = "Q1W2Z3X4J5"  # pragma: allowlist secret (made up)
SECOND = "J5K6V7Y8"  # pragma: allowlist secret (made up)


@pytest.fixture(autouse=True)
def _clean():
    secret_redaction.forget_all()
    yield
    secret_redaction.forget_all()


def _parts(value: str) -> set[str]:
    return {value[i:j] for i in range(len(value)) for j in range(i + MIN_CHARS, len(value) + 1)}


def _assert_masked(out: str, *values: str) -> None:
    for value in values:
        shown = [p for p in _parts(value) if p in out]
        assert not shown, (value, sorted(shown, key=len)[-3:])


def _receipt(text: str) -> dict:
    return receipts.new_receipt(
        "proof",
        notebook={"name": "Sales", "parameters": {"token": text, "region": f"eu {text}"},
                  "code": {"revision": "ab" * 32, "cells": []}},
        verification={"verdict": "match", "text": f"Outputs match ({text})", "level": f"rows {text}"},
        outputs=[{"table": f"t_{text}", "result": "match", "level": "rows", "pc": {"rows": 3},
                  "fabric": {"rows": 3}, "note": f"compared with {text} | done"}],
        inputs={"tables": [{"name": f"src {text}", "version": 4}], "files": [{"path": f"/f/{text}", "sha256": "0" * 64}]},
        runs={"pc": {"seconds": 1.5, "status": "ok", "message": f"ran with {text}"}},
        cost={"text": f"cost {text}", "assumptions": [f"assumed {text}"]},
        environment={"pc": {"python": "3.12", "packages": {"pkg": text}}},
        limitations=[f"the proof used {text}", "nothing else"],
    )


def _exports(body: dict) -> tuple[str, str]:
    rid = receipts.receipt_id(body)
    return receipts.to_markdown(body, rid), receipts.to_html(body, rid)


def test_a_remembered_value_is_masked_everywhere_in_the_markdown_and_html_exports():
    secret_redaction.remember(SECRET)
    body = _receipt(SECRET)
    md, page = _exports(body)
    _assert_masked(md, SECRET)
    _assert_masked(page, SECRET)
    _assert_masked(html.unescape(page), SECRET)
    for shown in ("Outputs match ([REDACTED])", "the proof used [REDACTED]", "assumed [REDACTED]",
                  '"token": "[REDACTED]"', '"region": "eu [REDACTED]"'):
        assert shown in md, shown
    assert REDACTED in page and "the proof used [REDACTED]" in page


def test_the_receipt_itself_and_its_id_are_unchanged():
    secret_redaction.remember(SECRET)
    body = _receipt(SECRET)
    rid = receipts.receipt_id(body)
    before = json.dumps(body, sort_keys=True)
    _exports(body)
    assert json.dumps(body, sort_keys=True) == before
    assert receipts.receipt_id(body) == rid


def test_a_non_ascii_value_is_masked_in_the_parameters_json_and_everywhere_else():
    secret_redaction.remember(ACCENTED)
    md, page = _exports(_receipt(ACCENTED))
    for out in (md, page, html.unescape(page)):
        _assert_masked(out, ACCENTED, json.dumps(ACCENTED)[1:-1])
    assert '"token": "[REDACTED]"' in md


def test_encoded_forms_of_a_value_in_free_text_are_masked():
    secret_redaction.remember(ACCENTED)
    for form in (json.dumps(ACCENTED)[1:-1], quote(ACCENTED, safe=""), ascii(ACCENTED)[1:-1],
                 quote(ACCENTED, safe="").replace("%C3%A9", "%c3%a9")):
        md, page = _exports(_receipt(form))
        _assert_masked(md, form)
        _assert_masked(html.unescape(page), form)


def test_overlapping_values_are_masked_together():
    for v in (FIRST, SECOND):
        secret_redaction.remember(v)
    md, page = _exports(_receipt(FIRST + SECOND[2:]))
    _assert_masked(md, FIRST, SECOND)
    _assert_masked(html.unescape(page), FIRST, SECOND)


def test_a_value_in_a_parameter_name_and_the_notebook_name_is_masked():
    secret_redaction.remember(SECRET)
    body = receipts.new_receipt("proof", notebook={"name": f"nb {SECRET}", "parameters": {SECRET: 1}},
                                limitations=[])
    md, page = _exports(body)
    _assert_masked(md, SECRET)
    _assert_masked(page, SECRET)


def test_an_export_without_remembered_values_is_unchanged():
    body = _receipt("plain words")
    md, page = _exports(body)
    assert "Outputs match (plain words)" in md and "the proof used plain words" in md
    secret_redaction.remember(SECRET)
    assert _exports(body) == (md, page)


@pytest.mark.parametrize("seed", range(5))
def test_random_values_in_random_receipt_text_are_masked(seed):
    rng = random.Random(3000 + seed)
    alphabet = string.ascii_uppercase + string.digits + "é-_/+="
    values = ["".join(rng.choice(alphabet) for _ in range(rng.randint(MIN_CHARS + 4, 30))) for _ in range(3)]
    for v in values:
        secret_redaction.remember(v)
    words = [rng.choice(values) if rng.random() < 0.3 else "".join(rng.choice("abcdefgh") for _ in range(5))
             for _ in range(12)]
    md, page = _exports(_receipt(" ".join(words)))
    _assert_masked(md, *values)
    _assert_masked(html.unescape(page), *values)
