"""Fuzz the support bundle's redaction (support_bundle._files: redact.for_support and the text it reads) over
what a bundle holds, log lines, JSON (escaped and not) and paths, with names, emails, user folders, tenant
names and tokens in many scripts and forms: \\uXXXX escapes, percent-encoding, a bytes repr, NFC and NFD.

The rule is that nothing redaction is meant to take out survives in any form: each output is decoded every
way the input could have been written, and none of the planted values may be found in it. Each case is
seeded (stdlib ``random``) and reproducible from its seed. The fixed cases reproduce what long runs found.

The second part is the replay sample size limit: a Parquet file whose footer understates its rows."""

from __future__ import annotations

import io
import json
import random
import re
import string
import unicodedata
import uuid
import zipfile
from pathlib import Path
from urllib.parse import quote, unquote

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from fabric_desktop import app_settings, replay, support_bundle

CASES = 300

# Letters of many scripts, each with the marks it combines with where it has them.
SCRIPTS = {
    "latin": "abcdefghijklmnopqrstuvwxyzéèêëñçøåßüöäÿœłńśźżíóúãõ",
    "cjk": "王李张刘陈杨赵黄周吴伟芳娜敏静秀英华明",
    "kana": "さとうすずきたかはしわたなべアイウエオ",
    "hangul": "김이박최정민준서연지우현",
    "cyrillic": "абвгдежзийклмнопрстуфхцчшщыэюяё",
    "greek": "αβγδεζηθικλμνξοπρστυφχψωάέή",
    "arabic": "ابتثجحخدذرزسشصضطظعغفقكلمنهوي",
    "hebrew": "אבגדהוזחטיכלמנסעפצקרשת",
    "devanagari": "कखगघचछजझटठडढतथदधनपफबभमयरलवशसह",
    "thai": "กขคงจฉชซญดตถทนบปผพฟมยรลวสหอฮ",
}
MARKS = {"devanagari": "ािीुूेैोौ्ं", "thai": "ัิีึืุู่้๊๋", "arabic": "ًٌٍَُِّْ"}
DOMAINS = ["contoso.com", "contoso.cn", "fabrikam.de", "bücher.de", "例え.jp", "пример.рф", "xn--bcher-kva.de",
           "mail.contoso.co.uk", "ex-ample.org"]


def _word(rnd: random.Random, script: str | None = None, n: int | None = None) -> str:
    script = script or rnd.choice(list(SCRIPTS))
    out = ""
    for _ in range(n or rnd.randint(5, 8)):
        out += rnd.choice(SCRIPTS[script])
        if script in MARKS and rnd.random() < 0.4:
            out += rnd.choice(MARKS[script])
    return out


def _person(rnd: random.Random) -> str:
    """A user name: one or two words, sometimes a dot, an apostrophe or a dash between them."""
    first = _word(rnd)
    if rnd.random() < 0.5:
        return first
    return first + rnd.choice([".", "'", "-", "_"]) + _word(rnd)


def _token(rnd: random.Random) -> str:
    """A JWT-shaped value made at run time, never a literal (push protection)."""
    part = lambda n: "".join(rnd.choice(string.ascii_letters + string.digits + "_-") for _ in range(n))
    return "eyJ" + part(20) + "." + part(30) + "." + part(20)


# ---- the forms a value can be written in, and the decoding the check undoes them with

def _escaped(text: str) -> str:
    return json.dumps(text)[1:-1]  # \u00e9 for é, as json.dumps writes by default


def _bytes_repr(text: str) -> str:
    return repr(text.encode("utf-8"))[2:-1]  # \xc3\xa9 for é, as a bytes value prints


def _percent(text: str, safe: str = "") -> str:
    return quote(text, safe=safe)


_U = re.compile(r"\\u([0-9a-fA-F]{4})")
_X = re.compile(r"(?:\\x[0-9a-fA-F]{2})+")


def _every_form(text: str) -> str:
    """``text`` decoded every way it may have been written, until nothing more decodes, NFC and casefolded."""
    for _ in range(6):
        before = text
        text = _U.sub(lambda m: chr(int(m.group(1), 16)), text)
        text = text.encode("utf-16", "surrogatepass").decode("utf-16", "replace")  # pair up \ud83d\ude00
        text = _X.sub(lambda m: bytes.fromhex(m.group(0).replace("\\x", "")).decode("utf-8", "replace"), text)
        text = unquote(text)
        text = text.replace("\\\\", "\\")
        if text == before:
            break
    return unicodedata.normalize("NFC", text).casefold()


def _found(out: str, secrets: list[str]) -> list[str]:
    plain = _every_form(out)
    return [s for s in secrets if unicodedata.normalize("NFC", s).casefold() in plain]


# ---- a case: one personal value in one place, written in one form

def _case(rnd: random.Random) -> tuple[str, list[str]]:
    """A line a bundle could hold, and the values that must not survive redaction."""
    user = _person(rnd)
    kind = rnd.choice(["email", "win", "unix", "onedrive", "tenant", "sharepoint", "personal", "guid", "token"])
    if kind == "email":
        local = user
        address = f"{local}@{rnd.choice(DOMAINS)}"
        value, secrets = rnd.choice([f"signed in as {address}", f"https://graph.microsoft.com/v1.0/users/{address}",
                                     f"mailto:{address}", f"<{address}>", f"user={address}&x=1"]), [address, local]
    elif kind == "win":
        value, secrets = rnd.choice(["C:\\Users\\{}\\AppData\\Local\\x.py", "c:/users/{}/Documents/a b.ipynb",
                                     "D:\\Documents and Settings\\{}\\x"]).format(user), [user]
    elif kind == "unix":
        value, secrets = rnd.choice(["/home/{}/x.py", "/Users/{}/Library/x"]).format(user.replace("'", "")), \
            [user.replace("'", "")]
    elif kind == "onedrive":
        org = _word(rnd) + " " + _word(rnd)
        value, secrets = f"C:\\Users\\{user}\\OneDrive - {org}\\Notebooks\\a.ipynb", [user, org]
    elif kind == "tenant":
        name = _word(rnd, "latin").encode("ascii", "ignore").decode() + "corp"  # a tenant's domain is ASCII
        value, secrets = f"tenant {name}.onmicrosoft.com signed in", [name]
    elif kind == "sharepoint":
        name = _word(rnd, "latin").encode("ascii", "ignore").decode() + "corp"
        site = _word(rnd)
        value, secrets = f"https://{name}.sharepoint.com/sites/{site}/Shared Documents/a.csv", [name, site]
    elif kind == "personal":
        name = _word(rnd, "latin").encode("ascii", "ignore").decode() + "corp"
        local = _word(rnd)
        value, secrets = f"https://{name}-my.sharepoint.com/personal/{local}_{name}_com/Documents/a.csv", [name, local]
    elif kind == "guid":
        guid = str(uuid.UUID(int=rnd.getrandbits(128)))
        guid = guid.upper() if rnd.random() < 0.3 else guid
        value, secrets = rnd.choice(["workspace {} failed", "/groups/{}/items", "id={}&x"]).format(guid), [guid]
    else:
        token = _token(rnd)
        value, secrets = rnd.choice(["Authorization: Bearer {}", "token {} expired", "{{\"t\": \"{}\"}}"]).format(token), [token]
    if rnd.random() < 0.3:
        value = unicodedata.normalize("NFD", value)
    form = rnd.choice(["log", "json", "json-plain", "escaped-log", "percent", "percent-twice", "bytes", "json-percent"])
    if form == "log":
        line = f"2026-11-23 10:00:00 INFO {value}"
    elif form in ("json", "json-plain"):  # a JSON log line, as logs.JsonFormatter writes it, or a setting
        line = json.dumps({"level": "INFO", "msg": value, "deep": [{"v": value}]}, ensure_ascii=form == "json")
    elif form == "escaped-log":
        line = f"ERROR read failed for '{_escaped(value)}'"
    elif form == "percent":
        line = f"GET https://api.example.com/x?q={_percent(value)}&n=1"
    elif form == "percent-twice":
        line = f"redirect https://login.example.com/?next={_percent(_percent(value))}"
    elif form == "bytes":
        line = f"DEBUG raw b'{_bytes_repr(value)}'"
    else:
        line = json.dumps({"url": "https://x/?p=" + _percent(value, "/:")})
    return line, secrets


def _bundle_text(pc: Path, lines: list[str]) -> str:
    (pc / "sidecar.log").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return support_bundle._files(pc, None)["sidecar.log"]


@pytest.fixture
def pc(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "la"))
    monkeypatch.setenv("ProgramData", str(tmp_path / "pd"))
    data = tmp_path / "la" / "FabricDesktop"
    data.mkdir(parents=True)
    (tmp_path / "pd" / "FabricDesktop").mkdir(parents=True)
    return data


def test_nothing_personal_survives_the_bundle_in_any_form(pc):
    cases = [_case(random.Random(seed)) for seed in range(CASES * 4)]
    out = _bundle_text(pc, [line for line, _s in cases]).split("\n")
    assert len(out) == len(cases) + 1
    leaks = [(seed, cases[seed][0], line, found) for seed, line in enumerate(out[:-1])
             if (found := _found(line, cases[seed][1]))]
    assert leaks == []


@pytest.mark.parametrize("planted, gone", [
    ("signed in as 王@contoso.cn", ["王@contoso.cn"]),  # an address of no ASCII letter before the @ stayed whole
    ("signed in as éanne@contoso.com", ["éanne"]),  # é stayed in front of [email]
    ("user Иван.Петров@пример.рф", ["Иван", "пример"]),
    ("guest josé@bücher.de", ["josé", "bücher"]),  # a domain with ü stayed whole
    (unicodedata.normalize("NFD", "mail zoë@x.com"), ["zoë"]),  # e and its accent apart: zoe stayed
    ("mail o'brien@x.ie", ["o'brien", "brien"]),  # o' stayed in front of [email]
    ('{"msg": "jos\\u00e9.garc\\u00eda@contoso.com"}', ["josé", "jos"]),  # a JSON log line: jos\ stayed
    ('{"msg": "C:\\\\Users\\\\Zo\\u00eb \\u00dcnal\\\\x"}', ["Zoë", "Ünal", "nal"]),  # [user]\u00eb \u00dcnal
    ('{"msg": "/home/zo\\u00eb/x"}', ["zoë"]),
    ("GET https://graph.microsoft.com/v1.0/users/ann.lee%40contoso.com", ["ann.lee"]),
    ("redirect /?next=%2Fusers%2Fann.lee%2540contoso.com", ["ann.lee"]),  # escaped twice
    ("GET /x?path=C%3A%5CUsers%5CZo%C3%AB%5CDocuments", ["Zo", "Zoë"]),
    ("GET /x?path=%2Fhome%2Fzoe%2Fx", ["zoe"]),
    ("open https://contoso-my.sharepoint.com/personal/zo%C3%AB_contoso_com/x", ["zo", "contoso"]),
    ("open https://contoso-my.sharepoint.com/personal/최박정_contoso_com/x", ["최박정", "contoso"]),
    ("open C%3A%5CUsers%5Cann%5COneDrive%20-%20Fabrikam%20Ltd%5Cx", ["ann", "Fabrikam"]),
    ("DEBUG b'/Users/\\xc3\\xb8mzw/x'", ["ømzw", "mzw"]),
    ("id%3D22222222-2222-2222-2222-222222222222", ["22222222-2222"]),
])
def test_a_personal_value_written_another_way_is_taken_out_of_the_bundle(pc, planted, gone):
    """Each was found in a bundle whole, or in part ("jos\\[email]", "é[email]", "[user]\\u00eb \\u00dcnal")."""
    out = _bundle_text(pc, [planted])
    assert _found(out, gone) == [], out


@pytest.mark.parametrize("value", ['C:\\x%22y%5Cz\\u00e9', "a%0Ab%E9\\x41", "\\\\u00e9 \\ud800 %25%22", "100% done %E2%80%A8"])
def test_about_json_stays_json_with_escapes_in_a_setting(pc, value):
    """What is unescaped never ends a JSON string early (a quote) or starts an escape (a backslash)."""
    app_settings.user_path().write_text(json.dumps({"sql_temp_dir": value}), encoding="utf-8")
    json.loads(support_bundle._files(pc, None)["about.json"])


def test_random_json_stays_json_once_unescaped():
    alphabet = ["%", "\\", '"', "u", "x", "0", "a", "E", "9", "2", "5", "C", "d8", "dc", "00", "é", "王", "\n", "'", "@", " "]
    for seed in range(CASES * 3):
        rnd = random.Random(seed)
        doc = {"".join(rnd.choices(alphabet, k=rnd.randint(0, 6))): ["".join(rnd.choices(alphabet, k=rnd.randint(0, 20)))
                                                                    for _ in range(3)] for _ in range(3)}
        text = json.dumps(doc, ensure_ascii=rnd.random() < 0.5)
        assert isinstance(json.loads(support_bundle._plain(text)), dict), (seed, text)


# ---- the replay sample size limit: a footer that understates what a sample holds

def _uvarint(b: bytes, i: int) -> tuple[int, int]:
    shift = out = 0
    while True:
        x = b[i]
        i, out, shift = i + 1, out | (x & 0x7F) << shift, shift + 7
        if not x & 0x80:
            return out, i


def _varint(n: int) -> bytes:
    out = bytearray()
    while n >= 0x80:
        out.append(n & 0x7F | 0x80)
        n >>= 7
    return bytes(out + bytes([n]))


def _thrift(b: bytes, i: int, kind: int):
    """A value of Thrift's compact protocol (as a Parquet footer is written) and where it ends."""
    if kind in (1, 2):
        return kind == 1, i
    if kind == 3:
        return b[i], i + 1
    if kind in (4, 5, 6):
        n, i = _uvarint(b, i)
        return (n >> 1) ^ -(n & 1), i
    if kind == 7:
        return b[i:i + 8], i + 8
    if kind == 8:
        n, i = _uvarint(b, i)
        return b[i:i + n], i + n
    if kind in (9, 10):
        count, inner = b[i] >> 4, b[i] & 15
        i += 1
        if count == 15:
            count, i = _uvarint(b, i)
        items = []
        for _ in range(count):
            item, i = _thrift(b, i, inner)
            items.append(item)
        return (inner, items), i
    fields, last = [], 0  # 12, a struct: [field id, type, value]
    while b[i]:
        head, i = b[i], i + 1
        last = last + (head >> 4) if head >> 4 else _thrift(b, i, 5)[0]
        if not head >> 4:
            i = _thrift(b, i, 5)[1]
        value, i = (head & 15 == 1, i) if head & 15 in (1, 2) else _thrift(b, i, head & 15)
        fields.append([last, head & 15, value])
    return fields, i + 1


def _unthrift(kind: int, value) -> bytes:
    if kind in (4, 5, 6):
        return _varint(((value << 1) ^ (value >> 63)) & (1 << 64) - 1)
    if kind == 3:
        return bytes([value])
    if kind == 7:
        return value
    if kind == 8:
        return _varint(len(value)) + value
    if kind in (9, 10):
        inner, items = value
        head = bytes([len(items) << 4 | inner]) if len(items) < 15 else bytes([0xF0 | inner]) + _varint(len(items))
        return head + b"".join(bytes([1 if x else 2]) if inner in (1, 2) else _unthrift(inner, x) for x in items)
    out, last = bytearray(), 0
    for fid, t, v in value:
        t2 = (1 if v else 2) if t in (1, 2) else t
        out += bytes([(fid - last) << 4 | t2]) if 0 < fid - last <= 15 else bytes([t2]) + _unthrift(5, fid)
        out += b"" if t in (1, 2) else _unthrift(t, v)
        last = fid
    return bytes(out + b"\0")


def _field(fields, fid):
    return next(f for f in fields if f[0] == fid)


def _lying(data: bytes, rows: int | None = None, size: int | None = None, values: int | None = None) -> bytes:
    """``data`` with its footer saying it holds ``rows`` rows of ``size`` bytes unpacked and ``values`` values
    a column, whatever its pages hold. pyarrow writes an honest footer, so the footer is rewritten."""
    n = int.from_bytes(data[-8:-4], "little")
    meta, _end = _thrift(data, len(data) - 8 - n, 12)
    assert _unthrift(12, meta) == data[-8 - n:-8]
    if rows is not None:
        _field(meta, 3)[2] = rows
    for group in _field(meta, 4)[2][1]:
        if rows is not None:
            _field(group, 3)[2] = rows
        if size is not None:
            _field(group, 2)[2] = size
        for chunk in _field(group, 1)[2][1]:
            column = _field(chunk, 3)[2]
            if size is not None:
                _field(column, 6)[2] = size
            if values is not None:
                _field(column, 5)[2] = values
    footer = _unthrift(12, meta)
    return data[:-8 - n] + footer + len(footer).to_bytes(4, "little") + b"PAR1"


def _read(data: bytes):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("samples/1.parquet", data)
    with zipfile.ZipFile(buf) as z:
        return replay.read_sample(z, "samples/1.parquet", "dbo.t")


def _parquet(table: pa.Table, **kw) -> bytes:
    buf = io.BytesIO()
    pq.write_table(table, buf, compression="zstd", **kw)
    return buf.getvalue()


SMALL = 1 << 20  # MAX_SAMPLE_FILE for these tests, so an oversized sample stays small


@pytest.mark.parametrize("lie", [{"size": 100}, {"size": 100, "values": 1}, {}])
def test_a_sample_whose_footer_understates_it_is_stopped_while_it_is_read(monkeypatch, lie):
    """Sample files are read in small batches with row and byte limits, whatever the footer says ({} is
    pyarrow's own footer, which counts a dictionary-encoded value once)."""
    monkeypatch.setattr(replay, "MAX_SAMPLE_FILE", SMALL)
    data = _lying(_parquet(pa.table({"a": ["y" * 100_000] * 100})), **lie)
    assert len(data) < 2000
    with pytest.raises(ValueError, match="^The bundle's sample of dbo.t wasn't read, because it holds more than a sample"):
        _read(data)


def test_a_sample_whose_footer_says_fewer_rows_reads_no_more_than_it_says():
    data = _lying(_parquet(pa.table({"a": list(range(replay.SAMPLE_ROWS * 3))})), rows=3, size=24)
    assert _read(data).num_rows == 3


def test_samples_with_random_footers_are_read_within_bounds_or_refused_by_name(monkeypatch):
    monkeypatch.setattr(replay, "MAX_SAMPLE_FILE", SMALL)
    for seed in range(CASES // 3):
        rnd = random.Random(seed)
        n = rnd.choice([0, 1, 10, replay.SAMPLE_ROWS, replay.SAMPLE_ROWS + 1, 3000])
        kind, width = rnd.randrange(4), rnd.choice([1, 500, 5000, 20_000])
        column = [pa.array([width] * n), pa.array(["z" * width] * n),
                  pa.ListArray.from_arrays(pa.array(range(0, n * width + 1, width), pa.int32()), pa.repeat(pa.scalar(0), n * width)),
                  pa.array([{"k": "w" * width}] * n)][kind]
        data = _parquet(pa.table({"a": column}), use_dictionary=rnd.random() < 0.8,
                        row_group_size=rnd.choice([None, 7, 400]))
        lie = {k: rnd.choice([0, 1, 3, 100, 10 ** 6]) for k in rnd.sample(["rows", "size", "values"], rnd.randint(0, 3))}
        try:
            table = _read(_lying(data, **lie))
        except ValueError as exc:
            assert str(exc).startswith("The bundle's sample of dbo.t "), seed
        else:
            assert table.num_rows <= replay.SAMPLE_ROWS and table.nbytes <= SMALL, (seed, lie)
