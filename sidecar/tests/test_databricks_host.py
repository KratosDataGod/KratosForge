"""The Databricks workspace address a token is sent to (KUDP hardening): only the workspace's own
https address, written one way, so a fragment, another port, whitespace or a different case never
makes a request go somewhere odd or a profile look like another workspace's."""

from __future__ import annotations

import pytest

from fabric_desktop import connprofiles
from fabric_desktop.engines import databricks_auth as dba

HOST = "https://adb-1234567890123456.7.azuredatabricks.net"


@pytest.mark.parametrize("written", [HOST, HOST + "/", HOST.upper().replace("HTTPS", "https"), HOST + ":443",
                                     "https://ADB-1234567890123456.7.AzureDatabricks.net:443/"])
def test_every_way_to_write_the_workspace_address_is_one_address(written):
    assert dba.check_host(written) == HOST


@pytest.mark.parametrize("host", [
    HOST + "#frag", HOST + "/#", HOST + ":8443", HOST + ":0", HOST + ":x", HOST + ":99999",
    " " + HOST, HOST + "\n", HOST + "\t", "https://adb-1234567890123456.7.azuredata bricks.net",
    HOST + "\x00", "https://[adb.azuredatabricks.net", "http://adb-1234567890123456.7.azuredatabricks.net",
    HOST + "/path", HOST + "?q=1", "https://u:p@adb-1234567890123456.7.azuredatabricks.net",
    HOST + ".evil.com", "https://evil.com#.azuredatabricks.net",
])
def test_an_address_that_isnt_only_the_workspace_is_refused(host):
    with pytest.raises(dba.DatabricksAuthError, match="must be the workspace address"):
        dba.check_host(host)
    assert connprofiles._host_problems({"cloud": "azure", "host": host}, "conn")


def test_a_profile_written_in_another_case_is_the_same_workspace(tmp_path):
    cfg = tmp_path / ".databrickscfg"
    cfg.write_text(f"[dev]\nhost = {HOST.upper().replace('HTTPS', 'https')}\ntoken = t0ken\n")
    conn = {"platform": "databricks", "host": HOST + "/", "credential": {"method": "cli-profile", "profile": "dev"}}
    provider = dba.provider_for(conn, cfg_path=cfg, http=object())
    assert provider.host == HOST and provider.token() == "t0ken"
