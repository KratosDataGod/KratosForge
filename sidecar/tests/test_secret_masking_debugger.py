"""Secret values are shown masked in every view: the debugger's Evaluate answers, its watches, the error
text an evaluation shows, and the paused snapshot holding them, in both kernels, for a few hundred
seeded expressions over values that hold a value getSecret read (secret_masking_cases.py).

Neither the secret, its bytes as printed, nor any part of it secret_redaction.MIN_CHARS long shows,
and each evaluation still answers with a value or an error."""

from __future__ import annotations

import random
import time

import pytest
from secret_masking_cases import PRELUDE, cases, fake_vault, make_secret, shown

from fabric_desktop import http_session
from fabric_desktop.kernel_proc import ProcessKernel
from fabric_desktop.notebook import Kernel

SEEDS = {"engine": 4231, "process": 4232}
VALUES = 120
ROUNDS = 20

# Functions the expressions call: errors carrying the value in their args, and a chained one whose
# message holds the first error's text.
HELPERS = '''
def _boom(v):
    raise ValueError('could not use', v)
def _boom_text(v):
    raise RuntimeError('could not use ' + repr(v))
def _boom_chained(v):
    try:
        raise KeyError(v)
    except KeyError as inner:
        raise LookupError('while reading: ' + str(inner)) from inner
'''

TEMPLATES = ["{v}", "repr({v})", "str({v})", "[{v}, 1]", "{{'k': {v}}}", "({v},)", "_boom({v})", "_boom_text({v})",
             "_boom_chained({v})", "{{}}[repr({v})]", "getattr(object(), str({v}))", "_join(repr({v})[:7], repr({v})[7:])",
             "_joined([str({v})[:5], '', str({v})[5:]])", "Shown({v})", "Quoted([{v}])", "1 / 0 if {v} is not None else 0"]


@pytest.fixture(params=["engine", "process"])
def kernel(request):
    original = http_session.session  # the engine kernel's fake vault replaces it in this process
    k = Kernel(token=lambda audience: "tok") if request.param == "engine" else ProcessKernel(token=lambda audience: "tok")
    yield request.param, k
    http_session.session = original
    if request.param == "process":
        k.close()


def wait(k, timeout=30):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        snap = k.poll(-1, 0.3)
        if snap.get("paused") or snap["status"] != "running":
            return snap
    raise AssertionError("the cell neither paused nor finished")


def paused(k, seed):
    rnd = random.Random(seed)
    secrets = {f"app-key-{i}": make_secret(rnd) for i in range(4)}  # ASCII secrets
    generated = cases(seed, [f"s{i}" for i in range(len(secrets))], VALUES)
    assert k.run(fake_vault(secrets))["status"] == "ok"
    code = PRELUDE + HELPERS + "".join(f"v{i:03d} = {expr}\n" for i, (_, expr) in enumerate(generated)) + "done = 1"
    k.start(code, breakpoints=[code.count("\n") + 1])
    snap = wait(k)
    assert snap.get("paused"), snap.get("status")
    return list(secrets.values()), generated, rnd, snap


def test_evaluations_and_their_errors_show_every_secret_masked(kernel):
    which, k = kernel
    secrets, generated, rnd, snap = paused(k, SEEDS[which])
    assert shown(snap, secrets) is None
    problems, answered, errors = [], 0, 0
    for i in range(len(generated)):
        for template in rnd.sample(TEMPLATES, 3):
            expr = template.format(v=f"v{i:03d}")
            result = k.debug("eval", expr)
            found = shown(result, secrets)
            if found:
                problems.append((i, generated[i][0], template, found))
                continue
            assert "value" in result or "error" in result, (expr, result)
            answered += 1
            errors += "error" in result
    assert problems == []
    assert answered >= len(generated) * 3 and errors, (answered, errors)
    snap = k.poll(-1, 0.1)
    assert snap["paused"]["evals"] and shown(snap, secrets) is None  # the evaluations a pause keeps
    k.debug("continue")
    assert shown(wait(k), secrets) is None


def test_watches_show_every_secret_masked(kernel):
    which, k = kernel
    secrets, generated, rnd, snap = paused(k, SEEDS[which] + 7)
    rows = {r["name"]: r for r in snap["paused"]["variables"]}
    assert sum(r["value"] == "hidden" for r in rows.values()) >= len(generated) // 2
    k.debug("continue")
    wait(k)
    masked = 0
    # The values stay in the namespace: each round pauses a short cell with 10 watches, inside a function
    # so the pause has few locals to describe.
    for _ in range(ROUNDS):
        picked = rnd.sample(range(len(generated)), 10)
        watches = [rnd.choice(TEMPLATES).format(v=f"v{i:03d}") for i in picked]
        k.start("def _round():\n    return 2\n_round()", breakpoints=[2], watches=watches)
        snap = wait(k)
        found = shown(snap, secrets)
        assert found is None, (picked, found)
        p = snap["paused"]
        assert [w["expr"] for w in p["watches"]] == watches
        assert all(w.get("value") or w.get("error") for w in p["watches"]), p["watches"]
        masked += sum(w.get("value") == "hidden" or "[REDACTED]" in w.get("error", "") for w in p["watches"])
        k.debug("continue")
        wait(k)
    assert masked >= ROUNDS * 3, masked
