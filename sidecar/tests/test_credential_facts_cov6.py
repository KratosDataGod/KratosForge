"""What an agent says about its credential (credential_facts.py), the paths the other tests leave: a certificate
that isn't there, and one read once while its file is unchanged."""

from __future__ import annotations

from fabric_desktop import credential_facts as C


def test_a_certificate_that_isnt_there_has_no_expiry(tmp_path):
    assert C.certificate_expiry(str(tmp_path / "gone.pem")) is None


def test_a_certificate_is_read_once_while_its_file_is_unchanged(tmp_path, monkeypatch):
    cert = tmp_path / "agent.pem"
    cert.write_text("not really a certificate")
    reads = []
    monkeypatch.setattr(C, "_cache", {})
    monkeypatch.setattr(C, "_read_expiry", lambda path, password: reads.append(path) or "2027-01-01T00:00:00Z")
    assert C.certificate_expiry(str(cert)) == "2027-01-01T00:00:00Z"
    assert C.certificate_expiry(str(cert)) == "2027-01-01T00:00:00Z"
    assert reads == [cert]
