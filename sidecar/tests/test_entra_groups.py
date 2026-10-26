"""Entra ID groups per domain: naming, linking existing groups, and the scripts an Entra admin runs."""

import csv
import io
import shutil
import subprocess

import pytest

from fabric_desktop.central import core, entra
from fabric_desktop.central.client import LocalPlatform
from fabric_desktop.service import Service

ADMIN = "chris@example.com"
OWNER = "ana@contoso.com"
GID = "7c1e2f3a-1111-2222-3333-44445555a90d"


@pytest.fixture
def platform(tmp_path):
    p = LocalPlatform(tmp_path / "platform.sqlite", lambda: ADMIN)
    p.call("claim_admin")
    p.call("save_domain", domain={"name": "Sales", "owners": f"{OWNER}, Raj Patel"})
    p.call("save_domain", domain={"name": "Sales EMEA", "parent_id": "sales"})
    return p


def _plan(p, **kw):
    return entra.plan(p.call("list_domains"), "sales", p.call("list_entra_groups", domain="sales"),
                      p.call("entra_group_naming"), **kw)


def test_plan_names_every_role_and_only_the_environments_each_needs(platform):
    rows = _plan(platform)
    names = [r["suggested"] for r in rows]
    assert names == ["SG-FAB-SALES-DOMAIN-ADMINS", "SG-FAB-SALES-DOMAIN-CONTRIBUTORS", "SG-FAB-SALES-WSADMINS-DEV",
                     "SG-FAB-SALES-WSADMINS-TEST", "SG-FAB-SALES-WSADMINS-PROD", "SG-FAB-SALES-CREATORS-DEV",
                     "SG-FAB-SALES-CREATORS-TEST", "SG-FAB-SALES-READERS-PROD", "SG-FAB-SALES-VIEWERS-PROD",
                     "SG-FAB-SALES-STEWARDS"]
    assert all(r["mail_nickname"] == r["suggested"].lower() for r in rows)
    assert [r["suggested"] for r in _plan(platform, envs=["prod"])][2:4] == ["SG-FAB-SALES-WSADMINS-PROD", "SG-FAB-SALES-READERS-PROD"]


def test_subdomains_name_their_parent_and_themselves(platform):
    subs = [r for r in _plan(platform, subdomains=True) if r["domain_id"] == "sales-emea"]
    assert subs[0]["suggested"] == "SG-FAB-SALES-SALES-EMEA-DOMAIN-ADMINS"
    platform.call("save_entra_group_naming", naming={"pattern": "{PREFIX}_{DOMAIN}_{ROLE}_{ENV}", "prefix": "",
                                                       "case": "lower", "words": {"readers": "read"}})
    rows = _plan(platform)
    assert "sales_read_prod" in [r["suggested"] for r in rows]
    assert "sales_stewards" in [r["suggested"] for r in rows]


def test_naming_refuses_parts_that_would_break_a_script_or_entra(platform):
    for bad in ({"pattern": "{DOMAIN} {ROLE}"}, {"pattern": "{PREFIX}-{ROLE}"}, {"prefix": "SG;rm"},
                {"words": {"readers": "a'b"}}, {"words": {"owners": "X"}}, {"case": "title"}, {"bogus": 1}):
        with pytest.raises(core.PlatformError):
            platform.call("save_entra_group_naming", naming=bad)


def test_only_admins_name_groups_and_only_admins_or_owners_link(tmp_path, platform):
    path = tmp_path / "platform.sqlite"
    owner = LocalPlatform(path, lambda: OWNER)
    stranger = LocalPlatform(path, lambda: "eve@contoso.com")
    with pytest.raises(core.PlatformError, match="Only platform admins"):
        owner.call("save_entra_group_naming", naming={"prefix": "X"})
    with pytest.raises(core.PlatformError, match="owners"):
        stranger.call("link_entra_group", domain="sales", role="readers", env="prod", group_id=GID)
    # A parent domain's owners may link for its subdomains too.
    linked = owner.call("link_entra_group", domain="sales-emea", role="stewards", env="prod", group_id=GID.upper(),
                        group_name="EMEA Stewards")
    assert linked[0]["env"] == "" and linked[0]["group_id"] == GID
    assert owner.call("unlink_entra_group", domain="sales-emea", role="stewards") == []


def test_linking_checks_the_role_environment_and_object_id(platform):
    with pytest.raises(core.PlatformError, match="object ID"):
        platform.call("link_entra_group", domain="sales", role="readers", env="prod", group_id="Sales Readers")
    with pytest.raises(core.PlatformError, match="per environment"):
        platform.call("link_entra_group", domain="sales", role="readers", env="", group_id=GID)
    with pytest.raises(core.PlatformError, match="role must be"):
        platform.call("link_entra_group", domain="sales", role="owners", env="", group_id=GID)
    with pytest.raises(core.PlatformError, match="isn't a domain"):
        platform.call("link_entra_group", domain="nope", role="stewards", env="", group_id=GID)


def test_reading_never_creates_the_table(platform):
    with platform._db() as db:  # a platform made before linked groups
        db.run(f"DROP TABLE {db.t('catalog', 'entra_group')}")
        db.commit()
        assert core.list_entra_groups(db) == [] and not db.exists("catalog", "entra_group")
        assert core.entra_group_naming(db)["prefix"] == "SG-FAB"
    assert platform.call("link_entra_group", domain="sales", role="stewards", env="", group_id=GID)[0]["group_name"] == GID


def test_linked_roles_use_your_group_and_leave_the_create_scripts(platform):
    platform.call("link_entra_group", domain="sales", role="domain_admins", env="", group_id=GID, group_name="Sales Data Owners")
    rows = _plan(platform)
    owners = entra.owners_of(platform.call("list_domains")[0])
    assert owners == [OWNER]  # "Raj Patel" isn't an email, so a script can't make him an owner
    ps = entra.powershell(rows, owners, ADMIN, "2026-10-26")
    assert "SG-FAB-SALES-DOMAIN-ADMINS" not in ps and "Sales Data Owners (Domain owners)" in ps
    assert ps.count("@{ Name = ") == 9 and "$owners = @('ana@contoso.com')" in ps
    sh = entra.az(rows, owners)
    assert sh.count("\nmake_group '") == 9 and "SG-FAB-SALES-DOMAIN-ADMINS" not in sh
    table = list(csv.reader(io.StringIO(entra.table(rows, owners))))
    assert len(table) == 11 and table[1][3] == "Sales Data Owners" and table[1][9] == GID


def test_scripts_quote_what_people_typed(platform):
    platform.call("save_domain", domain={"id": "sales", "name": "Sales", "description": "x",
                                         "owners": OWNER})
    platform.call("link_entra_group", domain="sales", role="viewers", env="prod", group_id=GID,
                  group_name="It's =cmd|' /c calc'!A1\n$(rm -rf /)")
    rows = _plan(platform)
    rows[0]["description"] = "O'Brien's $(whoami) `n"
    ps = entra.powershell(rows, [OWNER])
    assert "'O''Brien''s $(whoami) `n'" in ps  # single-quoted: PowerShell expands nothing in it
    sh = entra.az(rows, [OWNER])
    assert "'O'\\''Brien'\\''s $(whoami) `n'" in sh
    assert "\n$(rm -rf /)" not in ps and "\n$(rm -rf /)" not in sh
    cells = [c for row in csv.reader(io.StringIO(entra.table(_plan(platform), [OWNER]))) for c in row]
    assert not any(c.startswith("=") for c in cells)


def test_role_script_grants_domain_and_workspace_roles_and_says_what_is_manual(platform):
    platform.call("link_entra_group", domain="sales", role="viewers", env="prod", group_id=GID, group_name="All Sales Staff")
    rows = _plan(platform)
    for r in rows:
        r["fabric_domain"] = "0f6a7b1c-0000-4000-8000-000000000001"
    text = entra.roles(rows, {"dev": [{"id": "11111111-1111-1111-1111-111111111111", "name": "Sales Dev"}],
                              "prod": [{"id": "22222222-2222-2222-2222-222222222222", "name": "Sales Prod"}]})
    assert "/admin/domains/0f6a7b1c-0000-4000-8000-000000000001/roleAssignments/bulkAssign" in text
    assert "type = 'Admins'" in text and "type = 'Contributors'" in text
    assert "Grant-Workspace '11111111-1111-1111-1111-111111111111' 'SG-FAB-SALES-CREATORS-DEV in Sales Dev' $gid 'Contributor'" in text
    assert f"(Get-GroupId 'All Sales Staff' '{GID}')" in text
    assert "No Test workspace of Sales is known yet" in text and "Manage OneLake data access" in text
    no_fabric = entra.roles(_plan(platform), {})
    assert "isn't in Fabric yet" in no_fabric and "bulkAssign" not in no_fabric


@pytest.mark.skipif(not shutil.which("bash"), reason="needs bash")
def test_az_script_is_valid_bash(platform, tmp_path):
    script = tmp_path / "groups.sh"
    script.write_text(entra.az(_plan(platform), [OWNER]))
    assert subprocess.run(["bash", "-n", str(script)], capture_output=True).returncode == 0


@pytest.mark.skipif(not shutil.which("pwsh"), reason="needs PowerShell")
def test_powershell_scripts_parse(platform, tmp_path):
    for name, text in (("groups.ps1", entra.powershell(_plan(platform), [OWNER])), ("roles.ps1", entra.roles(_plan(platform), {}))):
        path = tmp_path / name
        path.write_text(text)
        check = ("$e = $null; [void][System.Management.Automation.Language.Parser]::ParseFile("
                 f"'{path}', [ref]$null, [ref]$e); if ($e) {{ $e; exit 1 }}")
        assert subprocess.run(["pwsh", "-NoProfile", "-Command", check], capture_output=True).returncode == 0


# --------------------------------------------------------------------------- the engine calls


@pytest.fixture
def svc(tmp_path, monkeypatch):
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path / "home")
    s = Service(root=tmp_path / "app")
    s.platform_save_domain({"name": "Sales", "owners": OWNER})
    return s


def test_service_plans_links_and_saves_a_script(svc, tmp_path):
    out = svc.platform_entra_groups("sales")
    assert out["can_link"] and out["can_name"] and len(out["rows"]) == 10
    svc.platform_entra_link("sales", "readers", "prod", GID, "Sales Analytics Readers", envs=["dev", "test", "prod"])
    rows = svc.platform_entra_groups("sales")["rows"]
    assert [r["name"] for r in rows if r["role"] == "readers"] == ["Sales Analytics Readers"]
    script = svc.platform_entra_script("sales", "powershell")
    assert script["new"] == 9 and "SG-FAB-SALES-READERS-PROD" not in script["text"]
    saved = svc.platform_entra_save("sales", "csv")
    assert saved["path"].endswith("Sales Entra groups.csv") and saved["steps"]
    assert svc.platform_entra_save("sales", "csv")["path"].endswith("Sales Entra groups (2).csv")
    assert svc.platform_entra_unlink("sales", "readers", "prod")["linked"][0]["env"] == "dev"
    with pytest.raises(ValueError):
        svc.platform_entra_script("sales", "python")
    with pytest.raises(ValueError, match="object ID"):
        svc.platform_entra_link("sales", "readers", "prod", "not-an-id")


def test_search_without_directory_rights_says_to_paste_the_id(svc):
    class NoGraph:
        def _token(self, scopes):
            raise RuntimeError("AADSTS65001 consent required")
    svc._auth = NoGraph()
    with pytest.raises(PermissionError, match="object ID"):
        svc.platform_entra_find("sales readers")
    # Linking by ID still works: the name is the ID until someone names it.
    svc.platform_entra_link("sales", "stewards", "", GID)
    assert svc.platform_entra_groups("sales")["rows"][-1]["name"] == GID


_QUOTES = "'‘’‚‛"


def _code_outside_literals(script):
    """The PowerShell a script runs outside its quoted strings and comments, the way PowerShell's tokenizer
    reads single-quoted strings (any of the five single quotes opens or closes one; two in a row are one)."""
    out = []
    for line in script.splitlines():
        i, n, code = 0, len(line), []
        while i < n:
            c = line[i]
            if c == "#":
                break
            if c in _QUOTES:
                i += 1
                while i < n:
                    if line[i] in _QUOTES:
                        if i + 1 < n and line[i + 1] in _QUOTES:
                            i += 2
                            continue
                        break
                    i += 1
                i += 1
                continue
            if c == '"':
                i = line.find('"', i + 1)
                i = n if i < 0 else i + 1
                continue
            code.append(c)
            i += 1
        out.append("".join(code))
    return "\n".join(out)


@pytest.mark.parametrize("q", list(_QUOTES))
def test_no_single_quote_of_any_kind_leaves_a_powershell_string(platform, q):
    payload = f"x{q}; Start-Process calc; {q}"
    platform.call("save_domain", domain={"id": "sales", "name": f"Sales{q} + (Start-Process calc) + {q}", "owners": OWNER})
    platform.call("link_entra_group", domain="sales", role="viewers", env="prod", group_id=GID, group_name=payload)
    platform.call("link_entra_group", domain="sales", role="creators", env="dev", group_id=GID,
                  group_name=f"Chris{q} HR Start-Process calc")
    rows = _plan(platform)
    for r in rows:
        r["fabric_domain"] = "0f6a7b1c-0000-4000-8000-000000000001"
    spaces = {e: [{"id": "11111111-1111-1111-1111-111111111111", "name": f"WS{q}; Start-Process calc; {q}"}]
              for e in ("dev", "test", "prod")}
    for script in (entra.powershell(rows, [OWNER]), entra.roles(rows, spaces)):
        assert "Start-Process" in script
        assert "Start-Process" not in _code_outside_literals(script)
    assert entra._ps(payload) == f"'x{q}{q}; Start-Process calc; {q}{q}'"


@pytest.mark.parametrize("bad", ['{"pattern": "{DOMAIN}&calc&{ROLE}"}', '{"prefix": "SG|calc"}', '{"words": {"readers": "R^x"}}',
                                 '{"pattern": "{DOMAIN}-{ROLE}\\nx"}', '["not", "a", "dict"]', "not json"])
def test_naming_written_around_the_platform_reads_as_the_default(platform, bad):
    with platform._db() as db:  # someone with write access to the catalog's table, not through save
        core._upsert(db, "config", "setting", "name", {"name": "entra_group_naming", "value": bad})
        db.commit()
    assert platform.call("entra_group_naming") == core.DEFAULT_GROUP_NAMING
    assert _plan(platform)[0]["suggested"] == "SG-FAB-SALES-DOMAIN-ADMINS"


def test_a_name_with_shell_characters_never_reaches_a_script(platform):
    rows = _plan(platform)
    rows[7]["suggested"] = "SG-FAB-SALES-READERS&calc&-PROD"  # as if naming had slipped past the checks
    rows[8]["suggested"] = "SG-FAB-SALES-VIEWERS-PROD\n"
    for r in rows:
        r["fabric_domain"] = "0f6a7b1c-0000-4000-8000-000000000001"
    spaces = {"prod": [{"id": "22222222-2222-2222-2222-222222222222", "name": "Sales Prod"}]}
    for script in (entra.powershell(rows, [OWNER]), entra.az(rows, [OWNER]), entra.roles(rows, spaces)):
        assert "calc" not in script and "VIEWERS-PROD" not in script
        assert "Do these by hand: these names have characters" in script and "Data readers, Prod" in script
    assert entra.powershell(rows, [OWNER]).count("@{ Name = ") == 8
    assert not entra.scriptable("A\n") and not entra.scriptable("x" * 257) and entra.scriptable("SG-FAB_x.y")


def test_object_ids_match_whole(svc):
    with pytest.raises(ValueError, match="object ID"):
        svc.platform_entra_link("sales", "readers", "prod", GID + "\nx")
