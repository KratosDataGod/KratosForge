"""Moving keys from LH_Keys into the platform: the edges the move tests don't reach. Fakes stand in for
the lakehouse and the platform's keys; nothing reaches a tenant."""

from types import SimpleNamespace

import polars as pl

from fabric_desktop.central import migrate as mig

KT = SimpleNamespace(name="customer", table="customer", sk="customer_sk", bk="customer_bk")


class Lakehouse:
    def __init__(self, df):
        self.df = df

    def read(self, lakehouse, schema, table):
        assert (lakehouse, schema) == ("LH_Keys", "sk")
        return self.df


class Platform:
    def __init__(self, stored=None):
        self.ensured, self.stored = [], stored or []
        self.client = SimpleNamespace(call=lambda op, **kw: {"imported": len(kw["rows"])})

    def ensure(self, name):
        self.ensured.append(name)

    def export(self, name):
        return self.stored


def test_a_key_table_not_in_lh_keys_yet_is_created_empty_and_counts_as_matching():
    platform = Platform()
    out = mig.migrate(SimpleNamespace(key_tables={"c": KT}), Lakehouse(None), platform)
    assert platform.ensured == ["customer"]
    assert out == {"tables": [{"table": "customer", "copied": 0, "checked": 0, "match": True,
                               "note": "not in LH_Keys yet"}], "ok": True, "renumbered": 0}


def test_a_platform_copy_with_a_different_or_extra_key_does_not_match():
    df = pl.DataFrame({"customer_sk": [-1, 1, 2], "customer_bk": ["?", "A", "B"],
                       "create_date": ["2026-01-01"] * 3})
    platform = Platform(stored=[(-1, "?", ""), (1, "A", ""), (2, "Z", ""), (3, "C", "")])
    out = mig.migrate(SimpleNamespace(key_tables={"c": KT}), Lakehouse(df), platform)
    table = out["tables"][0]
    assert table["copied"] == 2 and table["checked"] == 2
    assert table["match"] is False and table["differ"] == [2] and table["extra_in_platform"] == 1
    assert out["ok"] is False
