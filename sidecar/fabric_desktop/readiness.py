"""The "Ready to work" checklist on Home (FD-136): can this PC do real work yet, and if not, what to do.

``checks`` only reads. ``write_test`` is the one check that writes: one small file in a folder of
its own in a lakehouse's Files area, read back and deleted, only when the person asks, and never
in a workspace whose name says it is production.
"""

from __future__ import annotations

import platform
import re
import time
from typing import Any, Callable

CHECK_FOLDER = "Files/_fabric_desktop_check"
CHECK_FILE = f"{CHECK_FOLDER}/write-test.txt"
_PROD = re.compile(r"(?i)(?<![a-z])(prod|prd|production)(?![a-z])")


def _check(key: str, title: str, status: str, text: str, fix: str = "", doc: str = "") -> dict[str, Any]:
    """status: ok, fail, warn (works but worth knowing), or wait (needs an earlier check first)."""
    return {"key": key, "title": title, "status": status, "text": text, "fix": fix, "doc": doc}


def checks(user: str | None, list_workspaces: Callable[[], list[dict[str, Any]]],
           list_capacities: Callable[[], list[dict[str, Any]]], settings: dict[str, Any]) -> list[dict[str, Any]]:
    out = [_check("engine", "Local engine", "ok", f"Starts on this PC. Python {platform.python_version()}.")]
    if not user:
        out.append(_check("sign_in", "Signed in", "fail", "Not signed in, so Fabric can't be reached yet.",
                          "sign_in", "install.md#download-and-start"))
        out += [_check("fabric", "Fabric answers", "wait", "Checked once you sign in."),
                _check("capacity", "A workspace on a capacity", "wait", "Checked once you sign in.")]
    else:
        out.append(_check("sign_in", "Signed in", "ok", f"As {user}."))
        try:
            spaces = list_workspaces()
        except Exception as exc:  # noqa: BLE001 - said in the row, with the fix
            out.append(_check("fabric", "Fabric answers", "fail", f"Fabric didn't answer: {exc}",
                              "retry", "troubleshooting.md"))
            spaces = None
        if spaces is not None:
            n = len(spaces)
            out.append(_check("fabric", "Fabric answers", "ok" if n else "warn",
                              f"You can see {n} workspace{'s' if n != 1 else ''}." if n else
                              "You can't see any workspaces yet. Ask to be added to one, or create one.",
                              "" if n else "workspaces", "" if n else "user-guide.md#workspaces"))
            out.append(_capacity_check(spaces, list_capacities))
    out.append(_policy_check(settings))
    return out


def _capacity_check(spaces: list[dict[str, Any]], list_capacities: Callable[[], list[dict[str, Any]]]) -> dict[str, Any]:
    on_capacity = [w for w in spaces if w.get("capacityId")]
    if not on_capacity:
        return _check("capacity", "A workspace on a capacity", "fail",
                      f"None of your {len(spaces)} workspaces is on a Fabric capacity, so you can't create a lakehouse "
                      "yet. Notebooks and SQL on this PC still work.", "capacity", "azure-setup.md#5-plan-workspaces-and-capacity")
    try:
        active = {c.get("id", "").lower() for c in list_capacities() if c.get("state") == "Active"}
    except Exception:  # noqa: BLE001 - reading capacities needs a scope some tenants don't grant
        return _check("capacity", "A workspace on a capacity", "ok",
                      f"{len(on_capacity)} of your workspaces are on a capacity. Whether it is running isn't visible "
                      "to your sign-in.")
    running = [w for w in on_capacity if str(w["capacityId"]).lower() in active]
    if running:
        return _check("capacity", "A workspace on a capacity", "ok",
                      f"{len(running)} of your workspaces are on a running capacity.")
    return _check("capacity", "A workspace on a capacity", "fail",
                  "Your workspaces' capacities are paused or not running, so Fabric work can't start.",
                  "capacity", "azure-setup.md#5-plan-workspaces-and-capacity")


def _policy_check(settings: dict[str, Any]) -> dict[str, Any]:
    fixed = [k for k, v in settings.items() if isinstance(v, dict) and v.get("source") == "policy"]
    problems = settings.get("policy_problems") or []
    if problems:
        return _check("policy", "Your organization's settings", "warn",
                      f"Check the policy file: {'; '.join(problems)}", "settings", "azure-setup.md#4-push-settings-to-everyone")
    text = (f"Your organization sets {', '.join(sorted(fixed))}." if fixed
            else "No organization policy on this PC; you choose your own settings.")
    return _check("policy", "Your organization's settings", "ok", text)


def write_test_refusal(workspace_name: str) -> str | None:
    if _PROD.search(workspace_name or ""):
        return f"{workspace_name} looks like a production workspace, so the test doesn't write there. Pick a Dev or Test one."
    return None


def write_test(store: Any, workspace_id: str, lakehouse_id: str, workspace_name: str) -> dict[str, Any]:
    """Write one small file, read it back, delete the folder. ``store`` is a ``FileStore``."""
    refusal = write_test_refusal(workspace_name)
    if refusal:
        raise PermissionError(refusal)
    body = f"KratosForge write test {int(time.time())}\n".encode()
    started = time.monotonic()
    try:
        store.upload(workspace_id, lakehouse_id, CHECK_FILE, body)
        back = store.read_bytes(workspace_id, lakehouse_id, CHECK_FILE)
    finally:
        store.delete(workspace_id, lakehouse_id, CHECK_FOLDER)
    if back != body:
        return _check("write", "Writing to OneLake", "fail", "The file written didn't read back the same.",
                      "retry", "troubleshooting.md")
    return _check("write", "Writing to OneLake", "ok",
                  f"Wrote, read and deleted a test file in {time.monotonic() - started:.1f} s.")
