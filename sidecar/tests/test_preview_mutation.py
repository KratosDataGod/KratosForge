"""Mutation-testing follow-up for preview.py (docs/quality/mutation-testing.md, batch 54)."""

import datetime as dt
import decimal

import pyarrow as pa

from fabric_desktop import preview


def test_the_limits_are_100_rows_200_mib_and_20000_characters():
    assert preview.ROWS == 100
    assert preview.MAX_FILE_BYTES == 200 * 2 ** 20 == 209_715_200
    assert preview.TEXT_BYTES == 20_000


def test_a_csv_shows_100_rows_by_default(tmp_path):
    path = tmp_path / "orders.csv"
    path.write_text("n\n" + "".join(f"{i}\n" for i in range(150)), encoding="utf-8")
    out = preview.file_preview(path, "orders.csv")
    assert len(out["rows"]) == 100 and out["rows"][-1] == [99]


def test_dates_and_decimals_show_as_text_and_nan_as_its_name():
    table = pa.table({"d": [dt.date(2026, 1, 2)], "m": [decimal.Decimal("1.50")], "f": [float("nan")],
                      "i": [3], "s": ["x"], "b": [True], "z": [None]})
    assert preview._rows(table, 5)["rows"] == [["2026-01-02", "1.50", "nan", 3, "x", True, None]]


class _Reader:
    """A CSV reader that hands out batches of 5 rows and counts how many were taken."""

    def __init__(self, batches):
        self.schema = pa.schema([("n", pa.int64())])
        self._batches = [pa.record_batch([pa.array(range(i * 5, i * 5 + 5))], schema=self.schema)
                         for i in range(batches)]
        self.taken = 0

    def __iter__(self):
        for b in self._batches:
            self.taken += 1
            yield b


def test_a_csv_is_read_only_until_it_has_enough_rows(tmp_path, monkeypatch):
    path = tmp_path / "big.csv"
    path.write_text("n\n1\n", encoding="utf-8")
    for limit, taken in ((10, 2), (6, 2), (5, 1), (11, 3)):
        reader = _Reader(4)
        monkeypatch.setattr(preview.pa_csv, "open_csv", lambda *_a, _r=reader, **_k: _r)
        out = preview.file_preview(path, "big.csv", limit=limit)
        assert reader.taken == taken, limit
        assert [r[0] for r in out["rows"]] == list(range(limit))


def test_a_text_file_of_exactly_the_limit_is_not_cut(tmp_path):
    path = tmp_path / "notes.txt"
    path.write_bytes(b"a" * preview.TEXT_BYTES)
    out = preview.file_preview(path, "notes.txt")
    assert out["truncated"] is False and len(out["text"]) == preview.TEXT_BYTES
    path.write_bytes(b"a" * (preview.TEXT_BYTES + 1))
    assert preview.file_preview(path, "notes.txt")["truncated"] is True
