"""The engine's log masks remembered secrets in long lines and encoded forms.

What goes through ``RedactedStream`` (the engine's and a kernel's stderr) and the engine's log
formatter shows a remembered value as [REDACTED]: in a line longer than ``HOLD`` that goes out in
parts, wherever the value falls against the part boundary, when two values overlap, and when the
value is printed JSON-escaped, percent-encoded or as ``ascii()`` writes it.
"""

from __future__ import annotations

import io
import json
import logging
import random
import string
import time
import tracemalloc
from urllib.parse import quote

import pytest

from fabric_desktop import secret_redaction, server
from fabric_desktop.secret_redaction import MIN_CHARS, REDACTED, RedactedStream

# Made-up values, only for these tests.
SECRET = "Q1W2Z3X4J5K6Q"  # pragma: allowlist secret (made up)
FIRST = "Q1W2Z3X4J5"  # pragma: allowlist secret (made up)
SECOND = "J5K6V7Y8"  # pragma: allowlist secret (made up)
ACCENTED = "Q1W2Z3X4é5J6K7V8Y9"  # pragma: allowlist secret (made up)
HOLD = RedactedStream.HOLD


@pytest.fixture(autouse=True)
def _clean():
    secret_redaction.forget_all()
    yield
    secret_redaction.forget_all()


def _parts(value: str) -> set[str]:
    """Every piece of ``value`` at least MIN_CHARS long."""
    return {value[i:j] for i in range(len(value)) for j in range(i + MIN_CHARS, len(value) + 1)}


def _assert_masked(out: str, *values: str) -> None:
    for value in values:
        assert value not in out
        shown = [p for p in _parts(value) if p in out]
        assert not shown, (value, sorted(shown, key=len)[-3:])


def _through(writes: list[str]) -> str:
    out = io.StringIO()
    stream = RedactedStream(out)
    for w in writes:
        stream.write(w)
    stream.flush()
    return out.getvalue()


def _filler(rng: random.Random, n: int) -> str:
    # Lower-case letters and spaces only: never part of the upper-case and digit values used here.
    return "".join(rng.choice("abcdefghijklmnopqrstuvwxyz ") for _ in range(n))


def _chunks(rng: random.Random, text: str, most: int) -> list[str]:
    parts, at = [], 0
    while at < len(text):
        n = rng.randint(1, most)
        parts.append(text[at:at + n])
        at += n
    return parts


# ---- a line longer than HOLD


def test_a_value_that_ends_a_line_past_hold_is_masked_whole():
    secret_redaction.remember(SECRET)
    out = _through(["z" * (HOLD - len(SECRET) + 1), SECRET, " and on\n"])
    _assert_masked(out, SECRET)
    assert out.endswith(REDACTED + " and on\n")
    assert out.count("z") == HOLD - len(SECRET) + 1


def test_two_overlapping_values_in_a_line_past_hold_are_masked_whole():
    for v in (FIRST, SECOND):
        secret_redaction.remember(v)
    joined = FIRST + SECOND[2:]  # they share "J5"
    out = _through(["z" * (HOLD - 5), joined, " and on\n"])
    _assert_masked(out, FIRST, SECOND)
    assert out.endswith(REDACTED + " and on\n")


def test_two_overlapping_values_are_masked_together_in_a_short_line():
    for v in (FIRST, SECOND):
        secret_redaction.remember(v)
    out = _through(["key " + FIRST + SECOND[2:] + " end\n"])
    assert out == "key " + REDACTED + " end\n"


@pytest.mark.parametrize("seed", range(6))
def test_a_value_anywhere_around_the_hold_boundary_is_masked(seed):
    rng = random.Random(seed)
    secret_redaction.remember(SECRET)
    for _ in range(25):
        before = HOLD + rng.randint(-2 * len(SECRET), 2 * len(SECRET))
        writes = [_filler(rng, before), SECRET, _filler(rng, rng.randint(0, 40)) + "\n"]
        if rng.random() < 0.5:  # the value itself written in pieces
            writes[1:2] = _chunks(rng, SECRET, 5)
        _assert_masked(_through(writes), SECRET)


@pytest.mark.parametrize("seed", range(6))
def test_overlapping_values_around_the_hold_boundary_in_random_chunks_are_masked(seed):
    rng = random.Random(100 + seed)
    for v in (FIRST, SECOND, SECRET):
        secret_redaction.remember(v)
    pieces = [FIRST, SECOND, SECRET, FIRST + SECOND[2:], SECRET[:-1] + SECRET, FIRST[:-2] + SECOND]
    for _ in range(10):
        text = _filler(rng, HOLD + rng.randint(-60, 10))
        for _ in range(rng.randint(1, 4)):  # values next to each other or a few characters apart
            text += rng.choice(pieces) + _filler(rng, rng.choice([0, 0, 1, 3, 20]))
        text += _filler(rng, rng.randint(0, HOLD + 200)) + rng.choice(["", "\n"])
        _assert_masked(_through(_chunks(rng, text, rng.choice([7, 300, 9000, HOLD]))), FIRST, SECOND, SECRET)


@pytest.mark.parametrize("seed", range(4))
def test_random_values_in_very_long_lines_and_random_chunks_are_masked(seed):
    rng = random.Random(500 + seed)
    alphabet = string.ascii_uppercase + string.digits
    values = ["".join(rng.choice(alphabet) for _ in range(rng.randint(MIN_CHARS + 4, 40))) for _ in range(5)]
    for v in values:
        secret_redaction.remember(v)
    text = ""
    while len(text) < 3 * HOLD:
        text += _filler(rng, rng.randint(0, 5000)) + rng.choice(values)
        if rng.random() < 0.1:
            text += "\n"
    out = _through(_chunks(rng, text, rng.choice([1, 50, 4000, HOLD + 7])))
    _assert_masked(out, *values)
    assert out.replace(REDACTED, "").replace("\n", "").strip(string.ascii_lowercase + " ") == ""


def test_text_without_remembered_values_goes_through_unchanged_in_parts():
    rng = random.Random(7)
    secret_redaction.remember(SECRET)
    text = _filler(rng, 3 * HOLD) + "\n" + _filler(rng, 100)
    assert _through(_chunks(rng, text, 5000)) == text


# ---- encoded forms


def _encoded(value: str) -> dict[str, str]:
    quoted = quote(value, safe="")
    return {
        "json": json.dumps(value)[1:-1],
        "json kept": json.dumps(value, ensure_ascii=False)[1:-1],
        "json upper": json.dumps(value)[1:-1].replace("\\u00e9", "\\u00E9"),
        "percent": quoted,
        "percent lower": quoted.replace("%C3%A9", "%c3%a9"),
        "ascii": ascii(value)[1:-1],
    }


@pytest.mark.parametrize("form", ["json", "json kept", "json upper", "percent", "percent lower", "ascii"])
def test_an_encoded_value_is_masked_in_the_stream(form):
    secret_redaction.remember(ACCENTED)
    shown = _encoded(ACCENTED)[form]
    assert shown != ACCENTED or form == "json kept"
    out = _through([f"payload {shown} done\n"])
    assert out == f"payload {REDACTED} done\n"


@pytest.mark.parametrize("form", ["json", "json kept", "json upper", "percent", "percent lower", "ascii"])
def test_an_encoded_value_is_masked_by_the_log_formatter(form):
    secret_redaction.remember(ACCENTED)
    shown = _encoded(ACCENTED)[form]
    record = logging.LogRecord("fabric_desktop.x", logging.INFO, __file__, 1, "sent %s", (shown,), None)
    assert server._SafeFormatter("%(message)s").format(record) == f"sent {REDACTED}"


def test_whole_json_documents_and_urls_with_a_value_are_masked():
    secret_redaction.remember(ACCENTED)
    body = json.dumps({"note": ACCENTED}) + " " + json.dumps({"note": ACCENTED}, ensure_ascii=False)
    url = "https://example.invalid/cb?state=" + quote(ACCENTED) + "&x=1"
    out = _through([body + " " + url + "\n"])
    assert ACCENTED not in out and quote(ACCENTED) not in out and json.dumps(ACCENTED) not in out
    assert out.count(REDACTED) == 3


@pytest.mark.parametrize("seed", range(4))
def test_encoded_values_split_across_writes_past_hold_are_masked(seed):
    rng = random.Random(900 + seed)
    secret_redaction.remember(ACCENTED)
    forms = list(_encoded(ACCENTED).values())
    for _ in range(10):
        form = rng.choice(forms)
        text = _filler(rng, HOLD + rng.randint(-len(form), len(form))) + form + _filler(rng, 30) + "\n"
        out = _through(_chunks(rng, text, rng.choice([3, 64, HOLD])))
        _assert_masked(out, form)


def test_the_log_formatter_masks_overlapping_values_together():
    for v in (FIRST, SECOND):
        secret_redaction.remember(v)
    record = logging.LogRecord("fabric_desktop.x", logging.INFO, __file__, 1, "%s", (FIRST + SECOND[2:],), None)
    assert server._SafeFormatter("%(message)s").format(record) == REDACTED


# ---- speed: the engine's stderr goes through this


def test_ten_megabytes_with_fifty_values_goes_through_quickly(monkeypatch):
    # Times the masking of remembered values; the credential patterns (redact.secrets) run on every
    # line whether or not values are remembered, and are timed by their own tests.
    from fabric_desktop import redact
    monkeypatch.setattr(redact, "secrets", lambda text: text)
    rng = random.Random(42)
    alphabet = string.ascii_uppercase + string.digits
    values = ["".join(rng.choice(alphabet) for _ in range(rng.randint(12, 48))) for _ in range(50)]
    for v in values:
        secret_redaction.remember(v)
    line = _filler(rng, 1000)
    lines = [line[: rng.randint(40, 1000)] + (rng.choice(values) if i % 50 == 0 else "") + "\n" for i in range(400)]
    block = "".join(lines)
    out = io.StringIO()
    stream = RedactedStream(out)
    written = 0
    started = time.perf_counter()
    while written < 10 * 1024 * 1024:
        for piece in _chunks(rng, block, 8192):
            stream.write(piece)
        written += len(block)
    stream.write(_filler(rng, 3 * HOLD))  # and one very long line
    stream.flush()
    elapsed = time.perf_counter() - started
    assert elapsed < 5, elapsed
    shown = out.getvalue()
    assert not [v for v in values if v in shown]
    assert shown.count(REDACTED) == (written // len(block)) * sum(1 for x in lines if x.count(REDACTED) == 0
                                                                   and any(v in x for v in values))


# ---- memory: a long value, such as a certificate's key, is remembered without storing its starts


def _long_multiline(rng: random.Random, size: int) -> str:
    alphabet = string.ascii_uppercase + string.digits + "+/"
    body = "".join(rng.choice(alphabet) for _ in range(size))
    return "\n".join(body[i:i + 64] for i in range(0, size, 64))


def _naive_tail(text: str, forms: set[str]) -> int:
    for n in range(min(len(text), max(map(len, forms)) - 1), 0, -1):
        if any(len(f) > n and f.startswith(text[-n:]) for f in forms):
            return n
    return 0


def test_an_eight_kilobyte_multiline_value_is_remembered_in_little_memory():
    rng = random.Random(8)
    values = [_long_multiline(rng, 8192) for _ in range(2)]
    tracemalloc.start()
    try:
        for value in values:
            secret_redaction.remember(value)
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert peak < 20 * 1024 * 1024, peak
    out = _through(["key: ", values[0][:3000], values[0][3000:], "\n", repr(values[1]), "\n"])
    # Every 16 characters of either value are its own random run; none of them may show.
    pieces = {v[i:i + 16] for v in values for i in range(0, len(v) - 16, 16)}
    assert out.count(REDACTED) == 2 and not [p for p in pieces if p in out]


@pytest.mark.parametrize("seed", range(3))
def test_partial_tail_and_safe_cut_stay_exact_with_a_long_value(seed):
    rng = random.Random(seed)
    value = _long_multiline(rng, 8192)
    secret_redaction.remember(value)
    forms = secret_redaction._forms(value)
    form = rng.choice(sorted(forms))
    for k in [1, 2, 3, 4, 63, 64, 65, rng.randint(5, len(form) - 2), len(form) - 1]:
        text = _filler(rng, 50) + form[:k]
        assert secret_redaction.partial_tail(text) == _naive_tail(text, forms) == k
        assert secret_redaction.safe_cut(text, len(text)) == 50
    whole = _filler(rng, 50) + form + _filler(rng, 50)
    assert secret_redaction.partial_tail(whole) == 0
    for cut in (51, 50 + len(form) // 2, 50 + len(form) - 1):
        assert secret_redaction.safe_cut(whole, cut) == 50
    assert secret_redaction.safe_cut(whole, 50 + len(form) + 10) == 50 + len(form) + 10
