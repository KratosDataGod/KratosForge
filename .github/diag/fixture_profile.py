"""Temporary: where the Windows runner spends a governance test fixture's 17 s (not for merge)."""
import cProfile, os, pathlib, pstats, sqlite3, subprocess, sys, tempfile, time

sys.path.insert(0, "tests")


def timed(label, fn, n=1):
    t = time.perf_counter()
    for _ in range(n):
        fn()
    print(f"{label:55s} {(time.perf_counter() - t) / n * 1000:9.1f} ms each (n={n})", flush=True)


base = pathlib.Path(tempfile.mkdtemp())
for mode, sync in [("delete", "full"), ("delete", "normal"), ("wal", "full"), ("wal", "normal")]:
    db = sqlite3.connect(base / f"t-{mode}-{sync}.db")
    db.execute(f"pragma journal_mode={mode}"); db.execute(f"pragma synchronous={sync}")
    db.execute("create table if not exists t(x)"); db.commit()
    def one(db=db):
        db.execute("insert into t values (1)"); db.commit()
    timed(f"sqlite commit journal={mode} synchronous={sync}", one, 30)
    db.close()
timed("sqlite connect + one select", lambda: sqlite3.connect(base / "t-delete-full.db").execute("select 1").fetchall(), 30)
timed("open+write+close a new small file", lambda: (base / f"f{time.perf_counter_ns()}.txt").write_text("x"), 30)
def fs():
    p = base / f"g{time.perf_counter_ns()}.txt"
    with open(p, "w") as f:
        f.write("x"); f.flush(); os.fsync(f.fileno())
timed("new file + fsync", fs, 30)
timed("git --version", lambda: subprocess.run(["git", "--version"], capture_output=True), 10)
timed("python -c pass", lambda: subprocess.run([sys.executable, "-c", "pass"], capture_output=True), 5)

import msal  # noqa: E402
import conftest  # noqa: E402  # the tests' offline sign-in service, as in pytest
_Real = msal.PublicClientApplication
class _Offline(_Real):
    def __init__(self, *a, **k):
        k.setdefault("http_client", conftest._NoSignInService())
        super().__init__(*a, **k)
msal.PublicClientApplication = _Offline

t = time.perf_counter()
from fabric_desktop.server import Service  # noqa: E402
from test_governance import NAMING  # noqa: E402
print(f"{'import Service + test_governance':55s} {(time.perf_counter() - t) * 1000:9.1f} ms", flush=True)

prof = cProfile.Profile()
d = pathlib.Path(tempfile.mkdtemp())
steps = []
prof.enable()
t = time.perf_counter(); s = Service(root=d / "app"); steps.append(("Service()", time.perf_counter() - t))
for name, fn in [
    ("save_domain Sales", lambda: s.platform_save_domain({"name": "Sales", "owners": "omar@contoso.com; ana@contoso.com", "description": "Selling"})),
    ("save_domain EMEA", lambda: s.platform_save_domain({"name": "EMEA", "parent_id": "sales"})),
    ("save_source", lambda: s.platform_save_source({"code": "SAP", "title": "SAP ECC", "system_type": "sap", "expected_hours": "24", "connections": {"prod": {"server_host": "sap.contoso.com", "secret_name": "sap-pw"}}})),
    ("create_key_table", lambda: s.platform_create_key_table("customer", business_key="customer_code", description="Customers", domain_id="sales")),
    ("save_term", lambda: s.platform_save_term({"term": "Active customer", "definition": "Bought in the last year", "domain_id": "sales"})),
    ("add_admin", lambda: s.platform_add_admin("ana@contoso.com")),
    ("governance_create", lambda: s.governance_create(NAMING)),
    ("governance_publish", lambda: s.governance_publish("table-names")),
    ("governance_assign", lambda: s.governance_assign("table-names", "domain", "sales", {"enforcement": "block"})),
    ("governance_create draft", lambda: s.governance_create(NAMING | {"id": "draft-only", "name": "Never published"})),
    ("git_create", lambda: s.git_create(str(d / "platform"), "platform")),
    ("save_domain again (warm)", lambda: s.platform_save_domain({"name": "Other"})),
]:
    t = time.perf_counter(); fn(); steps.append((name, time.perf_counter() - t))
prof.disable()
for name, secs in steps:
    print(f"fixture step {name:42s} {secs * 1000:9.1f} ms", flush=True)
st = pstats.Stats(prof); st.sort_stats("tottime").print_stats(30); st.sort_stats("cumulative").print_stats(45)
