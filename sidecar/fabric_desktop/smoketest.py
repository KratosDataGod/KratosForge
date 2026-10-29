"""The tenant smoke test of ten core flows (PB-002), on a workspace set aside for tests.

    fabric-desktop smoke-test --workspace <workspace id> --dry-run           # what it would do; writes nothing
    fabric-desktop smoke-test --workspace <workspace id> --yes --run-in-fabric

It signs in the way the app does (Microsoft's Fabric CLI client, no app registration) and runs the
parts of the ten flows in docs/product/smoke-test/README.md that work without the app's window.
Everything it writes goes through the live test (livetest.py) and its guards: the workspace must be
marked ``fabric-desktop-live-test`` and not look like production, and only ``fdlt_<run id>`` things
it created are deleted. The rest of each flow is a checklist a person ticks in the results file.

Results go to ``<out>/<run id>/smoke.json`` and ``summary.md``, with the live test's own results
under ``live/``. None of them holds a token.
"""

from __future__ import annotations

import json
import re
import tempfile
import uuid
import zipfile
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from . import __version__, fabric_api, livetest
from .auth import MICROSOFT_CLIENT_ID

SCHEMA = "smoke-test-result@1"
_TOKEN = re.compile(r"eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}")


@dataclass
class Flow:
    """One of the ten flows: what it proves, which features, and what a person checks by hand."""

    n: int
    title: str
    features: list[str]
    by_hand: list[str]
    live_checks: list[str] = field(default_factory=list)  # live-test check ids that belong to this flow


FLOWS = [
    Flow(1, "Install from the Preview release and start the engine", ["FEAT-SHL-01", "FEAT-SHL-09"],
         ["Install the latest Preview from the Releases page on a PC without KratosForge.",
          "Start the app: the engine starts, Home opens and Settings > About shows the version."]),
    Flow(2, "Sign in with Microsoft's Fabric CLI client, no app registration", ["FEAT-AUTH-01"],
         ["Sign in from the app (Windows sign-in, WAM) with Settings > Advanced > App ID empty.",
          "Sign out and in again; the account menu shows the right account."]),
    Flow(3, "List workspaces and open a lakehouse in the catalog", ["FEAT-FAB-02", "FEAT-ONE-01"],
         ["In Workspaces, open the test workspace and a lakehouse; its tables and files are listed."]),
    Flow(4, "Create a lakehouse; refuse an invalid name", ["FEAT-FAB-03"],
         ["Create > Lakehouse with the name 1 bad name: the app says why and suggests a valid name.",
          "Delete any lakehouse you created by hand."],
         ["fabric.create_lakehouse", "fabric.get_lakehouse"]),
    Flow(5, "Notebook on the PC writes a table to OneLake", ["FEAT-NB-02", "FEAT-ONE-05"],
         ["Create a notebook, run its cells on This PC, and write a small table to the test lakehouse.",
          "Open the table in the catalog: the rows are there."],
         ["onelake.files", "delta.write", "delta.read", "delta.append", "delta.merge", "delta.time_travel",
          "delta.concurrent"]),
    Flow(6, "Publish the notebook and Run in Fabric; see it in Monitor", ["FEAT-FAB-06", "FEAT-FAB-07"],
         ["Publish the notebook from flow 5 and choose Run in Fabric.",
          "Monitor > Runs shows the run and its outcome."],
         ["notebook.publish", "notebook.run"]),
    Flow(7, "Prove the notebook on the PC and in Fabric", ["FEAT-PRV-01"],
         ["Choose Prove on the notebook from flow 5; the verdict says both runs match, or what differs."]),
    Flow(8, "Metadata project: run Dev on the PC, promote to Test", ["FEAT-META-02", "FEAT-META-09", "FEAT-META-14"],
         ["Data mesh > Environments: run the Dev environment on This PC.",
          "Promote to Test, with Test mapped to the marked test workspace; the deploy preview lists what changes."]),
    Flow(9, "The live test: dry run, run, cleanup", ["FEAT-OPS-04", "FEAT-FAB-01"],
         ["Optional, for FEAT-AUTH-04: run fabric-desktop live-test --agent-config <agent.toml> as the agent's identity."],
         ["errors.not_found", "errors.bad_token", "pipeline.definition"]),
    Flow(10, "Report a problem and save a support bundle; nothing personal in either", ["FEAT-SHL-10", "FEAT-SHL-11"],
         ["Help > Report a problem: read the draft; it names no person, tenant or workspace."]),
]

# With --platform (PB-052): the central platform's flows (platformtest.py), after the ten.
PLATFORM_FLOWS = [
    Flow(11, "Central platform: Finish or repair once, from empty", ["FEAT-MESH-01"],
         ["Platform > Settings: the test workspace's platform shows as ready, with you as its admin."]),
    Flow(12, "Central platform: Finish or repair once, again or from a previous version", ["FEAT-MESH-01"],
         ["For the upgrade: before the run, Finish setup in the test workspace from the Stable build, then run "
          "the smoke test from the candidate with --platform-upgrade."]),
    Flow(13, "Central platform: register a data product", ["FEAT-MESH-04"],
         ["Data mesh > Products lists the fdlt_ product while the run keeps the platform (--platform-keep)."]),
    Flow(14, "Central platform: issue and look up surrogate keys", ["FEAT-MESH-02", "FEAT-META-05"], []),
    Flow(15, "Central platform: a policy exception, asked for, approved and ended", ["FEAT-GOV-03", "FEAT-GOV-08"],
         ["Data mesh > Requests: ask for an exception to a published policy, approve it as an admin, end it."]),
    Flow(16, "Central platform: restrict a central lakehouse family (central_restrict)", ["FEAT-ADM-11"],
         ["Optimize > Access > Who may read it: with a central lakehouse in the test workspace (OneLake security on, "
          "a lakehouse with schemas), pick the readers, confirm, and check the OneLake security role in Fabric."]),
]


@dataclass
class Step:
    flow: int
    id: str
    title: str
    status: str = "skipped"  # passed, failed, skipped
    detail: str = ""
    required: bool = True  # a step that isn't required is recorded but doesn't fail its flow


class SmokeTest:
    """One unattended run of the automated parts of the ten flows."""

    def __init__(self, workspace_id: str, credential: Any, fabric: Any, out_dir: Path,
                 live_test: Callable[[Path], Any] | None = None, run_in_fabric: bool = False,
                 support_bundle: Callable[[Path], Path] | None = None, run_id: str = "", client_id: str = "",
                 platform_test: Callable[[Path], Any] | None = None):
        self.workspace_id = workspace_id
        self.credential, self.fabric = credential, fabric
        self.run_id = run_id or uuid.uuid4().hex[:12]
        self.dir = Path(out_dir) / self.run_id
        self.run_in_fabric = run_in_fabric
        self.client_id = client_id  # an own app registration from Settings; flow 2 needs it empty or Microsoft's
        # Builds the LiveTest that does every write; the CLI passes the real one, tests a faked one.
        self.live_test = live_test
        self.support_bundle = support_bundle or _support_bundle
        # Builds the PlatformTest for the central platform's flows (--platform); None leaves them out.
        self.platform_test = platform_test
        self.steps: list[Step] = []
        self.live: dict[str, Any] = {}
        self.platform: dict[str, Any] = {}

    def plan(self) -> list[str]:
        return [
            "Flow 1: check the engine's version and that DuckDB, Polars, delta-rs and PyArrow load.",
            "Flow 2: sign in the way the app does and get tokens for Fabric and OneLake, then try Azure SQL "
            "(warehouses) and Kusto (Monitoring) and record the answer.",
            f"Flow 3: list your workspaces, check {self.workspace_id} is among them, and list its items.",
            "Flow 4: check the Fabric client's name check refuses the lakehouse name '1 bad name' (no request is sent).",
            "Flows 4, 5, 6 and 9: run the live test with every opt-in check"
            + (" and --run-in-fabric (spends Spark capacity)." if self.run_in_fabric
               else "; without --run-in-fabric the notebook is published and deleted again, but not run."),
            "Flow 10: save a support bundle and check it holds no token, user name, tenant id or workspace id.",
            *(["Flows 11-16 (--platform): make the central platform in the test workspace, use it and delete it again; "
               "see the platform plan below."] if self.platform_test else []),
            "Flows 7 and 8, and the by-hand part of every flow: listed in the summary for a person to tick.",
            f"Write the results to {self.dir}.",
        ]

    def _step(self, flow: int, id: str, title: str, fn: Callable[[], str | None], required: bool = True) -> Step:
        step = Step(flow, id, title, required=required)
        try:
            step.detail = fn() or ""
            step.status = "passed"
        except Exception as exc:  # every failure is a result, not a crash
            step.status = "failed"
            step.detail = livetest.scrub(f"{type(exc).__name__}: {exc}")
        self.steps.append(step)
        return step

    # ---- the run

    def run(self) -> dict[str, Any]:
        started = datetime.now(timezone.utc).isoformat(timespec="seconds")
        self._step(1, "engine", "The engine and its data libraries load", _engine)
        self._step(2, "auth.client", "Sign-in uses Microsoft's Fabric CLI client, not an own app registration",
                   self._client)
        fabric = self._step(2, "auth.fabric", "Token for the Fabric API", lambda: self._token("fabric_token"))
        self._step(2, "auth.onelake", "Token for OneLake", lambda: self._token("storage_token"))
        self._step(2, "auth.sql", "Token for Azure SQL (warehouse queries)", lambda: self._token("warehouse_token"),
                   required=False)
        self._step(2, "auth.kusto", "Token for Kusto (warehouse Monitoring)", lambda: self._token("kusto_token"),
                   required=False)
        if fabric.status == "passed":
            self._step(3, "catalog.workspaces", "The test workspace is in the workspace list", self._workspaces)
            self._step(3, "catalog.items", "List the test workspace's items", self._items)
        self._step(4, "lakehouse.bad_name", "An invalid lakehouse name is refused before calling Fabric", self._bad_name)
        if fabric.status == "passed" and self.live_test:
            self._live()
        if fabric.status == "passed" and self.platform_test:
            self._platform()
        self._step(10, "support.bundle", "A support bundle holds nothing personal", self._bundle)
        return self._finish(started)

    def _client(self) -> str:
        # app_settings gives Microsoft's client id when nothing is set, so only another id counts
        if self.client_id and self.client_id != MICROSOFT_CLIENT_ID:
            raise RuntimeError("Settings > Advanced > App ID (or FABRIC_DESKTOP_CLIENT_ID) is set, so this run "
                               "signs in with your own app registration. Clear it to test Microsoft's client.")
        return "No App ID set: signs in with Microsoft's client."

    def _token(self, method: str) -> str:
        if not getattr(self.credential, method)():
            raise RuntimeError("Sign-in returned an empty token")
        return "Token received (not recorded)."

    def _workspaces(self) -> str:
        spaces = self.fabric.list_workspaces()
        if not any(str(w.get("id", "")).lower() == self.workspace_id.lower() for w in spaces):
            raise RuntimeError(f"The test workspace isn't among the {len(spaces)} workspaces listed")
        return f"{len(spaces)} workspaces listed, the test workspace among them."

    def _items(self) -> str:
        items = self.fabric.list_items(self.workspace_id)
        kinds = sorted({str(i.get("type", "")) for i in items})
        return f"{len(items)} items ({', '.join(kinds) or 'none'})."

    def _bad_name(self) -> str:
        # The check create_lakehouse makes before any request, called on its own: this step never
        # reaches Fabric, so a regression can't create an unledgered lakehouse.
        problem = fabric_api.lakehouse_name_problem("1 bad name")
        if not problem:
            raise RuntimeError("The name check accepted '1 bad name', so the client would send it to Fabric")
        return f"Refused: {problem}"

    def _live(self) -> None:
        test = self.live_test(self.dir / "live")
        self.live = test.run()
        owner = {id: f.n for f in FLOWS for id in f.live_checks}
        for c in self.live["checks"]:
            self.steps.append(Step(owner.get(c["id"], 9), f"live.{c['id']}", c["title"], c["status"], c["detail"]))
        refused = self.live["refused"]
        self.steps.append(Step(9, "live.outcome", "The live test passed and left nothing behind",
                               "passed" if self.live["outcome"] == "passed" and not self.live["left_behind"] else "failed",
                               "; ".join(refused) if refused else f"Live test {self.live['run_id']}: {self.live['outcome']}"
                               + (f", {len(self.live['left_behind'])} left behind" if self.live["left_behind"] else "")))

    def _platform(self) -> None:
        test = self.platform_test(self.dir / "platform")
        self.platform = test.run()
        for c in self.platform["checks"]:
            self.steps.append(Step(c["flow"], c["id"], c["title"], c["status"], c["detail"]))
        refused, left = self.platform["refused"], self.platform["left_behind"]
        self.steps.append(Step(11, "platform.outcome", "The platform flows ran and left nothing behind",
                               "passed" if not refused and (not left or self.platform["kept"]) else "failed",
                               "; ".join(refused) if refused else
                               f"{len(left)} item(s) kept (--platform-keep)" if self.platform["kept"] else
                               f"{len(left)} item(s) left behind: see platform/platform-ledger.json" if left else
                               "Every platform item it made was deleted."))

    def _bundle(self) -> str:
        with tempfile.TemporaryDirectory() as tmp:
            path = self.support_bundle(Path(tmp))
            with zipfile.ZipFile(path) as z:
                text = "\n".join(z.read(n).decode("utf-8", "replace") for n in z.namelist())
        found = []
        if _TOKEN.search(text):
            found.append("a token")
        for label, value in (("the signed-in user", livetest._safe(self.credential.signed_in_user)),
                             ("the tenant id", livetest._safe(self.credential.signed_in_tenant)),
                             ("the test workspace id", self.workspace_id)):
            if value and value.lower() in text.lower():
                found.append(label)
        if found:
            raise RuntimeError(f"The support bundle contains {', '.join(found)}")
        return f"Checked {len(text)} characters: no token, user name, tenant id or workspace id."

    # ---- results

    def _finish(self, started: str) -> dict[str, Any]:
        flows = []
        for f in FLOWS + (PLATFORM_FLOWS if self.platform_test else []):
            mine = [s for s in self.steps if s.flow == f.n]
            failed = [s for s in mine if s.status == "failed" and s.required]
            automated = "failed" if failed else ("passed" if any(s.status == "passed" for s in mine) else "none")
            flows.append({"n": f.n, "title": f.title, "features": f.features, "automated": automated,
                          "by_hand": f.by_hand})
        failed = any(f["automated"] == "failed" for f in flows)
        result = {
            "schema": SCHEMA, "run_id": self.run_id, "version": __version__, "started_at": started,
            "finished_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "workspace": self.live.get("workspace") or {"id": self.workspace_id, "name": ""},
            "outcome": "failed" if failed else "passed", "run_in_fabric": self.run_in_fabric,
            "flows": flows, "steps": [asdict(s) for s in self.steps],
            "live_test": {k: self.live.get(k) for k in ("run_id", "outcome", "refused", "left_behind", "files")}
            if self.live else None,
            "platform_test": {k: self.platform.get(k) for k in ("run_id", "outcome", "refused", "left_behind", "kept")}
            if self.platform else None,
        }
        self.dir.mkdir(parents=True, exist_ok=True)
        (self.dir / "smoke.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
        (self.dir / "summary.md").write_text(summary(result), encoding="utf-8")
        result["files"] = [str(self.dir / "smoke.json"), str(self.dir / "summary.md")]
        return result


def _engine() -> str:
    import deltalake
    import duckdb
    import polars
    import pyarrow
    return (f"KratosForge engine {__version__}; DuckDB {duckdb.__version__}, Polars {polars.__version__}, "
            f"delta-rs {deltalake.__version__}, PyArrow {pyarrow.__version__}.")


def _support_bundle(out: Path) -> Path:
    from . import support_bundle
    return support_bundle.create(out)


def summary(result: dict[str, Any]) -> str:
    mark = {"passed": "Passed", "failed": "**Failed**", "skipped": "Skipped", "none": "Nothing automated"}
    ws = result["workspace"]
    lines = [f"# KratosForge smoke test {result['run_id']}", "",
             f"Outcome of the automated parts: **{result['outcome']}**. Engine {result['version']}, workspace "
             f"{ws.get('name') or ws.get('id')}, started {result['started_at']}"
             f"{', with Run in Fabric' if result['run_in_fabric'] else ', without Run in Fabric'}.", "",
             "Copy docs/product/smoke-test/RESULTS-TEMPLATE.md to results-<date>.md and fill in the by-hand part.", "",
             "| Flow | Features | Automated | By hand |", "| --- | --- | --- | --- |"]
    for f in result["flows"]:
        lines.append(f"| {f['n']}. {f['title']} | {', '.join(f['features'])} | {mark[f['automated']]} | "
                     f"{len(f['by_hand'])} to tick |")
    lines += ["", "## Steps", "", "| Flow | Step | Result | Detail |", "| --- | --- | --- | --- |"]
    for s in result["steps"]:
        note = "" if s["required"] else " (recorded, doesn't fail the flow)"
        detail = (s["detail"] + note).replace("|", "\\|").replace("\n", " ")
        lines.append(f"| {s['flow']} | {s['title']} | {mark[s['status']]} | {detail} |")
    lines += ["", "## By hand", ""]
    for f in result["flows"]:
        lines.append(f"{f['n']}. **{f['title']}**")
        lines += [f"   - [ ] {b}" for b in f["by_hand"]]
    live = result.get("live_test")
    if live and live.get("left_behind"):
        lines += ["", "**Left behind** by the live test: run `fabric-desktop live-test --cleanup` with its ledger.json."]
    platform = result.get("platform_test")
    if platform and platform.get("left_behind"):
        lines += ["", f"**{'Kept' if platform.get('kept') else 'Left behind'}** by the platform flows: the items in "
                      "platform/platform-ledger.json. Delete them in Fabric when you're done."]
    return "\n".join(lines) + "\n"
