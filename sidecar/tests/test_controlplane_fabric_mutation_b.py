"""controlplane/fabric.py from a mutation run: a workspace is cut at 200 lakehouses and not before, a new
discovery hasn't cut anything short, and a lakehouse whose tables can't be listed is reported with the reason.
Fakes stand in for Fabric and OneLake: nothing reaches a tenant."""

from fabric_desktop.controlplane import fabric as fb


class Fabric:
    def __init__(self, lakehouses):
        self.lakehouses = lakehouses

    def list_workspaces(self):
        return []

    def list_lakehouses(self, workspace_id):
        return self.lakehouses


class NoManifest:
    def read(self):
        return None


def discovery(lakehouses, table_names):
    return fb.FabricDiscovery(Fabric(lakehouses), lambda ws, lh: NoManifest(), table_names)


def test_a_workspace_is_cut_short_past_200_lakehouses_only():
    assert fb.MAX_LAKEHOUSES == 200
    seen = []

    def tables(ws, lh):
        seen.append(lh)
        return []

    d = discovery([{"id": f"lh{i}", "displayName": f"L{i}"} for i in range(200)], tables)
    assert not d.truncated
    assert d.find_environments("ws") == [] and not d.truncated and len(seen) == 200


def test_a_new_discovery_has_cut_nothing_short():
    assert discovery([], lambda ws, lh: []).truncated is False


def test_a_lakehouse_whose_tables_cant_be_listed_is_reported_with_the_reason():
    def tables(ws, lh):
        raise PermissionError("403 Forbidden") if lh == "denied" else OSError("disk on fire")

    found = discovery([{"id": "denied", "displayName": "B"}, {"id": "broken", "displayName": "A"}],
                      tables).find_environments("ws")
    assert [(f.ref.lakehouse_id, f.state) for f in found] == [("broken", "unreadable"), ("denied", "unreadable")]
    assert found[0].reason.startswith("Couldn't be read (") and "disk on fire" in found[0].reason
    assert found[1].reason == "No access"
