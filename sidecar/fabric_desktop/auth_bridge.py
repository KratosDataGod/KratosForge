"""MSAL sign-in for the Rust engine (same cache and app as the sidecar)."""

from __future__ import annotations

import json
import sys

from .auth import Authenticator


def _auth() -> Authenticator:
    return Authenticator()


def sign_in() -> dict:
    auth = _auth()
    auth.fabric_token()
    auth.storage_token()
    return {"user": auth.signed_in_user(), "tenant": auth.signed_in_tenant()}


def sign_out() -> dict:
    _auth().sign_out()
    return {"user": None}


def environment() -> dict:
    import platform

    auth = _auth()
    return {
        "python": platform.python_version(),
        "user": auth.signed_in_user(),
        "tenant": auth.signed_in_tenant(),
    }


def main() -> None:
    if len(sys.argv) < 2:
        raise SystemExit("usage: python -m fabric_desktop.auth_bridge sign_in|sign_out|environment")
    op = sys.argv[1]
    try:
        if op == "sign_in":
            out = sign_in()
        elif op == "sign_out":
            out = sign_out()
        elif op == "environment":
            out = environment()
        else:
            raise ValueError(f"unknown op {op!r}")
    except Exception as exc:  # noqa: BLE001
        sys.stdout.write(json.dumps({"error": str(exc)}) + "\n")
        raise SystemExit(1) from exc
    sys.stdout.write(json.dumps(out) + "\n")


if __name__ == "__main__":
    main()
