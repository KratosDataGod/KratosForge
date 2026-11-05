"""Check now falls back to the policy package only when the platform can't be reached, not when it answers."""
import pytest
import test_policy_package as package
from gateway_answers import PAGE, gateway

from fabric_desktop.central import core

svc = package.svc


@pytest.mark.parametrize("content", [PAGE, b'{"errors": [{"message": "Bad Gateway"}]}'])
def test_check_now_falls_back_to_the_package_on_a_502(svc, content):
    col = package.mark_pii(svc)
    package.publish(svc, "sensitive-columns-labelled", "labels")
    svc.governance_check_now()  # keeps the package
    gateway(svc, 502, content, "Bad Gateway")
    out = svc.governance_check_now()
    assert out["offline"] is True
    assert out["problems"][0] == ("The platform couldn't be reached, so the policies came from the policy package on this PC: "
                                  "HTTPError: UDF_Platform couldn't be reached to resolve policies: Fabric answered 502 Bad Gateway. "
                                  "Try again in a few minutes.")
    assert [f["object"] for f in package.labels(out)["findings"]] == [f"silver.customers#{col}"]


def test_check_now_doesnt_fall_back_on_a_400_that_isnt_json(svc):
    package.publish(svc, "sensitive-columns-labelled", "labels")
    svc.governance_check_now()
    gateway(svc, 400, b"Bad request", "Bad Request")
    with pytest.raises(core.PlatformError, match="couldn't resolve policies: UDF_Platform answered 400"):
        svc.governance_check_now()
