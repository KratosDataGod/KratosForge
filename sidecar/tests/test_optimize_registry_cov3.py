"""optimize/registry.py: a connections file that can't be read, and forgetting a lakehouse that isn't active."""

from fabric_desktop.optimize import registry as reg


def _conn(lh):
    return {"workspace_id": "w", "lakehouse_id": lh, "lakehouse_name": lh.upper()}


def test_a_connections_file_that_cant_be_read_is_no_connections(tmp_path, monkeypatch):
    def broken(path, default):
        raise ValueError("not JSON")
    monkeypatch.setattr(reg.jsonfile, "read_json", broken)
    r = reg.Registry(tmp_path)
    assert r.all() == [] and r.active() is None


def test_forgetting_another_lakehouse_keeps_the_active_one(tmp_path):
    r = reg.Registry(tmp_path)
    r.add(_conn("a"))
    r.add(_conn("b"))  # b is active now
    r.forget("a")
    assert [c["lakehouse_id"] for c in r.all()] == ["b"]
    assert r.active()["lakehouse_id"] == "b"
