"""Moving surrogate keys from LH_Keys (a lakehouse) into SQL_Platform, keeping every number.

Per key table: copy every key with its own number, then read the platform's copy back and
compare it row by row with LH_Keys. Only when every table matches does the environment switch
to the platform (``keys = "platform"`` in environments.toml). LH_Keys is left as it was, so
switching back is a one-line change.
"""

from __future__ import annotations

from typing import Any

import polars as pl

from ..meta import keys as keys_mod


def migrate(project: Any, lakehouse: Any, service: Any) -> dict[str, Any]:
    """Copy and check each key table. ``lakehouse`` is the LH_Keys store, ``service`` the platform's keys."""
    report: list[dict[str, Any]] = []
    for kt in sorted(project.key_tables.values(), key=lambda k: k.name):
        df = lakehouse.read(keys_mod.KEYS_LAKEHOUSE, keys_mod.KEY_SCHEMA, kt.table)
        if df is None:
            service.ensure(kt.name)
            report.append({"table": kt.name, "copied": 0, "checked": 0, "match": True, "note": "not in LH_Keys yet"})
            continue
        real = df.filter(pl.col(kt.sk) != keys_mod.UNKNOWN_KEY).sort(kt.sk)
        rows = [[int(r[kt.sk]), str(r[kt.bk]), str(r["create_date"])] for r in real.iter_rows(named=True)]
        out = {"imported": 0}
        for i in range(0, len(rows), 5000):
            got = service.client.call("import_keys", name=kt.name, rows=rows[i:i + 5000])
            out["imported"] += int(got.get("imported", 0))
        copy = {int(sk): bk for sk, bk, _ in service.export(kt.name) if int(sk) != keys_mod.UNKNOWN_KEY}
        source = {sk: bk for sk, bk, _ in rows}
        differ = [sk for sk in source if copy.get(sk) != source[sk]]
        extra = [sk for sk in copy if sk not in source]
        report.append({"table": kt.name, "copied": out["imported"], "checked": len(source),
                       "match": not differ, "differ": differ[:10], "extra_in_platform": len(extra)})
    return {"tables": report, "ok": all(r["match"] for r in report)}
