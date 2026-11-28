"""free_memory.py paths that mutation testing (batch 59, docs/quality/mutation-testing.md) showed no test
pinned: a control-group path with a colon in it, the exact value that means "no limit" on cgroup v1, and
the budget when the free memory's share equals the share asked for, or falls between them."""

from __future__ import annotations

import io
from types import SimpleNamespace

from fabric_desktop import free_memory as fm

MB = 1 << 20
MEMINFO = "MemTotal: 16000000 kB\nMemAvailable: 8388608 kB\n"  # 8 GiB free


def serve(monkeypatch, files):
    def fake_open(path, encoding=None):
        if path not in files:
            raise FileNotFoundError(path)
        return io.StringIO(files[path])

    monkeypatch.setattr(fm, "open", fake_open, raising=False)
    monkeypatch.setattr(fm, "os", SimpleNamespace(name="posix"))


def test_a_control_group_path_may_hold_a_colon(monkeypatch):
    serve(monkeypatch, {"/proc/meminfo": MEMINFO, "/proc/self/cgroup": "0::/app:one\n",
                        "/sys/fs/cgroup/app:one/memory.max": str(1024 * MB),
                        "/sys/fs/cgroup/app:one/memory.current": str(24 * MB)})
    assert fm.available() == 1000 * MB


def test_a_limit_of_exactly_2_to_the_60_means_none_and_one_less_is_a_limit(monkeypatch):
    v1 = "4:memory:/c\n"
    files = {"/proc/self/cgroup": v1, "/sys/fs/cgroup/memory/c/memory.usage_in_bytes": "0",
             "/sys/fs/cgroup/memory/c/memory.limit_in_bytes": str(1 << 60)}
    serve(monkeypatch, files)
    assert fm._group_free() is None
    files["/sys/fs/cgroup/memory/c/memory.limit_in_bytes"] = str((1 << 60) - 1)
    assert fm._group_free() == (1 << 60) - 1


def test_when_three_quarters_of_free_equals_the_share_the_share_sets_the_cap():
    got = fm.budget(3072, 4096 * MB)  # 3/4 of 4096 MB is 3072 MB
    assert got == {"cap_mb": 3072, "duckdb_mb": 1536, "bound": "share", "free_mb": 4096}


def test_a_share_between_three_quarters_of_free_and_all_of_it_is_cut_to_three_quarters():
    got = fm.budget(3000, 3072 * MB)  # 3/4 of 3072 MB is 2304 MB, under the 3000 MB share
    assert got == {"cap_mb": 2304, "duckdb_mb": 1152, "bound": "free", "free_mb": 3072}
