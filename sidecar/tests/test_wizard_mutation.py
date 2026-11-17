"""wizard.py paths that mutation testing (batch 18, docs/quality/mutation-testing.md) showed no test checked."""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from fabric_desktop import wizard


@pytest.fixture(autouse=True)
def _own_app_data(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "appdata"))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))


def _code(built):
    return "\n".join(c["source"] for c in built["cells"] if c["type"] == "code")


def _md(built):
    return "\n".join(c["source"] for c in built["cells"] if c["type"] == "markdown")


def test_the_fiscal_month_choices_are_numbered_from_1_with_their_names():
    months = wizard.CHOICES["fiscal_start"]
    assert months[0] == ["1", "January"] and months[6] == ["7", "July"] and months[11] == ["12", "December"]
    assert len(months) == 12


def test_a_malformed_allowed_values_line_shows_the_allowed_values_example():
    with pytest.raises(ValueError) as caught:
        wizard.build("quality", {"source": "dbo.orders", "keys": "order_id", "allowed": "status",
                                 "output": "silver.orders", "rejects": "silver.orders_rejects"})
    assert str(caught.value) == "Write each allowed values line as column: rule, like status: open|closed."


def test_a_malformed_range_line_shows_the_range_example():
    with pytest.raises(ValueError) as caught:
        wizard.build("quality", {"source": "dbo.orders", "keys": "order_id", "ranges": "amount",
                                 "output": "silver.orders", "rejects": "silver.orders_rejects"})
    assert str(caught.value) == "Write each range line as column: rule, like amount: 0..1000000."


def test_clean_with_no_columns_keeps_every_column():
    code = _code(wizard.build("clean", {"source": "dbo.orders", "keys": "order_id", "output": "silver.orders"}))
    assert "# Keep every column. To keep some: df = df.select([...])" in code
    assert "df = df.select(['" not in code


def test_clean_with_columns_selects_them():
    code = _code(wizard.build("clean", {"source": "dbo.orders", "keys": "order_id", "output": "silver.orders",
                                        "columns": "order_id, amount"}))
    assert "df = df.select(['order_id', 'amount'])\n" in code
    assert "# Keep every column" not in code


def test_load_file_needs_key_columns():
    with pytest.raises(ValueError, match="so the load can be checked and proven"):
        wizard.build("load_file", {"file": "Files/raw/rates.csv", "output": "dbo.rates"})


def test_load_folder_refuses_another_file_type():
    with pytest.raises(ValueError, match=r"^The file type is csv, parquet or json\.$"):
        wizard.build("load_folder", {"folder": "Files/raw/daily", "file_type": "xml", "keys": "order_id",
                                     "output": "bronze.orders"})


def test_scd2_with_tracked_columns_says_which_columns_start_a_new_version():
    text = _md(wizard.build("scd2", {"source": "dbo.customers", "keys": "customer_id", "tracked": "city, region",
                                     "output": "gold.dim_customer"}))
    assert "When `city`, `region` changes, the current row is closed" in text
    assert "A change to another column alone updates the current row in place" in text
    assert "any other column changes" not in text


def test_scd2_without_tracked_columns_versions_on_any_change():
    text = _md(wizard.build("scd2", {"source": "dbo.customers", "keys": "customer_id",
                                     "output": "gold.dim_customer"}))
    assert "When any other column changes, the current row is closed" in text
    assert "alone updates the current row" not in text


def _dates(start, days, fiscal="1"):
    return {"start": start.isoformat(), "end": (start + timedelta(days=days)).isoformat(), "fiscal_start": fiscal,
            "output": "gold.dim_date"}


def test_a_date_dimension_covers_at_most_200_years_of_366_days():
    start = date(2000, 1, 1)
    wizard.build("date_dimension", _dates(start, 200 * 366))
    with pytest.raises(ValueError, match=r"^A date dimension covers at most 200 years\.$"):
        wizard.build("date_dimension", _dates(start, 200 * 366 + 1))


def test_a_fiscal_year_from_january_is_the_calendar_year():
    built = wizard.build("date_dimension", _dates(date(2024, 1, 1), 30))
    assert "df = df.with_columns(fiscal_year=pl.col(\"year\"), fiscal_month=((pl.col(\"month\") - 1) % 12) + 1)" \
        in _code(built)
    assert "The fiscal year starts in January" in _md(built)


def test_a_fiscal_year_from_july_is_named_by_the_year_it_ends_in():
    built = wizard.build("date_dimension", _dates(date(2024, 1, 1), 30, "7"))
    assert "pl.col(\"year\") + (pl.col(\"month\") >= 7).cast(pl.Int32)" in _code(built)
    assert "The fiscal year starts in July and is named by the calendar year it ends in." in _md(built)
