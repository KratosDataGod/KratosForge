"""Secret values are shown masked in every view: the Variables pane's rows and an opened variable
(variable_detail), in both kernels, for a few hundred seeded values that hold a value getSecret read.

The values (secret_masking_cases.py) hold a secret nested three or more levels deep in lists, dicts,
tuples and sets, in an object's own __repr__ or __str__, as a dict key, as bytes, in an exception's
args or a chained exception's text, cut by the preview's length limit, and joined from two parts.
Neither the secret, its bytes as printed, nor any part of it secret_redaction.MIN_CHARS long shows,
and each row and opened variable still answers."""

from __future__ import annotations

import random

import pytest
from secret_masking_cases import PRELUDE, cases, fake_vault, make_secret, shown

from fabric_desktop import http_session
from fabric_desktop.kernel_proc import ProcessKernel
from fabric_desktop.notebook import Kernel

SEEDS = {"engine": 4221, "process": 4222}
CASES = 260


@pytest.fixture(params=["engine", "process"])
def kernel(request):
    original = http_session.session  # the engine kernel's fake vault replaces it in this process
    k = Kernel(token=lambda audience: "tok") if request.param == "engine" else ProcessKernel(token=lambda audience: "tok")
    yield request.param, k
    http_session.session = original
    if request.param == "process":
        k.close()


def setup(k, seed, outer=None):
    rnd = random.Random(seed)
    secrets = {f"app-key-{i}": make_secret(rnd, accent=i == 3) for i in range(5)}
    names = [f"s{i}" for i in range(len(secrets))]
    generated = cases(seed, names, CASES)
    if outer:  # and as many again, each past the first items a preview reads, so the variable opens
        generated += cases(seed + 1, names, CASES, outer)
    assert k.run(fake_vault(secrets))["status"] == "ok"
    code = PRELUDE + "".join(f"v{i:03d} = {expr}\n" for i, (_, expr) in enumerate(generated))
    out = k.run(code)
    assert out["status"] == "ok", [o.get("evalue") for o in out["outputs"]]
    return list(secrets.values()), generated


def test_variables_pane_rows_show_every_secret_masked(kernel):
    which, k = kernel
    secrets, generated = setup(k, SEEDS[which])
    rows = {r["name"]: r for r in k.variables()}
    assert {f"v{i:03d}" for i in range(len(generated))} <= set(rows)
    found = {name: shown(row, secrets) for name, row in rows.items()}
    assert [(n, generated[int(n[1:])][0] if n.startswith("v") else n, f) for n, f in found.items() if f] == []
    for name, row in rows.items():
        assert isinstance(row["value"], str) and row["value"], (name, row)
    for i in range(len(secrets)):
        assert rows[f"s{i}"]["value"] == "hidden"
    hidden = sum(rows[f"v{i:03d}"]["value"] == "hidden" for i in range(len(generated)))
    assert hidden >= len(generated) * 0.6, hidden  # most hold it within what a preview reads


def test_an_opened_variable_shows_every_secret_masked(kernel):
    which, k = kernel
    secrets, generated = setup(k, SEEDS[which] + 100, "long_list" if which == "engine" else "long_dict_key")
    problems = []
    opened = masked = 0
    for i, (kind, _) in enumerate(generated):
        detail = k.variable_detail(f"v{i:03d}")
        found = shown(detail, secrets)
        if found:
            problems.append((i, kind, found))
            continue
        assert detail.get("name") == f"v{i:03d}" and not detail.get("missing"), detail
        assert detail.get("hidden") is True or "items" in detail or detail.get("type") == "str", detail
        if "items" in detail:
            opened += 1
            assert all(item["value"] for item in detail["items"]), detail
            masked += any(item["value"] == "hidden" for item in detail["items"])
    assert problems == []
    assert opened >= CASES * 0.8 and masked >= CASES * 0.5, (opened, masked)
