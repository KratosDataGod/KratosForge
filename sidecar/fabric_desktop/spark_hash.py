"""Spark's hash() and xxhash64() on this PC, as DuckDB SQL that gives Spark's numbers.

Spark hashes each argument in turn, each one's hash the seed of the next (42 to start), NULLs keep the
seed, and an array's elements are hashed in turn the same way. Each value is hashed by its type as
Spark 3.5 does (InterpretedHashFunction in hash.scala): booleans, bytes, shorts, ints and dates as a
4-byte int; longs and timestamps (microseconds since 1970 UTC) as an 8-byte long; floats and doubles by
their IEEE bits (-0.0 as 0); a decimal of up to 18 digits by its unscaled long, a longer one by its
two's-complement bytes; text by its UTF-8 bytes and binary by its bytes. hash is Murmur3 x86 32-bit
(Murmur3_x86_32.java), xxhash64 is xxHash64 (XXH64.java), both with Spark's own ways of hashing the
last few bytes.

How: every argument becomes a list of items, each one value to hash: kind 'i' (an int, in ``i``), 'l'
(a long, in ``i``) or 'b' (bytes, as hex in ``h``). An argument's text is read by its DuckDB type, so
any type binds (DuckDB checks every CASE branch's types, even ones a row never takes). One fold over
all the items then runs the hash. Maps, structs, arrays of arrays and intervals stop the cell by name.
A TIMESTAMP (no zone) is read in the session time zone, as Spark reads its timestamps: the same
zone here and in Fabric gives the same number. Checked against Apache Spark 3.5.3 (tests/test_spark_hash.py).
"""

from __future__ import annotations

import itertools
import threading

_names = itertools.count()


def _reset_names() -> None:
    """Names start again for each hash, so the same call is always the same SQL (rewrite caches it)."""
    global _names
    _names = itertools.count()


def _name() -> str:
    return f"kf_h{next(_names)}"


def _let(value: str, body) -> str:  # type: ignore[no-untyped-def]
    """``body(name)`` with ``value`` bound to a lambda variable, so it is written (and run) once."""
    name = _name()
    return f"(LIST_TRANSFORM([{value}], {name} -> {body(name)}))[1]"


# ---- the hash as a list of steps on six registers, run by one LIST_REDUCE
#
# Written as nested SQL (one expression per step) the hash nests a few hundred levels deep, deeper
# than sqlglot and DuckDB read. As a list of steps it is one level: each step is a struct (o: what
# it does, d: the register it sets, s: the other register or the shift, c: a constant or a value
# read from the data), and one LIST_REDUCE runs them on the registers. Values are unsigned 32-bit
# (Murmur3) or 64-bit (xxHash64), held in HUGEINTs.

_H, _T, _V = 1, 2, (3, 4, 5, 6)  # the hash, a scratch register, xxHash64's four stripe lanes
_REGISTERS = 6


def _op(o: str, d: int, s: int = 0, c: str | int = 0) -> str:
    """One step (r: the registers, set only on the running state, so steps and state are one type)."""
    return f"STRUCT_PACK(o := '{o}', d := {d}, s := {s}, c := ({c})::HUGEINT, r := []::HUGEINT[])"


def _constants(steps: list[str]) -> str:
    """Steps whose fields are all numbers, as one short text read into steps (much less SQL to read)."""
    field = "SPLIT_PART(kf_p, ' ', {})"
    return (f"LIST_TRANSFORM(STRING_SPLIT('{';'.join(steps)}', ';'), kf_p -> STRUCT_PACK(o := {field.format(1)}, "
            f"d := {field.format(2)}::INTEGER, s := {field.format(3)}::INTEGER, c := {field.format(4)}::HUGEINT, "
            f"r := []::HUGEINT[]))")


def _ops(*steps: tuple) -> str:  # type: ignore[type-arg]
    """A SQL list of steps, each (o, d, s, c) with s and c 0 when left out."""
    parts: list[str] = []
    run: list[tuple] = []  # type: ignore[type-arg]

    def flush() -> None:
        if len(run) >= 3:
            parts.append(_constants([" ".join(str(f) for f in step) for step in run]))
        elif run:
            parts.append("[" + ", ".join(_op(*step) for step in run) + "]")
        run.clear()
    for step in steps:
        full = (*step, 0, 0)[:4]
        if isinstance(full[3], int):
            run.append(full)
        else:
            flush()
            parts.append(f"[{_op(*full)}]")
    flush()
    return parts[0] if len(parts) == 1 else f"LIST_CONCAT({', '.join(parts)})"


def _run(width: int, steps: str) -> str:
    """The hash register after ``steps`` (a SQL list of _op structs) run from all registers 0."""
    mask, half = (1 << width) - 1, width // 2
    v, src = "kf_r.r[kf_o.d]", "kf_r.r[kf_o.s]"
    value = (f"CASE kf_o.o WHEN 'set' THEN kf_o.c & {mask} WHEN 'mov' THEN {src} "
             f"WHEN 'add' THEN ({v} + kf_o.c) & {mask} WHEN 'addr' THEN ({v} + {src}) & {mask} "
             f"WHEN 'xor' THEN XOR({v}, {src}) WHEN 'xsr' THEN XOR({v}, {v} >> kf_o.s) "
             f"WHEN 'rotl' THEN (({v} << kf_o.s) & {mask}) | ({v} >> ({width} - kf_o.s)) "
             f"WHEN 'mul' THEN ({v} * (kf_o.c & {(1 << half) - 1}) + ((({v} * (kf_o.c >> {half})) & {(1 << half) - 1}) "
             f"<< {half})) & {mask} END")
    registers = ", ".join(f"CASE WHEN kf_o.d = {i} THEN kf_n ELSE kf_r.r[{i}] END" for i in range(1, _REGISTERS + 1))
    zero = ", ".join(["0::HUGEINT"] * _REGISTERS)
    start = f"STRUCT_PACK(o := '', d := 0, s := 0, c := 0::HUGEINT, r := [{zero}])"
    after = f"STRUCT_PACK(o := '', d := 0, s := 0, c := 0::HUGEINT, r := (LIST_TRANSFORM([{value}], kf_n -> [{registers}]))[1])"
    return f"(LIST_REDUCE(LIST_PREPEND({start}, {steps}), (kf_r, kf_o) -> {after})).r[{_H}]"


def _bytes(hexes: str) -> str:
    """The bytes (0-255) of hex text."""
    return f"LIST_TRANSFORM(RANGE(LENGTH({hexes}) // 2), kf_x -> ('0x' || SUBSTR({hexes}, 2 * kf_x + 1, 2))::BIGINT)"


def _word(b: str, start: str, size: int) -> str:
    """The little-endian ``size``-byte word at 0-based ``start`` of byte list ``b``."""
    return " | ".join(f"({b}[{start} + {j + 1}]::HUGEINT << {8 * j})" for j in range(size))


def _each(values: str, block) -> str:  # type: ignore[no-untyped-def]
    """``block(v)``'s steps for each value in the SQL list ``values``, in order."""
    return f"FLATTEN(LIST_TRANSFORM({values}, kf_v -> {block('kf_v')}))"


# ---- Murmur3 x86 32-bit (Murmur3_x86_32.java)

_C1, _C2 = 0xCC9E2D51, 0x1B873593


def _murmur_mix(k: str) -> str:
    """mixK1 of k, then mixH1 into the hash."""
    return _ops(("set", _T, 0, k), ("mul", _T, 0, _C1), ("rotl", _T, 15), ("mul", _T, 0, _C2),
                ("xor", _H, _T), ("rotl", _H, 13), ("mul", _H, 0, 5), ("add", _H, 0, 0xE6546B64))


def _murmur_end(length: str) -> str:
    """fmix(hash, length)."""
    return _ops(("set", _T, 0, length), ("xor", _H, _T), ("xsr", _H, 16), ("mul", _H, 0, 0x85EBCA6B),
                ("xsr", _H, 13), ("mul", _H, 0, 0xC2B2AE35), ("xsr", _H, 16))


def _murmur_item(item: str) -> str:
    hash_int = f"LIST_CONCAT({_murmur_mix(f'{item}.i')}, {_murmur_end('4')})"
    hash_long = (f"LIST_CONCAT({_murmur_mix(f'{item}.i')}, {_murmur_mix(f'{item}.i >> 32')}, {_murmur_end('8')})")

    def as_bytes(b: str) -> str:
        """hashUnsafeBytes: whole 4-byte words, then each byte left as a signed int."""
        words = f"LIST_TRANSFORM(RANGE(LEN({b}) // 4), kf_w -> {_word(b, '4 * kf_w', 4)})"
        tail = f"LIST_TRANSFORM({b}[LEN({b}) // 4 * 4 + 1:], kf_t -> CASE WHEN kf_t >= 128 THEN kf_t - 256 ELSE kf_t END)"
        return f"LIST_CONCAT({_each(f'LIST_CONCAT({words}, {tail})', _murmur_mix)}, {_murmur_end(f'LEN({b})')})"
    return (f"CASE {item}.k WHEN 'i' THEN {hash_int} WHEN 'l' THEN {hash_long} "
            f"ELSE {_let(_bytes(f'{item}.h'), as_bytes)} END")


# ---- xxHash64 (XXH64.java)

_P1, _P2, _P3, _P4, _P5 = (0x9E3779B185EBCA87, 0xC2B2AE3D27D4EB4F, 0x165667B19E3779F9,
                           0x85EBCA77C2B2AE63, 0x27D4EB2F165667C5)


def _xx_round(lane: int) -> tuple:  # type: ignore[type-arg]
    """lane = rotl(lane + T * P2, 31) * P1"""
    return (("mul", _T, 0, _P2), ("addr", lane, _T), ("rotl", lane, 31), ("mul", lane, 0, _P1))


def _xx_long(k: str) -> str:
    """hash ^= rotl(k * P2, 31) * P1; hash = rotl(hash, 27) * P1 + P4"""
    return _ops(("set", _T, 0, k), ("mul", _T, 0, _P2), ("rotl", _T, 31), ("mul", _T, 0, _P1), ("xor", _H, _T),
                ("rotl", _H, 27), ("mul", _H, 0, _P1), ("add", _H, 0, _P4))


def _xx_int(k: str) -> str:
    """hash ^= k * P1; hash = rotl(hash, 23) * P2 + P3"""
    return _ops(("set", _T, 0, k), ("mul", _T, 0, _P1), ("xor", _H, _T), ("rotl", _H, 23), ("mul", _H, 0, _P2),
                ("add", _H, 0, _P3))


def _xx_byte(k: str) -> str:
    """hash ^= k * P5; hash = rotl(hash, 11) * P1"""
    return _ops(("set", _T, 0, k), ("mul", _T, 0, _P5), ("xor", _H, _T), ("rotl", _H, 11), ("mul", _H, 0, _P1))


_XX_END = _ops(("xsr", _H, 33), ("mul", _H, 0, _P2), ("xsr", _H, 29), ("mul", _H, 0, _P3), ("xsr", _H, 32))


def _xx_item(item: str) -> str:
    hash_int = f"LIST_CONCAT({_ops(('add', _H, 0, _P5 + 4))}, {_xx_int(f'{item}.i & {(1 << 32) - 1}')}, {_XX_END})"
    hash_long = f"LIST_CONCAT({_ops(('add', _H, 0, _P5 + 8))}, {_xx_long(f'{item}.i')}, {_XX_END})"

    def as_bytes(b: str) -> str:
        """hashUnsafeBytes: 32-byte stripes, then 8-byte words, one 4-byte word, then single bytes."""
        n = f"LEN({b})"
        lanes = _ops(*(("mov", lane, _H) for lane in _V), ("add", _V[0], 0, _P1 + _P2), ("add", _V[1], 0, _P2),
                     ("add", _V[3], 0, (-_P1) & ((1 << 64) - 1)))
        stripe = _ops(*(step for q, lane in enumerate(_V)
                        for step in (("set", _T, 0, _word("kf_v", str(8 * q), 8)), *_xx_round(lane))))
        stripes = f"FLATTEN(LIST_TRANSFORM(LIST_TRANSFORM(RANGE({n} // 32), kf_s -> {b}[32 * kf_s + 1:32 * kf_s + 32]), kf_v -> {stripe}))"
        converge = _ops(("mov", _H, _V[0]), ("rotl", _H, 1), ("mov", _T, _V[1]), ("rotl", _T, 7), ("addr", _H, _T),
                        ("mov", _T, _V[2]), ("rotl", _T, 12), ("addr", _H, _T), ("mov", _T, _V[3]), ("rotl", _T, 18),
                        ("addr", _H, _T),
                        *(step for lane in _V for step in (("mov", _T, lane), ("mul", _T, 0, _P2), ("rotl", _T, 31),
                                                           ("mul", _T, 0, _P1), ("xor", _H, _T), ("mul", _H, 0, _P1),
                                                           ("add", _H, 0, _P4))))
        head = (f"CASE WHEN {n} >= 32 THEN LIST_CONCAT({lanes}, {stripes}, {converge}) "
                f"ELSE {_ops(('add', _H, 0, _P5))} END")
        done32 = f"({n} // 32 * 32)"
        longs = f"LIST_TRANSFORM(RANGE(({n} - {done32}) // 8), kf_l -> {_word(b, f'{done32} + 8 * kf_l', 8)})"
        done8 = f"({n} // 8 * 8)"
        word4 = f"CASE WHEN {n} - {done8} >= 4 THEN {_xx_int(_word(b, done8, 4))} ELSE [] END"
        rest = f"{b}[{done8} + CASE WHEN {n} - {done8} >= 4 THEN 4 ELSE 0 END + 1:]"
        return (f"LIST_CONCAT({head}, {_ops(('set', _T, 0, n), ('addr', _H, _T))}, {_each(longs, _xx_long)}, "
                f"{word4}, {_each(rest, _xx_byte)}, {_XX_END})")
    return (f"CASE {item}.k WHEN 'i' THEN {hash_int} WHEN 'l' THEN {hash_long} "
            f"ELSE {_let(_bytes(f'{item}.h'), as_bytes)} END")


# ---- what each value is hashed as

def _double_bits(d: str, float32: bool) -> str:
    """Java's floatToIntBits / doubleToLongBits of ``d`` (a DOUBLE), as a BIGINT (NaN as Java's one NaN)."""
    if float32:
        mant, bias, emin, nan, inf = 23, 127, -126, 0x7FC00000, 0x7F800000
    else:
        mant, bias, emin, nan, inf = 52, 1023, -1022, 0x7FF8000000000000, 0x7FF0000000000000
    sign = f"CASE WHEN {d} < 0 THEN {1 << (31 if float32 else 63)} ELSE 0 END"

    def body(a: str) -> str:
        e = f"FLOOR(LOG2({a}))::INTEGER"
        exact = (f"(LIST_TRANSFORM([{e}], kf_e -> CASE WHEN POW(2.0, kf_e) > {a} THEN kf_e - 1 "
                 f"WHEN POW(2.0, kf_e + 1) <= {a} THEN kf_e + 1 ELSE kf_e END))[1]")
        normal = _let(exact, lambda x: (
            f"CASE WHEN {x} < {emin} THEN ({a} * POW(2.0, {-emin}) * POW(2.0, {mant}))::HUGEINT "
            f"ELSE (({x} + {bias})::HUGEINT << {mant}) | (({a} / POW(2.0, {x}) - 1) * POW(2.0, {mant}))::HUGEINT END"))
        return (f"CASE WHEN ISNAN({d}) THEN {nan} WHEN {a} = 0 THEN 0 WHEN ISINF({d}) THEN {inf} | ({sign}) "
                f"ELSE {normal} | ({sign}) END")
    bits = _let(f"ABS({d})", body)
    if float32:
        return f"(CASE WHEN ({bits}) >= {1 << 31} THEN ({bits}) - {1 << 32} ELSE ({bits}) END)::BIGINT"
    return f"(CASE WHEN ({bits}) >= {1 << 63} THEN ({bits}) - {1 << 64} ELSE ({bits}) END)::BIGINT"


def _decimal_item(text: str, kind: str) -> str:
    """A decimal of type ``kind`` ('DECIMAL(p,s)') written as ``text``: its unscaled value as a long
    (up to 18 digits) or as BigInteger.toByteArray's bytes (big-endian two's complement, fewest bytes)."""
    scale = f"REGEXP_EXTRACT({kind}, ',(\\d+)\\)', 1)::INTEGER"
    precision = f"REGEXP_EXTRACT({kind}, '\\((\\d+),', 1)::INTEGER"
    unscaled = (f"((SPLIT_PART(LTRIM({text}, '-'), '.', 1) || RPAD(SPLIT_PART({text}, '.', 2), {scale}, '0'))::HUGEINT "
                f"* CASE WHEN {text} LIKE '-%' THEN -1 ELSE 1 END)")

    def as_bytes(u: str) -> str:
        size = (f"COALESCE((LIST_TRANSFORM([{u}], kf_u -> LIST_FILTER(RANGE(1, 16), kf_n -> "
                f"kf_u >= -(1::HUGEINT << (8 * kf_n - 1)) AND kf_u < (1::HUGEINT << (8 * kf_n - 1)))[1]))[1], 16)")
        hexes = (f"ARRAY_TO_STRING(LIST_TRANSFORM(RANGE({size} - 1, -1, -1), kf_q -> "
                 f"LPAD(HEX((({u} >> (8 * kf_q)) & 255)::INTEGER), 2, '0')), '')")
        return f"STRUCT_PACK(k := 'b', i := 0::BIGINT, h := {hexes})"
    return _let(unscaled, lambda u: (
        f"CASE WHEN {precision} <= 18 THEN STRUCT_PACK(k := 'l', i := {u}::BIGINT, h := '') ELSE {as_bytes(u)} END"))


def _item(text: str, kind: str, name: str) -> str:
    """The item to hash for a value of DuckDB type ``kind``, written as ``text`` (both SQL)."""
    def make(k: str, i: str = "0", h: str = "''") -> str:
        return f"STRUCT_PACK(k := '{k}', i := ({i})::BIGINT, h := {h})"
    return (
        f"CASE WHEN {kind} = 'BOOLEAN' THEN {make('i', f'CASE WHEN {text} = {chr(39)}true{chr(39)} THEN 1 ELSE 0 END')} "
        f"WHEN {kind} IN ('TINYINT', 'SMALLINT', 'INTEGER') THEN {make('i', f'{text}::BIGINT')} "
        f"WHEN {kind} = 'DATE' THEN {make('i', f'{text}::DATE - DATE {chr(39)}1970-01-01{chr(39)}')} "
        f"WHEN {kind} = 'BIGINT' THEN {make('l', f'{text}::BIGINT')} "
        f"WHEN {kind} = 'TIMESTAMP' THEN {make('l', f'EPOCH_US({text}::TIMESTAMP::TIMESTAMPTZ)')} "
        f"WHEN {kind} = 'TIMESTAMP WITH TIME ZONE' THEN {make('l', f'EPOCH_US({text}::TIMESTAMPTZ)')} "
        f"WHEN {kind} = 'FLOAT' THEN {make('i', _double_bits(f'{text}::FLOAT::DOUBLE', True))} "
        f"WHEN {kind} = 'DOUBLE' THEN {make('l', _double_bits(f'{text}::DOUBLE', False))} "
        f"WHEN {kind} LIKE 'DECIMAL(%' THEN {_decimal_item(text, kind)} "
        f"WHEN {kind} = 'VARCHAR' THEN {make('b', h=f'HEX(ENCODE({text}))')} "
        f"WHEN {kind} = 'BLOB' THEN {make('b', h=f'HEX({text}::BLOB)')} "
        f"ELSE ERROR('{name}() of a value of type ' || {kind} || ' can''t run on this PC the way it runs in Fabric, so nothing "
        f"ran. Run the cell in Fabric, or hash its parts.') END")


def _values(value: str) -> str:
    """An argument's values to hash, each its type and text: none for NULL, one for a value, one per
    element (NULLs skipped) for an array."""
    def body(k: str) -> str:
        texts = (f"CASE WHEN {k} LIKE '%[]' THEN JSON_EXTRACT_STRING(TO_JSON({value}), '$[*]') "
                 f"ELSE [CAST({value} AS VARCHAR)] END")
        return (f"CASE WHEN {value} IS NULL THEN [] ELSE LIST_TRANSFORM(LIST_FILTER({texts}, kf_v -> kf_v IS NOT NULL), "
                f"kf_v -> STRUCT_PACK(t := REGEXP_REPLACE({k}, '\\[\\]$', ''), v := kf_v)) END")
    return _let(f"TYPEOF({value})", body)


_BUILDING = threading.Lock()  # the name counter is shared: two builds at once would mix their lambda names


def hash_sql(name: str, count: int) -> str:
    """DuckDB SQL for Spark's ``hash`` (an INTEGER) or ``xxhash64`` (a BIGINT) of fj_arg0, fj_arg1, ..."""
    with _BUILDING:
        return _hash_sql(name, count)


def _hash_sql(name: str, count: int) -> str:
    _reset_names()
    values = [_values(f"fj_arg{i}") for i in range(count)]
    values_sql = f"LIST_CONCAT({', '.join(values)})" if count > 1 else values[0]
    items = f"LIST_TRANSFORM({values_sql}, kf_x -> {_item('kf_x.v', 'kf_x.t', name)})"
    width, item = (32, _murmur_item) if name == "hash" else (64, _xx_item)
    steps = f"LIST_CONCAT({_ops(('set', _H, 0, 42))}, FLATTEN(LIST_TRANSFORM({items}, kf_it -> {item('kf_it')})))"
    top = 1 << (width - 1)
    return _let(_run(width, steps), lambda h: (
        f"(CASE WHEN {h} >= {top} THEN {h} - {2 * top} ELSE {h} END)::{'INTEGER' if width == 32 else 'BIGINT'}"))
