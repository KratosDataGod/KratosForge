"""FIPS mode: a non-security hash says so, so a FIPS-only OpenSSL doesn't refuse it.

Python on Windows doesn't read Windows' FIPS policy (its bundled OpenSSL has no FIPS provider), so
this matters where the engine's OpenSSL is in FIPS mode: the Linux agent on a FIPS host (RHEL with
fips=1), or a Python built against a FIPS OpenSSL. There, ``hashlib.md5()`` and ``hashlib.sha1()``
raise ValueError unless they are called with ``usedforsecurity=False``. Every MD5 or SHA-1 in the
engine is an id or a change check, never a security check, so each one says so. ruff's S324 rule
finds one that doesn't; these tests keep that rule on and run the hashing code as FIPS would.
"""

import hashlib
import tomllib
from pathlib import Path

import pytest

WEAK = {"md5", "sha1"}


def test_ruff_still_refuses_a_weak_hash_that_doesnt_say_why():
    config = tomllib.loads((Path(__file__).resolve().parents[1] / "pyproject.toml").read_text(encoding="utf-8"))
    assert "S324" in config["tool"]["ruff"]["lint"]["select"]


@pytest.fixture
def fips(monkeypatch):
    """hashlib as OpenSSL's FIPS provider leaves it: MD5 and SHA-1 only when not for security."""
    real_md5, real_sha1, real_new = hashlib.md5, hashlib.sha1, hashlib.new

    def refused(name):
        return ValueError(f"[digital envelope routines] unsupported: {name} isn't allowed in FIPS mode")

    def weak(name, real):
        def made(*args, usedforsecurity=True, **kwargs):
            if usedforsecurity:
                raise refused(name)
            return real(*args, usedforsecurity=False, **kwargs)
        return made

    def new(name, *args, usedforsecurity=True, **kwargs):
        if str(name).lower().replace("-", "") in WEAK and usedforsecurity:
            raise refused(name)
        return real_new(name, *args, usedforsecurity=usedforsecurity, **kwargs)

    monkeypatch.setattr(hashlib, "md5", weak("md5", real_md5))
    monkeypatch.setattr(hashlib, "sha1", weak("sha1", real_sha1))
    monkeypatch.setattr(hashlib, "new", new)


def test_the_stand_in_refuses_a_hash_for_security(fips):
    with pytest.raises(ValueError, match="FIPS"):
        hashlib.sha1(b"x")
    with pytest.raises(ValueError, match="FIPS"):
        hashlib.new("md5")
    assert hashlib.sha256(b"x").hexdigest()


def test_ids_and_change_checks_work_under_fips(fips):
    from fabric_desktop import fabric_compute, leases, model_bpa, notebook

    assert notebook._derived_id(0, {"type": "code", "source": "x = 1"}).startswith("fd-")
    assert len(model_bpa.notebook_hash({"cells": []})) == 12
    assert len(fabric_compute.bundle_hash({"a.json": b"{}"})) == 12
    assert len(leases.LocalBackend._etag(b"data")) == 40


def test_databricks_sql_hmac_with_md5_or_sha1_works_under_fips(fips):
    from fabric_desktop import databricks_bytes

    # HMAC of b"m" with key b"k", as hashlib gives it without FIPS (and Databricks' hmac()).
    expected = {
        "MD5": "ed7e724d3a91554aaa2043041d9c5305",  # pragma: allowlist secret
        "SHA-1": "772b71c8a2997e768be01d9711e4a4f34a24ef33",  # pragma: allowlist secret
        "SHA256": "b60090e3052297aeb5a080889ce2fc4bca957e756faeb4df7d31800ca1e771ec",  # pragma: allowlist secret
    }
    for algorithm, answer in expected.items():
        assert databricks_bytes.hmac("6b", "6d", algorithm) == answer
