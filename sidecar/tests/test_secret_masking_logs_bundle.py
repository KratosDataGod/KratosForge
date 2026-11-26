"""Secret values are shown masked in every view: log lines (the engine's stderr through RedactedStream,
its log records through _SafeFormatter) and the support bundle (support_bundle._files, the zip
create() writes and preview()), for a few hundred seeded lines holding a remembered secret
(secret_masking_cases.py): nested in containers, in an object's repr, as a dict key, as bytes, in an
exception's args and a chained exception's traceback, written in pieces, and cut by the bundle's
tail.

Neither the secret, its bytes as printed, nor any part of it secret_redaction.MIN_CHARS long shows,
and each output is still written, with [REDACTED] where the secret was."""

from __future__ import annotations

import io
import json
import logging
import random
import traceback
import zipfile

import pytest
from secret_masking_cases import PRELUDE, cases, make_secret, shown

from fabric_desktop import secret_redaction, server, support_bundle

LINES = 300


def remembered(seed, accent=True):
    rnd = random.Random(seed)
    secrets = [make_secret(rnd, accent=accent and i == 2) for i in range(4)]
    for s in secrets:
        secret_redaction.remember(s)
    return rnd, secrets


def values(seed, secrets, n):
    """The seeded cases, made in this process: (kind, value)."""
    space = {f"s{i}": s for i, s in enumerate(secrets)}
    exec(PRELUDE, space)  # noqa: S102 - the test's own helper classes
    return [(kind, eval(expr, space)) for kind, expr in cases(seed, list(space)[:len(secrets)], n)]  # noqa: S307


def chained_traceback(value):
    try:
        try:
            raise KeyError("missing", value)
        except KeyError as inner:
            raise RuntimeError(f"while reading {value!r}") from inner
    except RuntimeError as exc:
        return exc


def lines(rnd, generated):
    """Log lines as code writes them: a value's repr or str in a message, or a chained traceback."""
    out = []
    for _, value in generated:
        shape = rnd.randrange(4)
        if shape == 0:
            out.append(f"ERROR reading failed: {value!r}")
        elif shape == 1:
            out.append(f"INFO {rnd.choice(['got', 'kept', 'sent'])} {value}")
        elif shape == 2 and "\\" not in repr(value):  # a JSON line, of a text without escapes of its own
            out.append(json.dumps({"level": "warning", "detail": repr(value)}, ensure_ascii=False))
        elif shape == 2:
            out.append(f"WARNING detail={value!r}")
        else:
            out.append("".join(traceback.format_exception(chained_traceback(value))).rstrip("\n"))
    return out


def test_the_engines_stderr_shows_every_secret_masked_however_it_is_written():
    rnd, secrets = remembered(4251)
    text = "\n".join(lines(rnd, values(4251, secrets, LINES))) + "\n"
    for _ in range(6):  # the same text in pieces of random sizes, cut anywhere: inside a secret too
        raw = io.StringIO()
        stream = server._RedactedStream(raw)
        at = 0
        while at < len(text):
            step = rnd.choice([1, 2, 3, 5, 8, 13, 40, 200, 3000])
            stream.write(text[at:at + step])
            at += step
        stream.flush()
        out = raw.getvalue()
        assert shown(out, secrets) is None
        assert out.count("\n") == text.count("\n") and out.count("[REDACTED]") >= LINES


def test_log_records_show_every_secret_masked():
    rnd, secrets = remembered(4252)
    formatter = server._SafeFormatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    for i, (kind, value) in enumerate(values(4252, secrets, LINES)):
        args = (value,) if rnd.random() < 0.5 else (repr(value), str(value))
        msg = "failed: %s" if len(args) == 1 else "failed: %s (%s)"
        exc = chained_traceback(value)
        record = logging.LogRecord("fabric_desktop.notebook", logging.WARNING, __file__, i, msg, args,
                                   (type(exc), exc, exc.__traceback__) if i % 3 == 0 else None)
        out = formatter.format(record)
        assert shown(out, secrets) is None, (i, kind)
        assert "[REDACTED]" in out and "failed: " in out


@pytest.fixture
def bundle_folder(tmp_path, monkeypatch):
    monkeypatch.setattr(support_bundle, "about", lambda data_dir=None: {"version": "test"})
    data, agent = tmp_path / "data", tmp_path / "agent"
    (agent / "logs").mkdir(parents=True)
    data.mkdir()
    return data, agent


def test_the_support_bundle_shows_every_secret_masked(bundle_folder, tmp_path):
    data, agent = bundle_folder
    rnd, secrets = remembered(4253)
    written = lines(rnd, values(4253, secrets, LINES))
    (data / "sidecar.log").write_text("\n".join(written) + "\n", encoding="utf-8")
    for path in (data / "sidecar.prev.log", data / "app-crash.log", agent / "logs" / "agent.log",
                 agent / "logs" / "agent-crash.log"):  # each of the others gets some of them
        path.write_text("\n".join(rnd.sample(written, 25)) + "\n", encoding="utf-8")
    files = support_bundle._files(data, agent)
    assert {"sidecar.log", "sidecar.prev.log", "app-crash.log", "agent.log", "agent-crash.log"} <= set(files)
    assert shown(files, secrets) is None
    assert files["sidecar.log"].count("[REDACTED]") >= LINES and files["agent.log"].count("[REDACTED]") >= 25
    made = support_bundle.create(tmp_path / "out", data, agent)
    with zipfile.ZipFile(made) as z:
        inside = {name: z.read(name).decode("utf-8") for name in z.namelist()}
    assert inside and shown(inside, secrets) is None and "[REDACTED]" in inside["sidecar.log"]
    preview = support_bundle.preview(data, agent)
    assert preview["files"] and shown(preview, secrets) is None


def test_a_log_cut_by_the_bundles_tail_shows_no_part_of_a_secret(bundle_folder, monkeypatch):
    data, _ = bundle_folder
    rnd, secrets = remembered(4254)
    # More lines than a bundle keeps: the first ones are dropped whole, never cut inside a secret.
    many = [f"INFO {i} {rnd.choice(secrets)} kept" for i in range(support_bundle.LOG_LINES + 50)]
    (data / "sidecar.log").write_text("\n".join(many) + "\n", encoding="utf-8")
    tail = support_bundle._files(data, None)["sidecar.log"]
    assert shown(tail, secrets) is None and tail.count("[REDACTED] kept") == support_bundle.LOG_LINES
    # A log whose byte limit falls inside a secret: the line it cut is left out. With a shorter limit
    # than a bundle's, so each case is quick.
    monkeypatch.setattr(support_bundle, "TAIL_BYTES", 64 * 1024)
    monkeypatch.setattr(support_bundle._tail, "__defaults__", (support_bundle.LOG_LINES, support_bundle.TAIL_BYTES))
    secret = secrets[0]
    for cut in sorted(rnd.sample(range(1, len(secret)), 12)):
        after = f"INFO after {secrets[1]}\n"
        rest = support_bundle.TAIL_BYTES + cut - len(secret) - len(" end\n") - len(after) - 1
        text = "y" * 500 + secret + " end\n" + after + "w" * rest + "\n"
        (data / "sidecar.log").write_text(text, encoding="ascii")
        assert len(text) - support_bundle.TAIL_BYTES == 500 + cut  # the limit falls inside the secret
        tail = support_bundle._files(data, None)["sidecar.log"]
        assert shown(tail, secrets) is None and tail.startswith("INFO after [REDACTED]")
