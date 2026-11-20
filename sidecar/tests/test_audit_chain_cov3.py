"""Edge cases of the audit chain: pruning a damaged or partly lost trail, and exports that are cut short."""

import json
import datetime as dt

from fabric_desktop import audit_chain, jsonfile


def _now(month: str) -> float:
    year, mon = (int(x) for x in month.split("-"))
    return dt.datetime(year, mon, 15, tzinfo=dt.timezone.utc).timestamp()


def _add(folder, at, **extra):
    return audit_chain.append(folder, {"at": at, "action": "x", **extra})


def _head(folder):
    return json.loads((folder / audit_chain.HEAD).read_text(encoding="utf-8"))


# ---------------------------------------------------------------- prune


def test_a_limit_of_zero_or_less_prunes_nothing(tmp_path):
    _add(tmp_path, "2020-01-01T00:00:00+00:00")
    _add(tmp_path, "2020-02-01T00:00:00+00:00")
    assert audit_chain.prunable(tmp_path, 0, _now("2026-01")) == []
    assert audit_chain.prunable(tmp_path, -5, _now("2026-01")) == []
    assert audit_chain.prune(tmp_path, 0, _now("2026-01")) == {"files": [], "lines": 0}
    assert len(audit_chain.files(tmp_path)) == 2


def test_a_damaged_last_line_in_a_pruned_month_leaves_an_empty_base_hash(tmp_path):
    _add(tmp_path, "2020-01-01T00:00:00+00:00")
    with (tmp_path / "audit-2020-01.jsonl").open("a", encoding="utf-8") as f:
        f.write("not json\n")
    _add(tmp_path, "2020-03-01T00:00:00+00:00")
    out = audit_chain.prune(tmp_path, 30, _now("2020-06"))
    assert out == {"files": ["audit-2020-01.jsonl"], "lines": 2, "through": "2020-01"}
    head = _head(tmp_path)
    assert head["base"] == {"hash": "", "through": "2020-01", "lines": 2}
    result = audit_chain.verify(tmp_path)
    # The damaged line restarted the chain from GENESIS, so the remaining trail checks out on its own.
    assert result["ok"] is True
    assert result["lines"] == 1
    assert result["pruned"] == {"through": "2020-01", "lines": 2}


def test_a_head_without_a_line_count_keeps_none_after_a_prune(tmp_path):
    _add(tmp_path, "2020-01-01T00:00:00+00:00")
    _add(tmp_path, "2020-03-01T00:00:00+00:00")
    head = _head(tmp_path)
    del head["lines"]
    jsonfile.write_json(tmp_path / audit_chain.HEAD, head)
    audit_chain.prune(tmp_path, 30, _now("2020-06"))
    new = _head(tmp_path)
    assert "lines" not in new
    assert new["base"]["lines"] == 1


def test_a_loss_after_the_cut_stays_on_record_shifted_by_the_pruned_lines(tmp_path):
    _add(tmp_path, "2020-01-01T00:00:00+00:00")
    _add(tmp_path, "2020-03-01T00:00:00+00:00")
    _add(tmp_path, "2020-03-02T00:00:00+00:00")
    lost_hash = _head(tmp_path)["hash"]
    head = _head(tmp_path)
    head["lost"] = {"hash": lost_hash, "lines": 3}
    jsonfile.write_json(tmp_path / audit_chain.HEAD, head)
    audit_chain.prune(tmp_path, 30, _now("2020-06"))
    new = _head(tmp_path)
    assert new["lost"] == {"hash": lost_hash, "lines": 2}
    assert new["lines"] == 2
    assert audit_chain.verify(tmp_path)["ok"] is True


def test_a_loss_before_the_cut_goes_with_the_pruned_history(tmp_path):
    first = _add(tmp_path, "2020-01-01T00:00:00+00:00")
    _add(tmp_path, "2020-03-01T00:00:00+00:00")
    head = _head(tmp_path)
    head["lost"] = {"hash": first["hash"], "lines": 1}
    jsonfile.write_json(tmp_path / audit_chain.HEAD, head)
    audit_chain.prune(tmp_path, 30, _now("2020-06"))
    assert "lost" not in _head(tmp_path)
    assert audit_chain.verify(tmp_path)["ok"] is True


def test_a_loss_without_a_line_count_is_left_as_it_is(tmp_path):
    _add(tmp_path, "2020-01-01T00:00:00+00:00")
    _add(tmp_path, "2020-03-01T00:00:00+00:00")
    head = _head(tmp_path)
    head["lost"] = {"hash": "abc"}
    jsonfile.write_json(tmp_path / audit_chain.HEAD, head)
    audit_chain.prune(tmp_path, 30, _now("2020-06"))
    assert _head(tmp_path)["lost"] == {"hash": "abc"}


def test_blank_lines_are_skipped_when_the_head_is_rebuilt(tmp_path):
    _add(tmp_path, "2026-01-01T00:00:00+00:00")
    with (tmp_path / "audit-2026-01.jsonl").open("a", encoding="utf-8") as f:
        f.write("\n   \n")
    (tmp_path / audit_chain.HEAD).unlink()  # forces a recount of the lines
    _add(tmp_path, "2026-01-02T00:00:00+00:00")
    assert _head(tmp_path)["lines"] == 2
    assert audit_chain.verify(tmp_path) == {"lines": 2, "ok": True, "problems": []}


# ---------------------------------------------------------------- export


def test_export_skips_blank_lines_and_lines_without_a_time_before_since(tmp_path):
    trail, out = tmp_path / "trail", tmp_path / "out" / "export.jsonl"
    _add(trail, "2026-01-01T00:00:00+00:00")
    path = trail / "audit-2026-01.jsonl"
    with path.open("a", encoding="utf-8") as f:
        f.write("\n")
        f.write("not json\n")
        f.write("[1, 2]\n")
    _add(trail, "2026-01-05T00:00:00+00:00")
    _add(trail, "2026-01-06T00:00:00+00:00")
    result = audit_chain.export(trail, out, since="2026-01-03")
    assert result["lines"] == 2
    lines = out.read_text(encoding="utf-8").splitlines()
    assert [json.loads(line)["at"] for line in lines] == ["2026-01-05T00:00:00+00:00", "2026-01-06T00:00:00+00:00"]
    assert audit_chain.verify_export(out, trail) == {"ok": True, "lines": 2, "problems": []}


def test_verify_export_reports_a_missing_export_file(tmp_path):
    trail, out = tmp_path / "trail", tmp_path / "export.jsonl"
    _add(trail, "2026-01-01T00:00:00+00:00")
    audit_chain.export(trail, out)
    out.unlink()
    assert audit_chain.verify_export(out) == {"ok": False, "lines": 0, "problems": ["export.jsonl: missing"]}


def test_verify_export_skips_blank_lines_and_reports_lines_that_arent_audit_lines(tmp_path):
    trail, out = tmp_path / "trail", tmp_path / "export.jsonl"
    _add(trail, "2026-01-01T00:00:00+00:00")
    audit_chain.export(trail, out)
    good = out.read_text(encoding="utf-8")
    out.write_text(good + "\n" + "[]\n" + "{\"no\": \"hash\"}\n" + "nope\n", encoding="utf-8")
    result = audit_chain.verify_export(out)
    assert result["ok"] is False
    assert result["lines"] == 4
    assert "line 3: not an audit line" in result["problems"]
    assert "line 4: not an audit line" in result["problems"]
    assert "line 5: not an audit line" in result["problems"]
    assert any("SHA-256" in p for p in result["problems"])


def test_verify_export_reports_lines_that_differ_from_the_trail(tmp_path):
    trail, out = tmp_path / "trail", tmp_path / "export.jsonl"
    for day in (1, 2, 3):
        _add(trail, f"2026-01-0{day}T00:00:00+00:00")
    audit_chain.export(trail, out)
    # Rebuild the trail's tail after the first line so the export's later lines no longer match it.
    path = trail / "audit-2026-01.jsonl"
    first = path.read_text(encoding="utf-8").splitlines()[0]
    path.write_text(first + "\n", encoding="utf-8")
    (trail / audit_chain.HEAD).unlink()
    _add(trail, "2026-01-02T00:00:00+00:00", other=True)
    result = audit_chain.verify_export(out, trail)
    assert result["problems"] == ["the export's lines differ from this trail's"]
    assert result["ok"] is False
