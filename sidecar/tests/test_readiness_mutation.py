"""Home's Ready to work checklist (readiness.py): the clock and free-space limits at their edges, how
the clock row words minutes, hours, ahead and behind, the workspace count's wording, how a data folder
on a network drive is told apart on Windows, and the write test's time. Mutation batch 61."""

import types

import pytest

from fabric_desktop import readiness

GIB = 1024 ** 3


def test_the_limits_are_five_minutes_and_two_gigabytes():
    assert readiness.CLOCK_SLACK_SECONDS == 300 and readiness.LOW_SPACE_BYTES == 2 * GIB
    assert readiness._DRIVE_REMOTE == 4


# ---- the clock row

@pytest.mark.parametrize("seconds", [300, -300, 0, 12.5])
def test_a_clock_within_five_minutes_is_ok(seconds):
    row = readiness.clock_check({"seconds": seconds, "host": "login.microsoftonline.com"})
    assert row["status"] == "ok" and row["text"] == "Within 5 minutes of login.microsoftonline.com's."


def test_a_clock_with_no_host_names_the_server():
    assert readiness.clock_check({"seconds": 1})["text"] == "Within 5 minutes of the server's."
    assert readiness.clock_check({"seconds": 1, "host": ""})["text"] == "Within 5 minutes of the server's."


@pytest.mark.parametrize("seconds, start", [
    (301, "This PC's clock is 5 minutes ahead of h's."),
    (-301, "This PC's clock is 5 minutes behind h's."),
    (119 * 60, "This PC's clock is 119 minutes ahead of h's."),
    (120 * 60, "This PC's clock is 2 hours ahead of h's."),
    (-150 * 60, "This PC's clock is 2 hours behind h's."),
    (-210 * 60, "This PC's clock is 4 hours behind h's."),
])
def test_a_clock_further_off_warns_in_minutes_then_hours(seconds, start):
    row = readiness.clock_check({"seconds": seconds, "host": "h"})
    assert row["status"] == "warn" and row["key"] == "clock" and row["text"].startswith(start)
    assert row["doc"] == "troubleshooting.md#this-pcs-clock-is-wrong"


@pytest.mark.parametrize("offset", [None, {}, {"seconds": "5"}, {"seconds": None}])
def test_no_clock_reading_gives_no_row(offset):
    assert readiness.clock_check(offset) is None


# ---- the workspace count

def test_one_workspace_is_singular_and_none_offers_the_workspaces_page():
    rows = {r["key"]: r for r in readiness.checks("pat", lambda: [{"id": "a"}], list, {})}
    assert rows["fabric"]["text"] == "You can see 1 workspace." and rows["fabric"]["status"] == "ok"
    assert rows["fabric"]["fix"] == "" and rows["fabric"]["doc"] == ""
    rows = {r["key"]: r for r in readiness.checks("pat", lambda: [], list, {})}
    assert rows["fabric"]["status"] == "warn" and rows["fabric"]["fix"] == "workspaces"
    assert rows["fabric"]["doc"] == "user-guide.md#workspaces"


# ---- free space

def _folder(tmp_path, free):
    return readiness.data_folder_check(tmp_path, free=lambda p: free, network=lambda p: False)


def test_exactly_two_gigabytes_free_is_enough(tmp_path):
    row = _folder(tmp_path, 2 * GIB)
    assert row["status"] == "ok" and row["text"] == f"{tmp_path}, with 2 GB free."


def test_less_than_two_gigabytes_free_warns_with_the_amount(tmp_path):
    row = _folder(tmp_path, 2 * GIB - 1)
    assert row["status"] == "warn" and row["text"].startswith(f"Only 2.0 GB is free on the drive that holds {tmp_path}.")
    assert _folder(tmp_path, int(1.24 * GIB))["text"].startswith("Only 1.2 GB is free")
    assert _folder(tmp_path, int(1.251 * GIB))["text"].startswith("Only 1.3 GB is free")  # GiB, not 1025 ** 3
    assert _folder(tmp_path, int(100.6 * GIB))["text"] == f"{tmp_path}, with 101 GB free."


# ---- a data folder on a network drive

class _Kernel32:
    def __init__(self, kind):
        self.kind, self.asked = kind, []

    def GetDriveTypeW(self, root):
        self.asked.append(root)
        if isinstance(self.kind, Exception):
            raise self.kind
        return self.kind


@pytest.fixture
def windows(monkeypatch):
    import ctypes

    def use(kind):
        k = _Kernel32(kind)
        monkeypatch.setattr(ctypes, "windll", types.SimpleNamespace(kernel32=k), raising=False)
        return k
    monkeypatch.setattr(readiness, "sys", types.SimpleNamespace(platform="win32"))
    return use


def test_a_share_path_is_on_the_network_on_any_os():
    assert readiness._on_network("\\\\server\\share\\data") is True
    assert readiness._on_network("//server/share/data") is True


def test_a_mapped_drive_is_asked_about_by_its_root(windows):
    k = windows(4)
    assert readiness._on_network("Z:\\Users\\pat\\AppData") is True and k.asked == ["Z:\\"]
    k = windows(4)
    assert readiness._on_network("Z:") is True and k.asked == ["Z:\\"]  # the drive alone
    k = windows(3)  # a fixed disk
    assert readiness._on_network("C:\\Users\\pat\\AppData") is False and k.asked == ["C:\\"]


@pytest.mark.parametrize("path", ["C", "data", "ab\\c"])
def test_a_path_without_a_drive_letter_is_not_asked_about(windows, path):
    k = windows(4)
    assert readiness._on_network(path) is False and k.asked == []


def test_a_drive_that_cant_be_asked_about_is_on_this_pc(windows):
    windows(OSError("no"))
    assert readiness._on_network("Z:\\data") is False


def test_off_windows_a_drive_letter_is_not_asked_about(monkeypatch):
    monkeypatch.setattr(readiness, "sys", types.SimpleNamespace(platform="linux"))
    assert readiness._on_network("Z:\\data") is False


# ---- the write test's time

class _Store:
    def __init__(self):
        self.files = {}

    def upload(self, ws, lh, path, data):
        self.files[path] = data

    def read_bytes(self, ws, lh, path):
        return self.files.get(path)

    def delete(self, ws, lh, path):
        self.files.clear()


def test_the_write_test_reports_how_long_it_took(monkeypatch):
    ticks = iter([100.0, 102.25])
    monkeypatch.setattr(readiness, "time", types.SimpleNamespace(time=lambda: 1_700_000_000,
                                                                 monotonic=lambda: next(ticks)))
    store = _Store()
    row = readiness.write_test(store, "ws", "lh", "Sales - Dev")
    assert row["status"] == "ok" and row["text"] == "Wrote, read and deleted a test file in 2.2 s."
    assert store.files == {}
