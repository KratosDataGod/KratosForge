"""WR-247 review: an error that shows a cell's value shows at most its first 1,000 characters."""

import pytest

from fabric_desktop.localdata import LocalData


@pytest.fixture()
def engine(tmp_path):
    local = LocalData(tmp_path / "app" / "local-data")
    yield local.engine(local.create("Error value cut")["id"])
    local.close_all()


@pytest.mark.parametrize("sql", [
    "SELECT CAST(REPLICATE('x', 5000) AS INT)",
    "SELECT CAST(t.s AS INT) FROM (SELECT REPLICATE('x', 5000) AS s) t",
    "SELECT CAST(REPLICATE('9', 5000) AS INT)",
])
def test_a_long_value_is_cut_in_the_message(engine, sql):
    with pytest.raises(Exception) as caught:
        engine.execute(sql, dialect="tsql")
    message = str(caught.value)
    assert len(message) < 1200
    assert "value '" in message


def test_a_short_value_is_shown_whole(engine):
    with pytest.raises(Exception, match=r"Conversion failed when converting the varchar value .abc. to data type int\."):
        engine.execute("SELECT CAST(t.s AS INT) FROM (SELECT 'abc' AS s) t", dialect="tsql")
