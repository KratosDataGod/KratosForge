"""Randomized, hostile round trips for the offline TMDL writer and reader (tmdl.from_bim / to_bim, PB-110) and for
TMDL to and from Apache Ossie. Every loop uses fixed seeds, so a failure names its seed and reproduces exactly:
rerun ``_Gen(seed).model()`` to get the model that failed.

The property: ``from_bim`` either refuses with a plain "This model can't be written as TMDL: ..." reason, or what
it writes reads back as the same model. Never another exception, never a loss it doesn't name."""

import copy
import random

import pytest

from fabric_desktop import model_ossie, tmdl
from test_tmdl_write import SEPARATORS, _read_back, _same

# Every str.splitlines separator, \r\n too.
BREAKS = ["\n", "\r", "\r\n", *SEPARATORS]
# Pieces names and values are built from: quotes, fences, edge spaces, TMDL keywords, Windows-reserved
# characters and device names, unicode (other spaces, a byte order mark, zero width joins, emoji).
PIECES = ["'", "''", '"', '""', "`", "```", "\t", " ", "  ", "=", ":", "///", "//", "#", "[", "]", ".", "..",
          "\\", "/", "<", ">", "|", "?", "*", "CON", "nul", "COM1", "LPT¹", "aux.txt", "a.", "é", "ß", "İ", "日本",
          "😀", "\U0001F468\u200d\U0001F469", "\u200b", "\u00a0", "\u3000", "\u2003", "\ufeff", "\x00", "\x7f",
          "a", "b", "Sales", "table", "measure", "column", "ref", "true", "false", "123", "-1", "1.5", "{", "}",
          "@", "$", "%", ","]
DATA_TYPES = ["string", "int64", "double", "decimal", "boolean", "dateTime"]
REFUSED = "This model can't be written as TMDL: "


def _indented(value: str) -> bool:
    """Whether every non-blank line of ``value`` starts with a tab or four spaces, as the reader counts indent."""
    lines = [line for line in value.replace("\r\n", "\n").replace("\r", "\n").split("\n") if line.strip()]
    return bool(lines) and all(line.startswith(("\t", "    ")) for line in lines)


class _Gen:
    """Random TMSL models from one seed. ``name_breaks`` is the chance a piece of a name is a line break;
    values get line breaks inside them, and at their edges only with ``edge_breaks``."""

    def __init__(self, seed: int, name_breaks: float = 0.0, edge_breaks: bool = False):
        self.r = random.Random(seed)
        self.name_breaks = name_breaks
        self.edge_breaks = edge_breaks

    def text(self, lo: int, hi: int, breaks: float) -> str:
        return "".join(self.r.choice(BREAKS) if self.r.random() < breaks else self.r.choice(PIECES)
                       for _ in range(self.r.randint(lo, hi)))

    def name(self, taken: set[str]) -> str:
        for _ in range(20):
            n = self.text(1, 4, self.name_breaks)
            if n not in taken:
                taken.add(n)
                return n
        n = f"n{len(taken)}"
        taken.add(n)
        return n

    def value(self) -> str:
        v = self.text(0, 8, 0.25)
        if self.edge_breaks:
            return v
        # What TMDL can't hold, reported by name (see the tests for each): blank first and last lines of a
        # fenced value, and an indent every line starts with (a fence's common indent is the layout's).
        v = v.strip("\r\n")
        return "x" + v if _indented(v) else v

    def maybe(self, p: float) -> bool:
        return self.r.random() < p

    def annotations(self) -> list[dict]:
        taken: set[str] = set()
        return [{"name": self.name(taken), "value": self.value()} for _ in range(self.r.randint(1, 2))]

    def table(self, taken: set[str]) -> dict:
        r = self.r
        t: dict = {"name": self.name(taken)}
        names: set[str] = set()  # a table's columns and measures share one namespace
        cols = []
        for _ in range(r.randint(0, 4)):
            c = {"name": self.name(names), "dataType": r.choice(DATA_TYPES)}
            if self.maybe(.5):
                c["sourceColumn"] = self.value()
            for key in ("description", "formatString", "displayFolder"):
                if self.maybe(.3):
                    c[key] = self.value()
            if self.maybe(.3):
                c["isHidden"] = self.maybe(.5)
            if self.maybe(.2):
                c["expression"], c["type"] = self.value(), "calculated"
            if self.maybe(.3):
                c["annotations"] = self.annotations()
            cols.append(c)
        if cols:
            t["columns"] = cols
        measures = []
        for _ in range(r.randint(0, 3)):
            m = {"name": self.name(names), "expression": self.value()}
            for key in ("description", "formatString", "displayFolder"):
                if self.maybe(.3):
                    m[key] = self.value()
            measures.append(m)
        if measures:
            t["measures"] = measures
        if self.maybe(.6):
            source = r.choice([{"type": "m", "expression": self.value()},
                               {"type": "entity", "entityName": self.value(), "schemaName": self.value(),
                                "expressionSource": self.value()}])
            t["partitions"] = [{"name": self.name(set()), "mode": r.choice(["import", "directLake"]), "source": source}]
        if self.maybe(.3):
            t["description"] = self.value()
        if self.maybe(.3):
            t["annotations"] = self.annotations()
        if self.maybe(.3):
            t["isHidden"] = True
        return t

    def model(self) -> dict:
        r = self.r
        taken: set[str] = set()
        tables = [self.table(taken) for _ in range(r.randint(1, 4))]
        model: dict = {"tables": tables}
        with_columns = [t for t in tables if t.get("columns")]
        if with_columns and self.maybe(.7):
            rels, rel_names = [], set()
            for _ in range(r.randint(1, 3)):
                a, b = r.choice(with_columns), r.choice(with_columns)
                rel = {"name": self.name(rel_names), "fromTable": a["name"], "fromColumn": r.choice(a["columns"])["name"],
                       "toTable": b["name"], "toColumn": r.choice(b["columns"])["name"]}
                if self.maybe(.3):
                    rel["isActive"] = False
                if self.maybe(.3):
                    rel["crossFilteringBehavior"] = "bothDirections"
                rels.append(rel)
            model["relationships"] = rels
        if self.maybe(.5):
            role_names: set[str] = set()
            model["roles"] = [{"name": self.name(role_names), "modelPermission": "read",
                               "members": [{"memberName": self.name(set())}],
                               "tablePermissions": [{"name": r.choice(tables)["name"], "filterExpression": self.value()}]}
                              for _ in range(r.randint(1, 2))]
        if self.maybe(.4):
            model["perspectives"] = [{"name": self.name(set()), "tables": [
                {"name": t["name"], **({"columns": [{"name": c["name"]} for c in t["columns"]]} if t.get("columns") else {})}
                for t in tables[:2]]}]
        if self.maybe(.5):
            expr_names: set[str] = set()
            model["expressions"] = [{"name": self.name(expr_names), "kind": "m", "expression": self.value()}
                                    for _ in range(r.randint(1, 2))]
        if self.maybe(.5):
            model["annotations"] = self.annotations()
        if self.maybe(.3):
            model["description"] = self.value()
        if self.maybe(.5):
            model["culture"] = self.value()
        return {"name": self.text(1, 3, 0), "compatibilityLevel": 1604, "model": model}


def _named(where: str, report: list[dict]) -> bool:
    """Whether ``where`` (a difference) is covered by a row of ``report``, the way from_bim matches them."""
    return any(where.startswith(r["object"].replace(" property ", " ", 1)) for r in report if r["kind"] == "lost")


# ---- TMSL -> TMDL -> TMSL

def test_random_hostile_models_write_exactly_or_are_refused_by_name():
    outcomes = {"exact": 0, "refused": 0}
    for seed in range(700):
        bim = _Gen(seed).model()
        try:
            files, report, back = _read_back(bim)
        except ValueError as exc:
            assert str(exc).startswith(REFUSED) and "three backticks" in str(exc), f"seed {seed}: {exc}"
            outcomes["refused"] += 1
            continue
        assert report == [], f"seed {seed}: {report}"
        assert _same(bim, back), f"seed {seed}: {tmdl.differences(bim, back)} {tmdl.differences(back, bim)}"
        assert tmdl.from_bim(back)[0] == files, f"seed {seed}: writing it again gives other files"
        outcomes["exact"] += 1
    assert outcomes["exact"] > 400 and outcomes["refused"] > 50, outcomes  # the generator reaches both


def test_random_names_with_line_breaks_are_refused_by_name():
    refused = 0
    for seed in range(300):
        bim = _Gen(seed, name_breaks=0.3).model()
        try:
            _read_back(bim)
        except ValueError as exc:
            assert str(exc).startswith(REFUSED), f"seed {seed}: {exc}"
            assert "line break" in str(exc) or "three backticks" in str(exc), f"seed {seed}: {exc}"
            refused += 1
    assert refused > 280


def test_values_with_line_breaks_at_their_edges_are_reported_by_name_never_silently():
    reported = 0
    for seed in range(400):
        bim = _Gen(seed, edge_breaks=True).model()
        try:
            files, report, back = _read_back(bim)
        except ValueError as exc:
            assert str(exc).startswith(REFUSED), f"seed {seed}: {exc}"
            continue
        diffs = tmdl.differences(bim, back)
        assert all(_named(d, report) for d in diffs), f"seed {seed}: {diffs} not all in {report}"
        assert all(r["message"] == "wouldn't read back from TMDL as it was" for r in report), f"seed {seed}: {report}"
        reported += bool(report)
    assert reported > 5


JUNK = [None, 0, -1, 1.5, float("nan"), True, False, "", [], {}, [1, "x"], {"a": 1}, ["a", "b"], {"name": 5},
        [{"name": None}], 10 ** 30]


def _slots(value, out):
    if isinstance(value, dict):
        for k in list(value):
            out.append((value, k))
            _slots(value[k], out)
    elif isinstance(value, list):
        for i in range(len(value)):
            out.append((value, i))
            _slots(value[i], out)
    return out


def test_models_with_wrong_types_anywhere_are_refused_or_name_what_they_lose():
    for seed in range(1500):
        g = _Gen(seed)
        bim = g.model()
        for _ in range(g.r.randint(1, 4)):
            slots = _slots(bim["model"], [])
            if not slots:
                break
            parent, key = g.r.choice(slots)
            parent[key] = copy.deepcopy(g.r.choice(JUNK))
        case = f"seed {seed}"
        try:
            files, report = tmdl.from_bim(bim)
        except ValueError as exc:
            assert str(exc).startswith(REFUSED), f"{case}: {exc}"
            continue
        back, _ = tmdl.to_bim(files, str(bim.get("name") or ""))
        missing = [d for d in tmdl.differences(bim, back) if not _named(d, report)]
        assert missing == [], f"{case}: lost without a word: {missing}"


def test_damaged_tmdl_is_read_or_refused_never_a_crash():
    extra = ["\n\t", "\n```", "\n\t\t```", "\nmeasure x = ```", "\n'", "///", "\nmodel", "\nmodel: 5",
             "\ndatabase: x", "\ntable: 3", "\n\tcolumn", "\n\t\tlevel x"]
    read = 0
    for seed in range(1500):
        g = _Gen(seed)
        try:
            files, _ = tmdl.from_bim(g.model())
        except ValueError:
            continue
        for _ in range(g.r.randint(1, 6)):
            path = g.r.choice(sorted(files))
            text, at = files[path], g.r.randint(0, len(files[path]))
            pick = g.r.random()
            if pick < .4:
                text = text[:at] + g.r.choice(PIECES + BREAKS + extra) + text[at:]
            elif pick < .7:
                text = text[:at] + text[at + g.r.randint(1, 20):]
            else:
                lines = text.split("\n")
                g.r.shuffle(lines)
                text = "\n".join(lines)
            files[path] = text
        try:
            tmdl.to_bim(files, "m")
        except ValueError:
            continue
        read += 1
    assert read > 1000


# ---- file names

_DEVICES = {"CON", "PRN", "AUX", "NUL", "CLOCK$", "CONIN$", "CONOUT$", *(f"{p}{i}" for p in ("COM", "LPT") for i in "123456789¹²³")}
_BAD_CHARS = set('<>:"/\\|?*\x7f\x85\u2028 ') | {chr(i) for i in range(32)}


def _check_file_name(out: str, case: str) -> None:
    assert out, case
    assert not set(out) & _BAD_CHARS, f"{case}: {out!r}"
    assert out.split(".")[0].upper().rstrip() not in _DEVICES, f"{case}: {out!r}"
    assert not out.endswith((".", " ")), f"{case}: {out!r}"
    assert len(out) <= 100, f"{case}: {len(out)} characters"


def test_random_file_names_are_safe_on_windows_and_unique_ignoring_case():
    r = random.Random(110)
    devices = sorted(_DEVICES) + ["con", "Nul", "lPt1", "COM¹"]
    for batch in range(300):
        taken: set[str] = set()
        seen: set[str] = set()
        names = []
        for _ in range(r.randint(1, 12)):
            kind = r.random()
            if kind < .25:
                name = r.choice(devices) + r.choice(["", ".tmdl", " ", ".", " .txt", "..", ".a.b"])
            elif kind < .35:
                name = r.choice(["x", "X", "ß", "a b"]) * r.randint(90, 130) + r.choice(["", ".", " ", "..."])
            elif kind < .5 and names:
                name = r.choice(names).swapcase()  # the same name in other case
            else:
                name = "".join(r.choice(PIECES + BREAKS) for _ in range(r.randint(0, 6)))
            names.append(name)
            case = f"batch {batch} name {name!r}"
            out = tmdl.file_name(name, taken)
            _check_file_name(out, case)
            assert out.casefold() not in seen, f"{case}: {out!r} given twice"
            seen.add(out.casefold())
            assert out == tmdl.file_name(name, set()) or out.endswith(")"), case  # only a number is added


def test_a_long_name_given_a_number_is_still_at_most_100_characters():
    taken: set[str] = set()
    names = [tmdl.file_name(n, taken) for n in ("x" * 150, "X" * 150, "x" * 99 + "." + "y", "x" * 99 + " y")]
    assert [len(n) for n in names] == [100, 100, 99, 100]  # the third would end in a dot
    assert names[1] == "X" * 96 + " (2)" and len({n.casefold() for n in names}) == 4


# ---- TMDL <-> Ossie

def _fabric(g: _Gen) -> dict:
    """A Direct Lake model shaped like the ones Fabric holds, with hostile names and values."""
    r = g.r
    taken: set[str] = set()
    tables = []
    for _ in range(r.randint(1, 3)):
        name, names = g.name(taken), set()
        cols = []
        for _ in range(r.randint(1, 3)):
            n = g.name(names)
            c = {"name": n, "dataType": r.choice(DATA_TYPES), "sourceColumn": n}
            for key in ("description", "formatString", "lineageTag"):
                if g.maybe(.35):
                    c[key] = g.value().replace("\r", "") or "x"  # the converter splits a bare \r differently
            if g.maybe(.3):
                c["isHidden"] = True
            cols.append(c)
        t = {"name": name, "columns": cols, "partitions": [{"name": name, "mode": "directLake", "source": {
            "type": "entity", "entityName": name, "schemaName": "dbo", "expressionSource": "DatabaseQuery"}}]}
        measures = [{"name": g.name(names), "expression": g.value().replace("\r", "") or "1",
                     **({"description": g.value().replace("\r", "") or "d"} if g.maybe(.4) else {})}
                    for _ in range(r.randint(0, 2))]
        if measures:
            t["measures"] = measures
        if g.maybe(.4):
            t["description"] = g.value().replace("\r", "") or "d"
        tables.append(t)
    model = {"culture": "en-US", "tables": tables, "expressions": [{"name": "DatabaseQuery", "kind": "m", "expression":
             "let\n    database = Sql.Database(\"abc\", \"sql1\")\nin\n    database"}]}
    if len(tables) > 1 and g.maybe(.7):
        a, b = tables[0], tables[1]
        model["relationships"] = [{"name": g.name(set()), "fromTable": a["name"], "fromColumn": a["columns"][0]["name"],
                                   "toTable": b["name"], "toColumn": b["columns"][0]["name"]}]
    if g.maybe(.5):
        model["roles"] = [{"name": g.name(set()), "modelPermission": "read", "tablePermissions": [
            {"name": tables[0]["name"], "filterExpression": g.value().replace("\r", "") or "TRUE()"}]}]
    return {"name": "SM", "compatibilityLevel": 1604, "model": model}


def test_random_fabric_models_go_tmdl_to_ossie_to_tmdl_exactly():
    exact = 0
    for seed in range(40):
        bim = _fabric(_Gen(seed))
        try:
            files, written = tmdl.from_bim(bim)
        except ValueError as exc:
            assert str(exc).startswith(REFUSED), f"seed {seed}: {exc}"
            continue
        assert written == [], f"seed {seed}: {written}"
        out = model_ossie.tmdl_to_ossie(files, "SM", translate_dax=False)
        lost = [r for r in out["report"] if r["message"] == "doesn't come back from Ossie as it was"]
        assert lost == [], f"seed {seed}: {lost}"
        back_files, report = model_ossie.ossie_to_tmdl(out["yaml"])
        assert report == [], f"seed {seed}: {report}"
        before, after = tmdl.to_bim(files, "SM")[0], tmdl.to_bim(back_files, "SM")[0]
        assert _same(before, after), f"seed {seed}: {tmdl.differences(before, after)}"
        exact += 1
    assert exact > 30


def test_random_models_to_ossie_and_back_name_everything_that_doesnt_come_back():
    for seed in range(40):
        bim = _Gen(seed).model()
        try:
            files, _ = tmdl.from_bim(bim)
        except ValueError:
            continue
        out = model_ossie.tmdl_to_ossie(files, bim["name"], translate_dax=False)
        try:
            back_files, report = model_ossie.ossie_to_tmdl(out["yaml"])
        except ValueError as exc:
            assert str(exc).startswith((REFUSED, "The Ossie file couldn't be converted")), f"seed {seed}: {exc}"
            continue
        before, after = tmdl.to_bim(files, bim["name"])[0], tmdl.to_bim(back_files, bim["name"])[0]
        missing = [d for d in tmdl.differences(before, after) if not _named(d, out["report"] + report)]
        assert missing == [], f"seed {seed}: lost without a word: {missing}"


# ---- regressions the loops above found

@pytest.mark.parametrize("value", ["\t", " ", "  \n  ", "\t\n ", " \u3000", "\x85\tx", "x\n\ty"])
def test_a_fenced_value_of_blank_lines_keeps_its_spaces(value):
    # A value that was only blank lines (spaces, tabs) came back with the layout's tabs added to it.
    bim = {"name": "m", "compatibilityLevel": 1604, "model": {"tables": [{"name": "T", "description": value, "columns": [
        {"name": "c", "dataType": "string", "sourceColumn": value, "annotations": [{"name": "a", "value": value}]}],
        "measures": [{"name": "M", "expression": value, "formatString": value}]}]}}
    files, report, back = _read_back(bim)
    assert report == [] and _same(bim, back), tmdl.differences(bim, back)


@pytest.mark.parametrize("value", ["\tx", "\tVAR a = 1\n\tRETURN a", "    four spaces", "\t\n\t\tx"])
def test_an_indent_every_line_of_a_value_starts_with_is_reported_by_name(value):
    # TMDL readers take a fence's common indent as layout, so this can't be written; it is named, not dropped.
    bim = {"name": "m", "compatibilityLevel": 1604, "model": {"tables": [{"name": "T", "measures": [
        {"name": "M", "expression": value}]}]}}
    _, report = tmdl.from_bim(bim)
    assert report == [{"object": "table 'T' measure 'M' expression", "kind": "lost",
                       "message": "wouldn't read back from TMDL as it was"}]


def test_an_empty_description_and_an_empty_calculated_column_come_back():
    bim = {"name": "m", "compatibilityLevel": 1604, "model": {"description": "", "tables": [{"name": "T", "description": "", "columns": [
        {"name": "c", "dataType": "string", "expression": "", "type": "calculated", "description": ""}]}]}}
    files, report, back = _read_back(bim)
    assert report == [] and _same(bim, back), tmdl.differences(bim, back)


def test_a_culture_property_with_a_line_break_is_read_as_the_property():
    # "culture = ```" was taken for a culture object (translations) and skipped.
    bim = {"name": "m", "model": {"culture": "en\u2028US", "tables": [{"name": "T"}]}}
    files, report, back = _read_back(bim)
    assert "culture = ```" in files["definition/model.tmdl"]
    assert report == [] and back["model"]["culture"] == "en\u2028US"


@pytest.mark.parametrize("bim", [[], {"model": "x"}, {"model": [1]}])
def test_something_that_isnt_a_model_is_refused_with_a_reason(bim):
    with pytest.raises(ValueError, match="isn't a semantic model"):
        tmdl.from_bim(bim)


def test_lists_and_sources_of_the_wrong_type_are_reported_not_a_crash():
    for wrong in (5, 1.5, True, "x"):
        _, report = tmdl.from_bim({"name": "m", "model": {"tables": wrong, "roles": wrong}})
        assert {r["object"] for r in report} >= {"tables", "roles"}, report
    bim = {"name": "m", "model": {"tables": [{"name": "T", "partitions": [{"name": "p", "source": [["a", "b"]]}]}]}}
    _, report = tmdl.from_bim(bim)
    assert report and all(r["kind"] == "lost" for r in report)


@pytest.mark.parametrize("text", ["model\n", "model: 5\n"])
def test_a_model_line_with_no_name_is_reported_not_a_crash(text):
    bim, report = tmdl.to_bim({"definition/model.tmdl": text}, "m")
    assert bim["model"] == {} and report[0]["object"] == "model"


def test_a_next_line_character_comes_back_from_ossie():
    # PyYAML writes U+0085 raw with allow_unicode and reads it back as a line break, so it came back a space.
    bim = copy.deepcopy(_fabric(_Gen(0)))
    t = bim["model"]["tables"][0]
    t["description"] = "a\x85b"
    t["columns"][0]["description"] = "x\x85\ny\x85"
    t["measures"] = [{"name": "M\x85", "expression": "1\x85+\n2", "description": "///\x85"}]
    assert model_ossie.round_trip(bim) == []


def test_writing_again_keeps_the_table_order_and_the_numbered_file_names():
    # The reader took the tables in file order, not model.tmdl's, and read "x (2).tmdl" before "x.tmdl", so
    # writing a model read from TMDL reordered model.tmdl and swapped which object got the number.
    bim = {"name": "m", "compatibilityLevel": 1604, "model": {
        "tables": [{"name": "b"}, {"name": "a"}, {"name": "A"}],
        "roles": [{"name": ":", "modelPermission": "read"}, {"name": ".", "modelPermission": "read"}]}}
    files, report, back = _read_back(bim)
    assert report == [] and [t["name"] for t in back["model"]["tables"]] == ["b", "a", "A"]
    assert tmdl.from_bim(back)[0] == files
    assert "definition/roles/_ (2).tmdl" in files and "definition/tables/A (2).tmdl" in files
