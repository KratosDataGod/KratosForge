"""The last functions a local pipeline run refused: the five that return a binary value, xml(), xpath()
and rand(). Binary values are carried as {"$content-type", "$content"}; xpath() is XPath 1.0 on the
standard library's expat parser, which refuses a DOCTYPE so no entity can expand."""

from __future__ import annotations

import re

import pytest

from fabric_desktop.orchestration import expr_xml
from fabric_desktop.orchestration.evaluate import ExpressionError, Scope, evaluate

ITEMS = ('<?xml version="1.0"?><produce><item><name>Gala</name><type>apple</type><count>20</count></item>'
         '<item><name>Honeycrisp</name><type>apple</type><count>10</count></item></produce>')
FILE = '<file xmlns="https://contoso.com"><location>Paris</location></file>'


def ev(text: str, **params):
    return evaluate("@" + text, Scope(parameters=params))


def octets(content: str, kind: str = "application/octet-stream") -> dict:
    return {"$content-type": kind, "$content": content}


@pytest.mark.parametrize("expr, want", [
    ("binary('hello')", octets("aGVsbG8=")),
    ("binary('é')", octets("w6k=")),                                       # UTF-8
    ("base64ToBinary('aGVsbG8=')", octets("aGVsbG8=")),
    ("decodeDataUri('data:text/plain;charset=utf-8;base64,aGVsbG8=')", octets("aGVsbG8=", "text/plain")),
    ("dataUriToBinary('data:,a%20b')", octets("YSBi")),
    ("uriComponentToBinary('a%20b+c')", octets("YSBiK2M=")),                # a + stays a +
    ("string(binary('héllo'))", "héllo"),
    ("base64(base64ToBinary('aGVsbG8='))", "aGVsbG8="),
    ("base64(decodeDataUri(dataUri('round trip')))", "cm91bmQgdHJpcA=="),
    ("concat('x', binary('y'))", "xy"),
])
def test_binary_functions(expr, want):
    assert ev(expr) == want


@pytest.mark.parametrize("expr, needle", [
    ("base64ToBinary('not base64!')", "base64ToBinary() was given text that isn't base64"),
    ("decodeDataUri('hello')", "decodeDataUri() was given text that isn't a data URI"),
    ("string(base64ToBinary('/w=='))", "isn't UTF-8 text"),
])
def test_binary_errors(expr, needle):
    with pytest.raises(ExpressionError, match=re.escape(needle)):
        ev(expr)


def test_xml_checks_text_and_converts_json():
    assert ev("xml('<name>Sophia Owen</name>')") == "<name>Sophia Owen</name>"
    obj = {"person": {"@id": 1, "name": "Sophia & co", "tag": ["a", "b"], "none": None, "ok": True,
                      "note": {"@lang": "en", "#text": "hi"}}}
    assert ev("xml(pipeline().parameters.obj)", obj=obj) == (
        '<person id="1"><name>Sophia &amp; co</name><tag>a</tag><tag>b</tag><none /><ok>true</ok>'
        '<note lang="en">hi</note></person>')


@pytest.mark.parametrize("expr, needle", [
    ("xml('<a>')", "isn't well formed"),
    ("xml('plain text')", "isn't well formed"),
    ("xml(json('{\"a\": 1, \"b\": 2}'))", "exactly one root property, not 2"),
    ("xml(json('{\"bad name\": 1}'))", "can't be an XML element name"),
    ("xml(createArray(1))", "XML text or a JSON object"),
    ("xml('<a:b/>')", "the prefix 'a' on <a:b> isn't declared"),
])
def test_xml_errors(expr, needle):
    with pytest.raises(ExpressionError, match=re.escape(needle)):
        ev(expr)


@pytest.mark.parametrize("path, want", [
    # the reference's examples
    ("/produce/item/name", ["<name>Gala</name>", "<name>Honeycrisp</name>"]),
    ("sum(/produce/item/count)", 30),
    # node sets
    ("/produce/item/name/text()", ["Gala", "Honeycrisp"]),
    ("//item[count > 15]/name/text()", ["Gala"]),
    ("//item[last()]/name/text()", ["Honeycrisp"]),
    ("//item[2]/preceding-sibling::item/name/text()", ["Gala"]),
    ("(//name)[1]/following::name/text()", ["Honeycrisp"]),
    ("//name[../count < 15] | //type[1]", ["<type>apple</type>", "<name>Honeycrisp</name>", "<type>apple</type>"]),
    ("//item[name = 'Gala']/ancestor::*/@missing", []),
    ("/produce/item[1]/*[position() > 1]", ["<type>apple</type>", "<count>20</count>"]),
    ("//count[. = 10]/parent::item/name/text()", ["Honeycrisp"]),
    # scalars
    ("count(//item)", 2),
    ("count(/produce/node())", 2),           # white space between elements is dropped
    ("//item[1]/count * 2 div 8", 5),
    ("avg-not-a-path = 1", False),
    ("string(//name)", "Gala"),
    ("concat(name(/*), ':', string-length((//name)[2]))", "produce:10"),
    ("string-length(//name[2])", 0),         # //name[2] is a second name under one parent: none
    ("substring-before('2018-03-15', '-')", "2018"),
    ("translate('apple', 'ap', 'AP')", "APPle"),
    ("normalize-space('  a   b ')", "a b"),
    ("round(2.5) + floor(-1.5) + ceiling(1.1)", 3),
    ("boolean(//pear)", False),
    ("not(//item[count > 100])", True),
    ("//count > 15 and //count < 15", True),  # a node set compares true when any node does
    ("-(//count)[2] mod 3", -1),
    ("number('1.5') + .5", 2),
    ("- - -1 + " + "(" * 30 + "1" + ")" * 30, 0),
    ("-" * 5001 + "1", -1),
])
def test_xpath(path, want):
    assert ev("xpath(xml(pipeline().parameters.items), pipeline().parameters.path)", items=ITEMS, path=path) == want


def test_xpath_namespaces_and_inputs():
    # the reference's namespace examples: match on name() / local-name() and namespace-uri()
    assert ev("xpath(xml(pipeline().parameters.f), '/*[name()=\"file\"]/*[name()=\"location\"]')", f=FILE) == [
        '<location xmlns="https://contoso.com">Paris</location>']
    assert ev("xpath(xml(pipeline().parameters.f), 'string(/*[local-name()=\"file\" and "
              "namespace-uri()=\"https://contoso.com\"]/*[local-name()=\"location\"])')", f=FILE) == "Paris"
    assert ev("xpath(pipeline().parameters.f, '/file')", f=FILE) == []     # an unprefixed name has no namespace
    assert ev("xpath('<p:a xmlns:p=\"u\" x=\"1\"><p:b>1</p:b></p:a>', '/*/*')") == ['<p:b xmlns:p="u">1</p:b>']
    assert ev("xpath('<a x=\"1\" y=\"2\"/>', '/a/@*')") == ["1", "2"]
    assert ev("xpath('<a><!--note--></a>', '/a/comment()')") == ["note"]
    assert ev("xpath(binary('<a>1</a>'), 'number(/a)')") == 1
    assert ev("xpath(json('{\"r\": {\"v\": [1, 2, 3]}}'), 'sum(/r/v)')") == 6
    assert ev("xpath('<a>1 &amp; 2</a>', '/a')") == ["<a>1 &amp; 2</a>"]
    assert ev("xpath('<a><!--c--><b>x</b></a>', '/')") == ["<a><!--c--><b>x</b></a>"]   # the document node


@pytest.mark.parametrize("doc, path, needle", [
    ('<!DOCTYPE a [<!ENTITY x "y">]><a>&x;</a>', "/a", "<!DOCTYPE> isn't read"),
    ('<!DOCTYPE a SYSTEM "file:///etc/passwd"><a/>', "/a", "<!DOCTYPE> isn't read"),
    ("<a/>", "/p:a", "uses a namespace prefix"),
    ("<a/>", "$x", "variables ($name) aren't available"),
    ("<a/>", "id('x')", "id() isn't supported"),
    ("<a/>", "namespace::*", "namespace:: isn't"),
    ("<a/>", "/a[", "ends too soon"),
    ("<a/>", "0 div 0", "NaN"),
    ("<a/>", "count('x')", "needs a node set"),
    ("<a/>", "1 | 2", "joins two node sets"),
    ("<a/>", "x" * (expr_xml.MAX_XPATH + 1), "longer than"),
    ("<a/>", "(" * 40 + "1" + ")" * 40, "nests too deeply"),
    ("<a/>", "/a" + "[1" * 40 + "]" * 40, "nests too deeply"),
])
def test_xpath_errors(doc, path, needle):
    with pytest.raises(ExpressionError, match=re.escape(needle)):
        ev("xpath(pipeline().parameters.doc, pipeline().parameters.path)", doc=doc, path=path)


def test_xpath_limits_depth_and_work(monkeypatch):
    deep = "<a>" * (expr_xml.MAX_DEPTH + 2) + "</a>" * (expr_xml.MAX_DEPTH + 2)
    with pytest.raises(ExpressionError, match="nests more than"):
        ev("xpath(pipeline().parameters.d, '/a')", d=deep)
    monkeypatch.setattr(expr_xml, "MAX_VISITS", 10_000)
    wide = "<r>" + "<i/>" * 300 + "</r>"
    with pytest.raises(ExpressionError, match="more than 10,000 steps of work"):
        ev("xpath(pipeline().parameters.d, 'count(//i[count(//i) > 0])')", d=wide)
    with pytest.raises(ExpressionError, match="more than 10,000 steps of work"):
        ev("xpath(pipeline().parameters.d, '//i = //i[1]/following::i')", d=wide)   # 300 x 299 comparisons
    assert ev("xpath(pipeline().parameters.d, 'count(//i)')", d=wide) == 300


def test_xpath_default_limits_stop_quadratic_work_quickly():
    import time
    wide = "<r>" + "<i/>" * 2000 + "</r>"
    start = time.monotonic()
    with pytest.raises(ExpressionError, match="steps of work|seconds"):
        ev("xpath(pipeline().parameters.d, 'count(//i[count(//i) > 0])')", d=wide)
    assert time.monotonic() - start < expr_xml.MAX_SECONDS + 1.5


def test_xpath_result_list_is_bounded():
    # 500 nested elements around 1 MB of text: "//*" would repeat the text in every one (about 500 MB)
    doc = "<a>" * 500 + "x" * 1_000_000 + "</a>" * 500
    with pytest.raises(ExpressionError, match="result has more than 10,485,760 characters"):
        ev("xpath(pipeline().parameters.d, '//*')", d=doc)
    assert len(ev("xpath(pipeline().parameters.d, '/a')", d=doc)[0]) == 1_000_000 + 7 * 500


def test_xpath_time_budget(monkeypatch):
    monkeypatch.setattr(expr_xml, "MAX_SECONDS", 0.0)
    with pytest.raises(ExpressionError, match="ran longer than 0 seconds"):
        ev("xpath('<r><i/><i/></r>', 'count(//i)')")


def test_xpath_text_it_builds_is_bounded():
    # 100 KB of text, concatenated 40 times, would be 4 MB
    doc = "<r>" + "x" * 100_000 + "</r>"
    path = "concat(" + ", ".join(["string(/)"] * 40) + ")"
    with pytest.raises(ExpressionError, match="concat\\(\\) made text of more than 1,048,576 characters"):
        ev("xpath(pipeline().parameters.d, pipeline().parameters.p)", d=doc, p=path)
    # reading text is charged too: 200 reads of 1 MB is past the work limit, though no result is big
    doc = "<r>" + "x" * 1_000_000 + "</r>"
    path = "concat(" + ", ".join(["string-length(string(/))"] * 200) + ")"
    with pytest.raises(ExpressionError, match="steps of work"):
        ev("xpath(pipeline().parameters.d, pipeline().parameters.p)", d=doc, p=path)


def test_rand():
    seen = {ev("rand(1, 4)") for _ in range(200)}
    assert seen == {1, 2, 3}
    assert ev("rand(-2, -1)") == -2
    for expr, needle in (("rand(5, 5)", "less than its maximum"), ("rand(1.5, 3)", "two whole numbers"),
                         ("rand('1', 3)", "two whole numbers")):
        with pytest.raises(ExpressionError, match=re.escape(needle)):
            ev(expr)
