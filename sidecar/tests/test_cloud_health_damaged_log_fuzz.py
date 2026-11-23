"""Table health (meta_health) with a local table whose _delta_log is damaged.

One table's log torn, missing its first commit, needing a reader feature this PC doesn't read, or with a
broken checkpoint, used to raise out of meta_health and take the whole table-health page with it. Now that
table shows as unreadable, with a plain sentence that says why, and every other table still reports."""

from __future__ import annotations

import json
import random
import time
from pathlib import Path

import pytest
from deltalake import DeltaTable, write_deltalake

from test_cloud import CloudService, FakeFabric, svc  # noqa: F401 - the fixture is used by name

SEEDS = range(12)
DAMAGED = "gold.fact_sales"
KINDS = ["torn", "missing_first", "reader_feature", "checkpoint", "garbage", "dropline"]

pytestmark = pytest.mark.filterwarnings("ignore")


def _table(s) -> Path:
    project = s._project()
    store = s._store(project, "dev")
    e = project.entities[DAMAGED]
    return Path(store.uri(project.layer(e.layer).lakehouse, e.schema, e.table))


def _commits(t: Path) -> list[Path]:
    return sorted((t / "_delta_log").glob("*.json"))


def _damage(rng: random.Random, t: Path, kind: str) -> None:
    log = t / "_delta_log"
    cs = _commits(t)
    if kind == "torn":
        c = cs[-1]
        c.write_bytes(c.read_bytes()[: rng.randrange(1, max(2, len(c.read_bytes()) // 2))])
    elif kind == "missing_first":
        if len(cs) == 1:  # a later commit stays, so the folder is still a Delta table, one missing its start
            write_deltalake(str(t), DeltaTable(str(t)).to_pyarrow_table(), mode="append")
        cs[0].unlink()
    elif kind == "reader_feature":
        nxt = int(cs[-1].name[:20]) + 1
        (log / f"{nxt:020d}.json").write_text(json.dumps({"protocol": {
            "minReaderVersion": 3, "minWriterVersion": 7, "readerFeatures": ["qqq"], "writerFeatures": ["qqq"]}}) + "\n"
            + json.dumps({"commitInfo": {"timestamp": int(time.time() * 1000), "operation": "WRITE"}}) + "\n")
    elif kind == "checkpoint":
        DeltaTable(str(t)).create_checkpoint()
        cp = next(log.glob("*.checkpoint.parquet"))
        b = bytearray(cp.read_bytes())
        start = rng.randrange(4, len(b) // 2)
        b[start:start + rng.randint(20, 60)] = bytes(rng.randrange(256) for _ in range(len(b[start:start + 60])))
        cp.write_bytes(bytes(b))
        for c in cs:  # the checkpoint is now the only record of those versions
            c.unlink()
    elif kind == "garbage":
        rng.choice(cs).write_bytes(bytes(rng.randrange(256) for _ in range(rng.randint(1, 80))))
    else:  # a commit's protocol or metaData line dropped
        c = cs[0]
        lines = [line for line in c.read_text().splitlines() if not line.startswith(rng.choice(['{"protocol"', '{"metaData"']))]
        c.write_text("\n".join(lines) + "\n")


def _statuses(h: dict) -> dict[str, str]:
    return {t["entity"]: t["status"] for t in h["tables"] if t["entity"] != DAMAGED}


def _check(h: dict, before: dict[str, str], kinds: list[str]) -> None:
    rows = {t["entity"]: t for t in h["tables"]}
    assert before and _statuses(h) == before, kinds  # every other table still reports, as it did
    bad = rows[DAMAGED]
    if bad["status"] == "unreadable":
        assert bad["exists"] is False and bad["advice"] == [], kinds
        msg = bad["message"]
        assert "because" in msg and "Kernel error" not in msg and ": " not in msg, (kinds, msg)
    else:  # a damage delta-rs still reads past (a garbage line it skips) is reported as usual
        assert bad["status"] in ("healthy", "needs_maintenance", "missing"), (kinds, bad)


@pytest.mark.parametrize("kind", ["torn", "missing_first", "reader_feature", "checkpoint"])
def test_one_damaged_local_table_shows_unreadable_and_the_others_still_report(svc, kind):  # noqa: F811
    before = _statuses(svc.meta_health("dev"))
    _damage(random.Random(0), _table(svc), kind)
    h = svc.meta_health("dev")
    bad = next(t for t in h["tables"] if t["entity"] == DAMAGED)
    assert bad["status"] == "unreadable"
    _check(h, before, [kind])


@pytest.mark.parametrize("seed", SEEDS)
def test_seeded_log_damage_never_breaks_table_health(svc, seed):  # noqa: F811
    rng = random.Random(seed)
    t = _table(svc)
    before = _statuses(svc.meta_health("dev"))
    kinds = [rng.choice(KINDS) for _ in range(rng.randint(1, 2))]
    for kind in kinds:
        if kind == "checkpoint" and not _commits(t):
            continue
        try:
            _damage(rng, t, kind)
        except Exception:  # noqa: BLE001 - an earlier damage already broke what this one needs
            pass
    _check(svc.meta_health("dev"), before, kinds)
