"""PB-062 slice 1: the support bundle's policy state, its preview, and that no planted secret reaches it."""

import json
import zipfile

import pytest

from fabric_desktop import app_settings, cli, support_bundle

JWT = "eyJhbGciOiJSUzI1NiJ9.eyJzdWIiOiJ4In0.c2lnbmF0dXJl"
TENANT = "72f988bf-86f1-41af-91ab-2d7cd011db47"
# One of each kind of secret or personal detail, as it could turn up in a log line or a setting.
PLANTED = {
    "jwt": JWT,
    "bearer": "Bearer abcdefghijklmnop0123",
    "sas": "https://acct.blob.core.windows.net/c/f?sv=2024&sig=SECRETSIGNATUREVALUE123",
    "password": "Server=tcp:x.database.windows.net;User ID=sa;Password=Hunter2Secret!",
    "account_key": "DefaultEndpointsProtocol=https;AccountName=acct;AccountKey=QUJDREVGR0hJSktMTU5PUA==",
    "client_secret": "client_secret=abc8Q~verysecretvalue0123456789abcdefgh",
    "email": "ann.lee@contoso.com",
    "user_path": "C:\\Users\\Ann Lee\\AppData\\Local\\x.py",
    "tenant": TENANT,
}
LEAKS = [JWT, "abcdefghijklmnop0123", "SECRETSIGNATUREVALUE123", "Hunter2Secret!", "QUJDREVGR0hJSktMTU5PUA==",
         "verysecretvalue", "ann.lee", "Ann Lee", TENANT]


@pytest.fixture
def pc(tmp_path, monkeypatch):
    """A PC with its own settings, policy and engine log folders."""
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "la"))
    monkeypatch.setenv("ProgramData", str(tmp_path / "pd"))
    data = tmp_path / "la" / "FabricDesktop"
    data.mkdir(parents=True)
    (tmp_path / "pd" / "FabricDesktop").mkdir(parents=True)
    return data


def _bundle(tmp_path, *extra):
    cli.main(["support-bundle", "--out", str(tmp_path / "out"), *extra])
    [path] = list((tmp_path / "out").glob("fabric-desktop-support-*.zip"))
    with zipfile.ZipFile(path) as z:
        return {name: z.read(name).decode() for name in z.namelist()}


def test_no_planted_secret_reaches_the_zip(tmp_path, pc):
    lines = "\n".join(f"{kind}: {value}" for kind, value in PLANTED.items())
    (pc / "sidecar.log").write_text(f"start\n{lines}\nend\n")
    agent = tmp_path / "agent"
    (agent / "logs").mkdir(parents=True)
    (agent / "logs" / "agent.log").write_text(lines + "\n")
    app_settings.policy_path().write_text(json.dumps({
        "allowed_tenants": [TENANT], "support_url": f"https://help.contoso.com/?u={PLANTED['email']}",
        "telemetry_connection_string": f"InstrumentationKey={TENANT};IngestionEndpoint=https://x/;Secret=Hunter2Secret!",
        "dbconnect_index_url": "https://ann.lee:Hunter2Secret!@pkgs.contoso.com/simple"}))
    app_settings.user_path().write_text(json.dumps({"sql_temp_dir": PLANTED["user_path"]}))
    files = _bundle(tmp_path, "--agent-home", str(agent))
    assert set(files) == {"about.json", "sidecar.log", "agent.log", "CONTENTS.txt"}
    for name, text in files.items():
        for leaked in LEAKS:
            assert leaked not in text, f"{leaked!r} reached {name}"
    about = json.loads(files["about.json"])
    assert about["settings"]["telemetry_connection_string"]["value"] == "set"  # a secret: only whether it's set
    assert about["settings"]["support_url"]["value"] == "set"
    assert about["settings"]["dbconnect_index_url"]["value"] == "set"  # an internal mirror's host names the organization
    assert "contoso" not in files["about.json"]
    assert "start" in files["sidecar.log"] and "end" in files["sidecar.log"]


@pytest.mark.parametrize("folder", ["\\\\fs01.contoso.local\\scratch\\kf", "//fs01.contoso.local/scratch/kf"])
def test_a_network_temp_folder_shows_only_that_it_is_one(tmp_path, pc, folder):
    app_settings.user_path().write_text(json.dumps({"sql_temp_dir": folder}))
    about = json.loads(_bundle(tmp_path)["about.json"])
    assert about["settings"]["sql_temp_dir"]["value"] == "a network folder"
    assert "contoso" not in json.dumps(about)


def test_a_local_temp_folder_still_shows(tmp_path, pc):
    app_settings.user_path().write_text(json.dumps({"sql_temp_dir": "D:\\Temp\\kf"}))
    assert json.loads(_bundle(tmp_path)["about.json"])["settings"]["sql_temp_dir"]["value"] == "D:\\Temp\\kf"


def test_policy_state_names_what_it_sets_but_never_a_value(tmp_path, pc):
    app_settings.policy_path().write_text(json.dumps({"allow_fabric_runs": False, "channel": "preview",
                                                      "allowed_tenants": [TENANT], "call Ann on 555-0100": 1}))
    policy = json.loads(_bundle(tmp_path)["about.json"])["policy"]
    assert policy["state"] == "applied" and policy["problem"] == ""
    assert policy["sets"] == ["allow_fabric_runs", "allowed_tenants", "channel"]
    assert policy["unknown_keys"] == 1 and "555-0100" not in json.dumps(policy)  # a key that isn't a setting is only counted
    assert {"allow_fabric_runs", "allowed_tenants", "channel"} <= set(policy["from_policy"])
    assert "preview" not in json.dumps(policy) and TENANT not in json.dumps(policy)


def test_policy_state_says_when_the_last_good_copy_or_nothing_applies(tmp_path, pc):
    assert support_bundle.policy_state()["state"] == "none"
    app_settings.policy_path().write_text(json.dumps({"telemetry": False}))
    assert support_bundle.policy_state()["state"] == "applied"  # this read also keeps a copy
    app_settings.policy_path().write_text("{ broken")
    state = support_bundle.policy_state()
    assert state["state"] == "last_known_good" and state["sets"] == ["telemetry"] and "last copy" in state["problem"]
    app_settings.saved_policy_path().unlink()
    state = support_bundle.policy_state()
    assert state["state"] == "unreadable" and state["sets"] == [] and "most restrictive" in state["problem"]


def test_the_preview_lists_what_the_saved_bundle_holds(tmp_path, pc, capsys):
    (pc / "sidecar.log").write_text(f"one {PLANTED['email']}\ntwo\n")
    shown = support_bundle.preview()
    files = _bundle(tmp_path)
    assert [f["name"] for f in shown["files"]] == list(files)
    sizes = {f["name"]: f["bytes"] for f in shown["files"]}
    for name in ("sidecar.log", "CONTENTS.txt"):  # about.json carries its own time, so only these match byte for byte
        assert sizes[name] == len(files[name].encode("utf-8"))
    assert shown["contents"] == files["CONTENTS.txt"]
    capsys.readouterr()
    cli.main(["support-bundle", "--preview", "--out", str(tmp_path / "nothing")])
    out = capsys.readouterr().out
    assert "sidecar.log" in out and "never their values" in out
    assert not (tmp_path / "nothing").exists(), "a preview saves nothing"
