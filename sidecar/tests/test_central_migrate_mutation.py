"""Moving keys from LH_Keys into the platform: what mutation testing found the move tests didn't pin
(docs/quality/mutation-testing.md, batch 78). Fakes stand in for the lakehouse and the platform's keys;
nothing reaches a tenant."""

from types import SimpleNamespace

import polars as pl
import pytest

from fabric_desktop.central import migrate as mig

KT = SimpleNamespace(name="customer", table="customer", sk="customer_sk", bk="customer_bk")


@pytest.fixture(autouse=True)
def _home(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.setenv("HOME", str(tmp_path))


class Lakehouse:
    def __init__(self, df):
        self.df = df

    def read(self, lakehouse, schema, table):
        return self.df


class Platform:
    """Keeps what each import_keys call sent; export gives back `stored`, or what was imported."""

    def __init__(self, stored=None, answer=None):
        self.ensured, self.stored, self.sent = [], stored, []
        self.answer = answer
        self.client = SimpleNamespace(call=self._call)

    def _call(self, op, **kw):
        assert op == "import_keys" and kw["name"] == "customer"
        self.sent.append(kw["rows"])
        return self.answer if self.answer is not None else {"imported": len(kw["rows"])}

    def ensure(self, name):
        self.ensured.append(name)

    def export(self, name):
        if self.stored is not None:
            return self.stored
        return [tuple(r) for chunk in self.sent for r in chunk]


def keys(n):
    return pl.DataFrame({"customer_sk": list(range(1, n + 1)), "customer_bk": [f"B{i}" for i in range(1, n + 1)],
                         "create_date": ["2026-01-01"] * n})


def run(df, platform, **kw):
    return mig.migrate(SimpleNamespace(key_tables={"c": KT}), Lakehouse(df), platform, **kw)


def test_keys_are_sent_in_chunks_of_5000_in_order_with_none_left_out():
    platform = Platform()
    out = run(keys(12001), platform)
    assert [len(c) for c in platform.sent] == [5000, 5000, 2001]
    assert [c[0][0] for c in platform.sent] == [1, 5001, 10001]
    assert [r[0] for c in platform.sent for r in c] == list(range(1, 12002))
    table = out["tables"][0]
    assert table["copied"] == 12001 and table["checked"] == 12001 and table["match"] is True
    assert out["ok"] is True


def test_exactly_5000_keys_go_in_one_call():
    platform = Platform()
    run(keys(5000), platform)
    assert [len(c) for c in platform.sent] == [5000]


def test_an_answer_without_a_count_counts_nothing_copied():
    platform = Platform(answer={"status": "ok"})
    out = run(keys(3), platform)
    assert out["tables"][0]["copied"] == 0
    assert out["tables"][0]["checked"] == 3


def test_at_most_ten_differing_keys_are_listed_but_all_count_against_the_match():
    stored = [(i, "other", "") for i in range(1, 13)]
    out = run(keys(12), Platform(stored=stored))
    table = out["tables"][0]
    assert table["differ"] == list(range(1, 11))
    assert table["match"] is False and table["extra_in_platform"] == 0


def test_renumbered_counts_add_up_across_tables_and_a_table_without_one_adds_nothing(monkeypatch):
    other = SimpleNamespace(name="product", table="product", sk="customer_sk", bk="customer_bk")
    monkeypatch.setattr(mig, "add_renumbering",
                        lambda rows, name, service: {"table": name, "match": True, "renumbered": len(rows)})
    platform = Platform()
    project = SimpleNamespace(key_tables={"c": KT, "p": other})
    out = mig.migrate(project, Lakehouse(keys(3)), platform, keep_numbers=False)
    assert out["renumbered"] == 6 and platform.ensured == ["customer", "product"]
    out = mig.migrate(SimpleNamespace(key_tables={"c": KT}), Lakehouse(None), Platform(), keep_numbers=False)
    assert out["renumbered"] == 0
