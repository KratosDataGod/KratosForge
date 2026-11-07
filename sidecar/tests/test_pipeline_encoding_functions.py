"""The text encoding functions a local pipeline run gained from the coverage list: base64, base64ToString,
decodeBase64, dataUri, dataUriToString, uriComponent, encodeUriComponent, uriComponentToString and
decodeUriComponent. The ones that return a binary value are in test_pipeline_xml_binary_functions.py."""

from __future__ import annotations

import pytest

from fabric_desktop.orchestration.evaluate import ExpressionError, Scope, evaluate


def ev(text: str):
    return evaluate("@" + text, Scope())


@pytest.mark.parametrize("expr, want", [
    ("base64('hello')", "aGVsbG8="),
    ("base64('héllo €')", "aMOpbGxvIOKCrA=="),  # UTF-8
    ("base64ToString('aGVsbG8=')", "hello"),
    ("decodeBase64(base64('héllo €'))", "héllo €"),
    ("dataUri('hello')", "data:text/plain;charset=utf-8;base64,aGVsbG8="),
    ("dataUriToString(dataUri('a,b;c'))", "a,b;c"),
    ("dataUriToString('data:text/plain,hello%20world')", "hello world"),
    ("dataUriToString('data:text/plain;charset=latin-1;base64,6Q==')", "é"),
    ("uriComponent('https://contoso.com/a b?x=1&y=é')", "https%3A%2F%2Fcontoso.com%2Fa%20b%3Fx%3D1%26y%3D%C3%A9"),
    ("encodeUriComponent('A-Z_a.z~0')", "A-Z_a.z~0"),  # unreserved characters stay
    ("uriComponentToString('a%20b%2Bc+d')", "a b+c+d"),  # + is not a space
    ("decodeUriComponent(uriComponent('x/y?é'))", "x/y?é"),
])
def test_encoding_functions(expr, want):
    assert ev(expr) == want


@pytest.mark.parametrize("expr, message", [
    ("base64ToString('not base64!')", r"isn't base64-encoded UTF-8 text"),
    ("decodeBase64('/w==')", r"isn't base64-encoded UTF-8 text"),   # one byte, 0xFF: not UTF-8
    ("dataUriToString('hello')", r"isn't a data URI"),
    ("dataUriToString('data:text/plain;base64,@@@')", r"couldn't read"),
    ("decodeUriComponent('%FF')", r"aren't UTF-8 text"),
])
def test_encoding_functions_refuse_what_they_cant_do(expr, message):
    with pytest.raises(ExpressionError, match=message):
        ev(expr)
