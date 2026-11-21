"""A secret read with dbutils.secrets is shown as [REDACTED] in the notebook's output, as on Databricks."""

import json

import pytest

from fabric_desktop import notebook, secret_redaction
from fabric_desktop.notebook import Kernel

VAULT_VALUE = "pa55-w0rd-xyz"


@pytest.fixture
def kernel(monkeypatch):
    class Resp:
        status_code = 200

        def json(self):
            return {"value": VAULT_VALUE}

    class Session:
        def get(self, url, params=None, headers=None, timeout=None):
            return Resp()

    from fabric_desktop import http_session
    monkeypatch.setattr(http_session, "session", lambda: Session())
    return Kernel(token=lambda audience: "tok")


def _text(out):
    return json.dumps(out["outputs"])


def test_a_printed_secret_is_redacted(kernel):
    out = kernel.run("pw = dbutils.secrets.get('kv', 'pw')\nprint('the password is', pw)\npw")
    assert out["status"] == "ok", out
    assert VAULT_VALUE not in _text(out)
    assert "the password is [REDACTED]" in _text(out)
    assert out["outputs"][-1]["text"] == "'[REDACTED]'"


def test_a_secret_stays_redacted_in_later_cells_and_errors(kernel):
    kernel.run("pw = dbutils.secrets.get('kv', 'pw')")
    later = kernel.run("import sys\nprint(pw, file=sys.stderr)\nraise ValueError('bad password ' + pw)")
    assert VAULT_VALUE not in _text(later)
    assert "bad password [REDACTED]" in later["outputs"][-1]["evalue"]


def test_a_secret_printed_in_pieces_is_redacted(kernel):
    out = kernel.run("pw = dbutils.secrets.get('kv', 'pw')\n"
                     "import sys\nsys.stdout.write(pw[:5])\nsys.stdout.write(pw[5:])")
    assert VAULT_VALUE not in _text(out)


def test_get_bytes_and_rich_outputs_are_redacted(kernel):
    out = kernel.run("b = dbutils.secrets.getBytes('kv', 'pw')\n"
                     "from IPython.display import HTML, display\n"
                     "display(HTML('<b>' + b.decode() + '</b>'))")
    assert VAULT_VALUE not in _text(out)


def test_the_overflow_file_never_holds_a_secret(monkeypatch, kernel):
    monkeypatch.setattr(notebook, "STREAM_CHARS", 50)
    out = kernel.run("pw = dbutils.secrets.get('kv', 'pw')\nfor _ in range(20):\n    print(pw)")
    assert VAULT_VALUE not in _text(out)
    spilled = [p for p in notebook._spills]
    for path in spilled:
        if path.exists():
            assert VAULT_VALUE not in path.read_text(encoding="utf-8")


def test_a_value_the_notebook_never_read_as_a_secret_shows_as_is(kernel):
    out = kernel.run("print('" + VAULT_VALUE + "')")
    assert VAULT_VALUE in _text(out)


def test_short_values_are_not_redacted():
    secret_redaction.remember("ab")
    assert secret_redaction.redact("abc") == "abc" and not secret_redaction.active()


def test_a_value_inside_another_is_replaced_whole():
    secret_redaction.remember("abcd")
    secret_redaction.remember("xxabcdxx")
    assert secret_redaction.redact("1 xxabcdxx 2 abcd") == "1 [REDACTED] 2 [REDACTED]"


def test_regex_characters_in_a_secret_are_literal():
    secret_redaction.remember("a.b*c(d")
    assert secret_redaction.redact("axbbc(d a.b*c(d") == "axbbc(d [REDACTED]"


def test_redact_output_copies_and_leaves_bytes():
    secret_redaction.remember("topsecret")
    original = {"type": "stream", "text": "topsecret", "data": [b"topsecret", ("topsecret",)]}
    assert secret_redaction.redact_output(original) == {"type": "stream", "text": "[REDACTED]",
                                                        "data": [b"topsecret", ("[REDACTED]",)]}
    assert original["text"] == "topsecret"


def test_redact_output_keeps_the_shape_order_and_types_of_nested_values():
    secret_redaction.remember("topsecret")
    obj = object()
    original = {"z": [{"b": ("topsecret", [1.5, None, ("x", {"k": "topsecret!"})]), "a": b"topsecret"}],
                "a": [[], {}, ()], "o": obj, "n": 3}
    out = secret_redaction.redact_output(original)
    assert out == {"z": [{"b": ("[REDACTED]", [1.5, None, ("x", {"k": "[REDACTED]!"})]), "a": b"topsecret"}],
                   "a": [[], {}, ()], "o": obj, "n": 3}
    assert list(out) == ["z", "a", "o", "n"] and list(out["z"][0]) == ["b", "a"]
    assert type(out["z"][0]["b"]) is tuple and type(out["a"][2]) is tuple and out["o"] is obj
    assert out["z"] is not original["z"] and original["z"][0]["b"][0] == "topsecret"


def test_redact_output_reads_values_nested_thousands_deep():
    """A rich output nested past Python's recursion limit (a JSON display of a deep object) is redacted
    rather than raising RecursionError from every poll of the cell."""
    secret_redaction.remember("topsecret")
    value: object = {"text": "topsecret"}
    for i in range(5000):
        value = [value] if i % 3 == 0 else ({"a": value} if i % 3 == 1 else (value,))
    out = secret_redaction.redact_output(value)
    for i in reversed(range(5000)):
        out = out[0] if i % 3 != 1 else out["a"]
    assert out == {"text": "[REDACTED]"}
