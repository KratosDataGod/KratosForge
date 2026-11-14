"""b64.decode_exact: base64 from the app is whole and canonically padded, on every Python 3.12 patch."""

import base64

import pytest

from fabric_desktop import b64


@pytest.mark.parametrize("data", [b"", b"a", b"ab", b"abc", b"abcd", bytes(range(256)) * 3])
def test_whole_base64_reads_back(data):
    assert b64.decode_exact(base64.b64encode(data).decode()) == data


# "YWJj=" and "YWJj==" are accepted by base64.b64decode(validate=True) on some 3.12 patch releases
@pytest.mark.parametrize("text", ["YWJj=", "YWJj==", "YWJj===", "YQ", "YQ=", "YQ===", "Y===", "YW=j", "=YWJ",
                                  "YQ==YQ==", "YWJj\n", " YWJj", "not base64!", "YW*j"])
def test_cut_joined_or_oddly_padded_base64_is_refused(text):
    with pytest.raises(ValueError):
        b64.decode_exact(text)


def test_the_check_doesnt_lean_on_the_stdlibs_padding_rule(monkeypatch):
    # stand in for a Python whose b64decode is lenient about padding: the length check alone refuses it
    monkeypatch.setattr(b64.base64, "b64decode", lambda s, validate=False: b"abc")
    with pytest.raises(ValueError, match="multiple of 4"):
        b64.decode_exact("YWJj=")
    with pytest.raises(ValueError, match="padding"):
        b64.decode_exact("YW==YWJj")


def test_app_and_definition_base64_goes_through_decode_exact():
    # validate=True alone accepts excess padding on some 3.12 patches: every strict decode uses b64.decode_exact
    import re
    from pathlib import Path
    root = Path(b64.__file__).parent
    loose = [str(p.relative_to(root)) for p in root.rglob("*.py")
             if p.name != "b64.py" and re.search(r"b64decode\([^\n]*validate=True", p.read_text(encoding="utf-8"))]
    assert loose == []
