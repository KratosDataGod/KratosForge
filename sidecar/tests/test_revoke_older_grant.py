"""#1352: a grant written before grants kept their request still revokes, with what it has."""

import json

from fabric_desktop.central.client import LocalPlatform


def test_an_older_grant_without_a_request_revokes_with_the_fields_it_has(tmp_path):
    admin = LocalPlatform(tmp_path / "p.sqlite", lambda: "chris@example.com")
    admin.call("whoami")
    with admin._db() as db:
        db.run(f"INSERT INTO {db.t('catalog', 'grant')} (id, product_id, project) VALUES (?, ?, ?)",
               ("crm/c360|ledger", "crm/c360", "ledger"))
        db.conn.commit()
    assert admin.call("revoke_grant", product_id="crm/c360", project="ledger") == []
    row = next(e for e in admin.call("audit", limit=10) if e["action"] == "revoke access")
    assert json.loads(row["detail"]) == {"project": "ledger"}
