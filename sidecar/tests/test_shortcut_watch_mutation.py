"""shortcut_watch.py paths that mutation testing (batch 55, docs/quality/mutation-testing.md)
showed no test pinned: the folder answer reused for each shortcut in it, a folder that can't be
read, the pool size, a lakehouse named in a problem, and an environment's own workspace."""

from fabric_desktop import shortcut_watch


class InOrder:
    """Runs the checks one after another, so the second shortcut in a folder always finds the
    folder's answer already kept. Records how many workers the pool was asked for."""

    workers: list[int] = []

    def __init__(self, max_workers):
        InOrder.workers.append(max_workers)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def map(self, fn, items):
        return [fn(i) for i in items]


class Files:
    def __init__(self, status):
        self.status, self.calls = status, []

    def probe(self, ws, lh, directory):
        self.calls.append(directory)
        code = self.status.get(directory, 200)
        return code, "" if code < 400 else "Forbidden"


def item(name, folder="Tables/dbo"):
    return {"id": name, "lakehouse_id": "lh", "host_workspace_id": "ws", "path": folder,
            "full_path": f"{folder}/{name}", "target_type": "OneLake"}


def test_the_pool_has_eight_workers(monkeypatch):
    InOrder.workers = []
    monkeypatch.setattr(shortcut_watch, "ThreadPoolExecutor", InOrder)
    shortcut_watch.probe(Files({}), [item("a")])
    assert InOrder.workers == [8]


def test_a_folder_answer_is_reused_for_every_shortcut_in_it(monkeypatch):
    monkeypatch.setattr(shortcut_watch, "ThreadPoolExecutor", InOrder)
    files = Files({"Tables/dbo/b": 404})
    out = shortcut_watch.probe(files, [item("a"), item("b"), item("c")])
    assert files.calls == ["Tables/dbo", "Tables/dbo/a", "Tables/dbo/b", "Tables/dbo/c"]
    assert [o["health"] for o in out] == ["healthy", "broken", "healthy"]


def test_a_folder_that_cant_be_read_checks_none_of_its_shortcuts(monkeypatch):
    monkeypatch.setattr(shortcut_watch, "ThreadPoolExecutor", InOrder)
    for code in (401, 403):
        files = Files({"Tables/dbo": code})
        out = shortcut_watch.probe(files, [item("a"), item("b")])
        assert files.calls == ["Tables/dbo"], code  # listed once, and no shortcut in it is asked for
        assert [o["health"] for o in out] == ["not_checked", "not_checked"]
        assert all("can't read the lakehouse" in o["detail"] for o in out)


class Fabric:
    def __init__(self, lakehouses):
        self.lakehouses = lakehouses

    def list_lakehouses(self, ws):
        return self.lakehouses

    def list_shortcuts(self, ws, lh):
        raise RuntimeError("no access")


def test_a_lakehouse_that_cant_be_listed_is_named_in_the_problem():
    fabric = Fabric([{"id": "lh-1", "displayName": "Sales"}, {"id": "lh-2"}])
    rows, problems = shortcut_watch.scan(["ws"], fabric, fabric, Files({}))
    assert rows == []
    assert problems == ["lakehouse Sales: RuntimeError: no access", "lakehouse lh-2: RuntimeError: no access"]


def test_an_environments_own_workspace_is_kept():
    assert shortcut_watch.env_workspaces({"workspace_id": "own", "workspaces": {"gold": {"id": "b"}}}) == ["b", "own"]
    assert shortcut_watch.env_workspaces({"workspace_id": "own"}) == ["own"]

