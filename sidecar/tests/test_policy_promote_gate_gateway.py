"""The promote gate falls back to the policy package only when the platform can't be reached, not when it answers."""
import pytest
import test_policy_promote_gate as gate
from gateway_answers import PAGE, gateway

svc = gate.svc


@pytest.mark.parametrize("content", [PAGE, b'{"errors": [{"message": "Bad Gateway"}]}'])
def test_the_promote_gate_falls_back_to_the_package_on_a_502(svc, content):
    col = gate.mark_pii(svc)
    gate.labels_policy(svc, "block")
    svc.governance_check_now()
    gateway(svc, 502, content, "Bad Gateway")
    check = svc.meta_promote_check("dev", "test")
    assert [e for e in gate.policy_errors(check) if f"silver.customers#{col}" in e]
    assert any(n.startswith("Block policies: Checked offline with the policy package taken") for n in check["notes"])


@pytest.mark.parametrize("status,content", [(400, b"Bad request"), (200, b"<html>login</html>"), (200, b"[1]")])
def test_the_promote_gate_doesnt_fall_back_on_a_platform_answer_that_isnt_json(svc, status, content):
    gate.labels_policy(svc, "block")
    svc.governance_check_now()
    gateway(svc, status, content)
    check = svc.meta_promote_check("dev", "test")
    assert not any("Checked offline" in n for n in check["notes"]), check["notes"]
