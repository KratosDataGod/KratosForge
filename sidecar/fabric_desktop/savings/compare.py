"""What one workload used on each platform, in each platform's own unit (Preview).

One card per platform: this PC (time), Fabric (CU), Databricks (DBU) and Snowflake (credits).
Each card says how the runs elsewhere were matched to the workload, the figure per run with its
classification, the input volume checked against this PC's runs, the method and the source:

- **This PC**: wall time, CPU and peak memory, Measured, as each run recorded them. Not priced.
- **Fabric**: CU per run of the Fabric runs that count by calibration's rules (``select_baselines``:
  the same workload, definition version and input scope, a valid CU figure, input volume within 25%).
  Estimated (run time x CU) or Measured, as the runs' own figures are.
- **Databricks**: billed DBUs per run (system.billing.usage, by ``job_run_id``) of the jobs the person
  linked to the workload. Billed usage is Measured, with its source.
- **Snowflake**: credits per query carrying a query tag that names the workload, worked out from
  warehouse time and the warehouse size's credits per hour (engines/snowflake_query_credits.py),
  so Estimated with its assumptions.

Money appears only under a pricing scenario: each figure priced at one version's rate for its own
unit (``pricing.price_native``), a Scenario figure. The closing sentence compares money only when at
least two platforms are priced under the same version, and says which were left out and why. It
names an Estimated figure "estimate" and a Measured one "figure", never says "savings" and never
recommends a platform.

Rules: units are never converted into each other; a figure that isn't known is None with the
reason, never 0; a run with no figure is counted beside the average, never filled in with it.

Pure: no I/O.
"""

from __future__ import annotations

import datetime as dt
import math
from typing import Any

from .. import calibration
from .. import measurement as ms
from ..engines.usage import UNITS as USAGE_UNITS
from ..engines.usage import UsageRecord
from . import pricing

PC, FABRIC, DATABRICKS, SNOWFLAKE = "pc", "fabric", "databricks", "snowflake"
LABELS = {PC: "This PC", FABRIC: "Fabric", DATABRICKS: "Databricks", SNOWFLAKE: "Snowflake"}
UNIT = {PC: "seconds", FABRIC: "fabric_cu_seconds", DATABRICKS: "databricks_dbu", SNOWFLAKE: "snowflake_credits"}
UNIT_LABEL = {PC: "time", FABRIC: "CU", DATABRICKS: "DBU", SNOWFLAKE: "credits"}
OK, UNKNOWN, NOT_CONNECTED, NOT_LINKED = "ok", "unknown", "not_connected", "not_linked"
DAYS = (7, 30)   # what the Databricks billing and Snowflake reads offer
BILLED_SOURCE = "Billed usage, system.billing.usage"
PC_MONEY = "Not priced. This PC's hardware and power aren't counted."
NO_SCENARIO = "Choose a pricing scenario to see money. Until then each platform shows its own unit, and nothing is compared."
CONNECT = {DATABRICKS: "To connect, add a Databricks connection in Settings > Connections. Reading billing also needs "
                       "access to system.billing.usage.",
           SNOWFLAKE: "To connect, add a Snowflake connection in Settings > Connections. Reading credits also needs "
                      "a role that can read query history."}
_SYMBOL = {"USD": "$", "EUR": "€", "GBP": "£"}


# ---------------------------------------------------------------- words and numbers

def money_text(value: float, currency: str) -> str:
    """$0.14, or 0.14 SEK for a currency without a symbol here. Small amounts keep their digits."""
    digits = 2 if value == 0 or value >= 0.01 else 4
    amount = f"{value:,.{digits}f}"
    return f"{_SYMBOL[currency]}{amount}" if currency in _SYMBOL else f"{amount} {currency}"


def rate_text(rate: float, currency: str) -> str:
    """A rate as entered: $0.18, $2.00, $0.055."""
    text = f"{rate:.2f}" if abs(rate * 100 - round(rate * 100)) < 1e-9 else f"{rate:.6f}".rstrip("0")
    return f"{_SYMBOL[currency]}{text}" if currency in _SYMBOL else f"{text} {currency}"


def number(value: float, digits: int = 2) -> str:
    return f"{value:,.{digits}f}"


def duration(seconds: float) -> str:
    """4 min 12 s; 45 s; 1 h 3 min."""
    s = int(round(seconds))
    if s < 60:
        return f"{s} s"
    if s < 3600:
        return f"{s // 60} min {s % 60} s" if s % 60 else f"{s // 60} min"
    return f"{s // 3600} h {(s % 3600) // 60} min"


def size_text(n: float) -> str:
    for unit, step in (("GB", 1e9), ("MB", 1e6), ("KB", 1e3)):
        if n >= step:
            return f"{n / step:.1f} {unit}"
    return f"{int(n)} bytes"


def rows_text(n: float) -> str:
    return f"{n / 1e6:.1f} M rows" if n >= 1e6 else f"{int(round(n)):,} rows"


def credits_per_hour(rate: float) -> str:
    return f"{rate:g} credit{'' if rate == 1 else 's'} per hour"


def _plural(n: int, word: str) -> str:
    return f"{n} {word}{'' if n == 1 else 's'}"


def _num(value: Any) -> float | None:
    """A recorded figure, or None when it wasn't recorded or isn't a usable number."""
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
        return None
    return float(value)


def _mean(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


# ---------------------------------------------------------------- shapes

def _figure(classification: str, value: float | None, unit: str, *, text: str = "", method: str = "",
            source: str = "", collected_at: Any = None, assumptions: list[str] | None = None, reason: str = "",
            runs: int = 0, runs_total: int = 0, period: dict[str, Any] | None = None) -> dict[str, Any]:
    """One per-run figure in a platform's own unit. Unknown carries None and its reason."""
    if classification == ms.UNKNOWN:
        value, text = None, "Not known"
        if not reason:
            raise ValueError("An unknown figure says why it isn't known.")
    elif value is None:
        raise ValueError("A known figure has a value; use Unknown with a reason instead.")
    return {"classification": classification, "label": ms.LABELS[classification], "value": value, "unit": unit,
            "unit_label": USAGE_UNITS.get(unit, unit), "text": text, "method": method, "source": source,
            "collected_at": collected_at, "assumptions": list(assumptions or []), "reason": reason,
            "runs": runs, "runs_total": runs_total, "period": period or {}}


def _card(platform: str, status: str, figure: dict[str, Any], **kw: Any) -> dict[str, Any]:
    out: dict[str, Any] = {"platform": platform, "label": LABELS[platform], "unit": UNIT[platform], "unit_label": UNIT_LABEL[platform],
           "status": status, "figure": figure, "sub": "", "matched_by": "", "input": "", "volume": None,
           "details": [], "excluded": [], "connect": "", "money": None, "priced": []}
    return out | kw


def _in_period(at: Any, start: dt.datetime, end: dt.datetime) -> bool:
    t = ms.parse_time(at)
    return t is not None and start <= t <= end


# ---------------------------------------------------------------- this PC

def _pc_card(runs: list[dict[str, Any]], period: dict[str, Any]) -> dict[str, Any]:
    total = len(runs)
    secs = [s for r in runs if (s := _num(r.get("seconds"))) is not None]
    source = next((str(r["source"]) for r in runs if r.get("source")), "Measured on this PC as each run ran")
    collected = max((str(r.get("at")) for r in runs if r.get("at")), default=None)
    if not secs:
        why = "No run of this workload on this PC in the period." if not total else \
            "The runs on this PC didn't record their time."
        fig = _figure(ms.UNKNOWN, None, "seconds", reason=why, runs=0, runs_total=total, period=period)
        return _card(PC, UNKNOWN, fig, matched_by=f"{_plural(total, 'run')} of the workload here.",
                     money={"status": "not_priced", "text": PC_MONEY})
    wall = sum(secs) / len(secs)
    fig = _figure(ms.MEASURED, round(wall, 3), "seconds", text=duration(wall), method="Wall-clock time, averaged per run",
                  source=source, collected_at=collected, runs=len(secs), runs_total=total, period=period)

    def detail(label: str, key: str, fmt: Any, unit: str) -> dict[str, Any]:
        vals = [v for r in runs if (v := _num(r.get(key))) is not None]
        if not vals:
            return {"label": label, "classification": ms.UNKNOWN, "value": None, "unit": unit, "text": "Not recorded",
                    "reason": f"{label} wasn't recorded for these runs."}
        avg = sum(vals) / len(vals)
        return {"label": label, "classification": ms.MEASURED, "value": round(avg, 3), "unit": unit, "text": fmt(avg),
                "reason": "" if len(vals) == total else f"Recorded for {len(vals)} of {total} runs."}

    rows = [v for r in runs if (v := _num(r.get("rows"))) is not None]
    return _card(PC, OK, fig, sub="Wall-clock time", matched_by=f"{_plural(total, 'run')} of the workload here.",
                 details=[detail("CPU", "cpu_seconds", duration, "cpu_seconds"),
                          detail("Peak memory", "peak_bytes", size_text, "bytes")],
                 input=f"{rows_text(sum(rows) / len(rows))} read" if rows else "Not recorded",
                 money={"status": "not_priced", "text": PC_MONEY})


def _pc_volume(runs: list[dict[str, Any]]) -> dict[str, Any]:
    """This PC's runs as one reference run for the volume check: their average input rows and bytes."""
    rows = [v for r in runs if (v := _num(r.get("rows"))) is not None]
    size = [v for r in runs if (v := _num(r.get("bytes"))) is not None]
    return {"run_id": "this PC's runs", "rows": _mean(rows), "bytes": _mean(size)}


def _volume_text(volume: dict[str, Any] | None, what: str) -> str:
    if not volume:
        return ""
    if volume["status"] == calibration.PASSED:
        return f"{what}. Within {calibration.VOLUME_TOLERANCE:.0%} of this PC's runs."
    if volume["status"] == calibration.FAILED:
        return f"{what}. {volume['reason']}"
    return f"{what}. The volume check didn't run." if what != "Not recorded" else \
        "Not recorded, so the volume check didn't run."


# ---------------------------------------------------------------- Fabric

def _fabric_card(workload: dict[str, Any], runs: list[dict[str, Any]], pc_runs: list[dict[str, Any]],
                 period: dict[str, Any], now: dt.datetime) -> dict[str, Any]:
    ref = {"workload_key": workload.get("key"), "definition_version": workload.get("definition_version"),
           "input_scope": workload.get("input_scope")} | _pc_volume(pc_runs)
    version, scope = workload.get("definition_version") or "not recorded", workload.get("input_scope") or "not recorded"
    chosen = calibration.select_baselines(ref, runs, now=now)
    counted, excluded = chosen["counted"], chosen["excluded"]
    matched = (f"{_plural(len(counted), 'run') if counted else 'Runs'} of the same workload, definition {version}, "
               f"input scope {scope}.")
    if not counted:
        why = "No Fabric run of this workload in the period." if not runs else \
            "No Fabric run counts: " + " ".join(sorted({x["reason"] for x in excluded}))
        fig = _figure(ms.UNKNOWN, None, UNIT[FABRIC], reason=why, runs_total=len(runs), period=period)
        return _card(FABRIC, UNKNOWN, fig, matched_by=matched, excluded=excluded)
    parts = [r["cu"] for r in counted]
    total = ms.total(parts, "fabric_job_cu", ms.CU_SECONDS)
    per_run = total["value"] / len(counted)
    fig = _figure(total["classification"], round(per_run, 3), UNIT[FABRIC], text=f"{per_run:,.0f} CU seconds",
                  method="; ".join(total["methods"]) or "As each run's CU figure says",
                  source=", ".join(total["sources"]), collected_at=max(str(r.get("at")) for r in counted),
                  assumptions=total["assumptions"], runs=len(counted), runs_total=len(runs), period=period)
    statuses = {r["volume"]["status"] for r in counted}
    volume = {"status": calibration.PASSED if statuses == {calibration.PASSED} else
              (calibration.NOT_RUN if calibration.PASSED not in statuses else "partly"),
              "reason": counted[0]["volume"]["reason"]}
    rows = [v for r in counted if (v := _num(r.get("rows"))) is not None]
    what = rows_text(sum(rows) / len(rows)) if rows else "Not recorded"
    if volume["status"] == "partly":
        n = sum(1 for r in counted if r["volume"]["status"] == calibration.PASSED)
        text = f"{what}. {n} of {len(counted)} runs within {calibration.VOLUME_TOLERANCE:.0%} of this PC's runs."
    else:
        text = _volume_text(volume, what)
    return _card(FABRIC, OK, fig, sub=f"{per_run / 3600:.2f} CU hours", matched_by=matched, input=text,
                 volume=volume, excluded=excluded)


# ---------------------------------------------------------------- Databricks

def _databricks_card(src: dict[str, Any] | None, period: dict[str, Any], start: dt.datetime,
                     end: dt.datetime) -> dict[str, Any]:
    src = src or {}
    unit = UNIT[DATABRICKS]
    if not src.get("connected"):
        fig = _figure(ms.UNKNOWN, None, unit, reason="There's no Databricks connection on this PC, so nothing was read.",
                      period=period)
        return _card(DATABRICKS, NOT_CONNECTED, fig, connect=CONNECT[DATABRICKS])
    jobs = [j for j in src.get("jobs") or [] if j.get("job_id")]
    if not jobs:
        fig = _figure(ms.UNKNOWN, None, unit, period=period,
                      reason="No Databricks job is linked to this workload. Link the job that runs it.")
        return _card(DATABRICKS, NOT_LINKED, fig)
    names = ", ".join(f"{j.get('name') or 'Job'} ({j['job_id']})" for j in jobs)
    matched = f"Job {names}, linked to this workload by you." if len(jobs) == 1 else \
        f"Jobs {names}, linked to this workload by you."
    read = src.get("read") or {}
    ids = {str(j["job_id"]) for j in jobs}
    if not read:
        why = "Databricks billing hasn't been read on this PC yet."
    elif read.get("runs") is None:
        why = "The last billing read has no per-run figures. Read Databricks billing again."
    else:
        why = ""
    rows = [r for r in read.get("runs") or [] if str(r.get("job_id")) in ids and r.get("job_run_id")
            and _num(r.get("dbu")) is not None and _in_period(r.get("started"), start, end)]
    if not why and not rows:
        why = ("No billing rows for this job's runs yet. Billing rows can arrive hours after a run."
               if len(jobs) == 1 else "No billing rows for these jobs' runs yet. Billing rows can arrive hours after a run.")
    if why:
        fig = _figure(ms.UNKNOWN, None, unit, reason=why, period=period, collected_at=read.get("read_at"))
        return _card(DATABRICKS, UNKNOWN, fig, matched_by=matched, read_at=read.get("read_at"))
    # One billed record per run and SKU; same-unit, same-kind records add, nothing else does.
    by_sku: dict[str, UsageRecord] = {}
    for r in rows:
        rec = UsageRecord("databricks", unit, float(r["dbu"]), "billed", source=BILLED_SOURCE, at=str(r.get("started")))
        sku = str(r.get("sku") or "")
        by_sku[sku] = by_sku[sku].add(rec) if sku in by_sku else rec
    runs = len({str(r["job_run_id"]) for r in rows})
    per_sku = {sku: rec.amount / runs for sku, rec in sorted(by_sku.items())}
    per_run = sum(per_sku.values())
    fig = _figure(ms.MEASURED, round(per_run, 6), unit, text=f"{number(per_run)} DBU",
                  method="Billed DBUs for the job's runs, averaged", source=BILLED_SOURCE,
                  collected_at=read.get("read_at"), runs=runs, runs_total=runs, period=period)
    return _card(DATABRICKS, OK, fig, sub=", ".join(s or "SKU not recorded" for s in per_sku),
                 matched_by=f"{matched} {_plural(runs, 'run')}.",
                 input="Not recorded, so the volume check didn't run.",
                 volume={"status": calibration.NOT_RUN, "reason": "Databricks billing has no input rows or bytes."},
                 read_at=read.get("read_at"), priced=[{"sku": s, "amount": round(a, 6)} for s, a in per_sku.items()])


# ---------------------------------------------------------------- Snowflake

def _snowflake_card(src: dict[str, Any] | None, pc_runs: list[dict[str, Any]], period: dict[str, Any],
                    start: dt.datetime, end: dt.datetime) -> dict[str, Any]:
    src = src or {}
    unit = UNIT[SNOWFLAKE]
    if not src.get("connected"):
        fig = _figure(ms.UNKNOWN, None, unit, reason="There's no Snowflake connection on this PC, so nothing was read.",
                      period=period)
        return _card(SNOWFLAKE, NOT_CONNECTED, fig, connect=CONNECT[SNOWFLAKE])
    tags = [str(t) for t in src.get("tags") or [] if t]
    if not tags:
        fig = _figure(ms.UNKNOWN, None, unit, period=period,
                      reason="No Snowflake query tag is linked to this workload. Link the tag its queries set.")
        return _card(SNOWFLAKE, NOT_LINKED, fig)
    read = src.get("read") or {}
    queries = [q for q in read.get("queries") or [] if q.get("tag") in tags and _in_period(q.get("start"), start, end)]
    warehouses = sorted({str(q.get("warehouse")) for q in queries if q.get("warehouse")})
    matched = f"Queries tagged {', '.join(tags)}" + (f" on warehouse {', '.join(warehouses)}." if warehouses else ".")
    if not read:
        why = "Snowflake query history hasn't been read on this PC yet."
    elif not queries:
        why = "No query with this tag in the period. Query history can be up to 45 minutes late."
    else:
        why = ""
    known = [q for q in queries if _num(q.get("credits")) is not None]
    if not why and not known:
        why = "The credits of these queries can't be worked out: " + " ".join(
            sorted({str(q.get("reason") or "not known") for q in queries}))
    if why:
        fig = _figure(ms.UNKNOWN, None, unit, reason=why, runs_total=len(queries), period=period,
                      collected_at=read.get("read_at"))
        return _card(SNOWFLAKE, UNKNOWN, fig, matched_by=matched, read_at=read.get("read_at"))
    per_run = sum(float(q["credits"]) for q in known) / len(known)
    secs = sum(float(q.get("seconds") or 0) for q in known) / len(known)
    rates = sorted({(str(q.get("size")), float(q["credits_per_hour"])) for q in known})
    rate_words = ", ".join(f"{s}, {credits_per_hour(c)}" for s, c in rates)
    method = (f"Warehouse time {duration(secs)} times {credits_per_hour(rates[0][1])}" if len(rates) == 1 else
              f"Warehouse time {duration(secs)} times each warehouse size's credits per hour")
    fig = _figure(ms.ESTIMATED, round(per_run, 6), unit, text=f"{number(per_run)} credits", method=method,
                  source=f"QUERY_HISTORY for queries tagged {', '.join(tags)}", collected_at=read.get("read_at"),
                  assumptions=list(read.get("assumptions") or []), runs=len(known), runs_total=len(queries),
                  period=period)
    scanned = [v for q in known if (v := _num(q.get("bytes_scanned"))) is not None]
    volume = calibration.volume_check(_pc_volume(pc_runs), {"run_id": "Snowflake", "bytes": _mean(scanned)})
    what = f"{size_text(sum(scanned) / len(scanned))} scanned" if scanned else "Not recorded"
    text = _volume_text(volume, what) if scanned else "Not recorded, so the volume check didn't run."
    return _card(SNOWFLAKE, OK, fig, sub=rate_words, matched_by=matched + f" {_plural(len(queries), 'run')}.",
                 input=text, volume=volume, read_at=read.get("read_at"),
                 priced=[{"sku": "", "amount": round(per_run, 6)}],
                 note=("WAREHOUSE_METERING_HISTORY has what a warehouse was billed, for the whole warehouse per hour. "
                       "It can't be split into single runs, and it can be up to 3 hours late."))


# ---------------------------------------------------------------- money and the closing sentence

def _price(card: dict[str, Any], version: dict[str, Any]) -> dict[str, Any]:
    """The card's per-run figure priced at ``version``: Scenario, or Unknown with the reason."""
    unit, cur = card["unit"], version["currency"]
    base = {"pricing_version": None, "currency": cur, "text": "Not known", "price_line": ""}
    if card["status"] == NOT_CONNECTED:
        return base | {"classification": ms.UNKNOWN, "value": None, "status": "no_figure",
                       "text": f"No figure. {card['label']} isn't connected.", "reason": "Not connected."}
    if card["figure"]["value"] is None:
        word = {DATABRICKS: "DBU", SNOWFLAKE: "credits", FABRIC: "CU"}[card["platform"]]
        return base | {"classification": ms.UNKNOWN, "value": None, "status": "unknown",
                       "reason": f"There's no {word} figure to price."}
    if card["platform"] == FABRIC:
        lines = [{"sku": "", "amount": card["figure"]["value"]}]
    else:
        lines = card["priced"]
    parts, words = [], []
    for line in lines:
        m = pricing.price_native(line["amount"], unit, version, sku=line["sku"])
        if m["classification"] == ms.UNKNOWN:
            return base | {"classification": ms.UNKNOWN, "value": None, "status": "no_rate", "reason": m["reason"]}
        parts.append(m)
        rate = rate_text(m["rate"], cur)
        if card["platform"] == FABRIC:
            words.append(f"{m['quantity']:.2f} CU hours × {rate} per CU hour")
        elif card["platform"] == DATABRICKS:
            words.append(f"{number(m['quantity'])} DBU × {rate} per DBU" + (f" ({line['sku']})" if len(lines) > 1 else ""))
        else:
            words.append(f"{number(m['quantity'])} credits × {rate} per credit")
    total = ms.total(parts, "platform_price", ms.CURRENCY, currency=cur)
    return {"classification": ms.SCENARIO, "label": ms.LABELS[ms.SCENARIO], "value": round(total["value"], 6),
            "currency": cur, "status": "priced", "text": money_text(total["value"], cur), "price_line": " + ".join(words),
            "pricing_version": version["pricing_version"], "assumptions": total["assumptions"],
            "source": version["source"], "reason": ""}


def _noun(card: dict[str, Any]) -> str:
    return f"the {card['label']} {'estimate' if card['figure']['classification'] == ms.ESTIMATED else 'figure'}"


def _left_out(card: dict[str, Any]) -> str:
    name = card["label"]
    if card["status"] == NOT_CONNECTED:
        return f"{name} isn't compared because it isn't connected."
    if card["status"] == NOT_LINKED:
        return f"{name} isn't compared because nothing is linked to this workload."
    if card["figure"]["value"] is None:
        return f"{name} isn't compared because its figure isn't known."
    return f"{name} isn't compared because the scenario has no price for it."


def _join(items: list[str]) -> str:
    return items[0] if len(items) == 1 else ", ".join(items[:-1]) + " and " + items[-1]


def sentence(cards: list[dict[str, Any]]) -> dict[str, Any] | None:
    """The closing line: money compared only between cards priced under one pricing version, two or
    more. Equal amounts (as shown) are said to be the same. Never a recommendation."""
    priced = [c for c in cards if (c.get("money") or {}).get("status") == "priced"]
    versions = {c["money"]["pricing_version"] for c in priced}
    left = [_left_out(c) for c in cards if c["platform"] != PC and (c.get("money") or {}).get("status") != "priced"]
    if len(priced) < 2 or len(versions) != 1:
        return None
    cur = priced[0]["money"]["currency"]
    order = sorted(priced, key=lambda c: (round(c["money"]["value"], 2 if c["money"]["value"] >= 0.01 else 4),
                                          list(LABELS).index(c["platform"])))
    shown = [money_text(c["money"]["value"], cur) for c in order]
    named = [f"{_noun(c)} ({s})" for c, s in zip(order, shown, strict=True)]
    lowest = [n for n, s in zip(named, shown, strict=True) if s == shown[0]]
    higher = named[len(lowest):]
    if not higher:
        text = f"At these prices, {_join(lowest)} are the same."
    else:
        verb = "is" if len(lowest) == 1 else "are"
        text = f"At these prices, {_join(lowest)} {verb} lower than {_join(higher)}."
    return {"classification": ms.SCENARIO, "label": ms.LABELS[ms.SCENARIO], "pricing_version": versions.pop(),
            "text": " ".join([text, *left]), "platforms": [c["platform"] for c in order], "left_out": left}


# ---------------------------------------------------------------- the comparison

def compare(workload: dict[str, Any], *, pc_runs: list[dict[str, Any]], fabric_runs: list[dict[str, Any]],
            databricks: dict[str, Any] | None = None, snowflake: dict[str, Any] | None = None,
            scenario: dict[str, Any] | None = None, days: int = 30, now: dt.datetime | None = None) -> dict[str, Any]:
    """The four cards for one workload over the last ``days``, the money under ``scenario`` (one
    pricing version, or None for native units only) and the closing sentence.

    ``workload``: key, name, definition_version, input_scope. ``pc_runs``: run_id, at, seconds,
    cpu_seconds, peak_bytes, rows, bytes (each None when not recorded). ``fabric_runs``: as
    calibration.check_baseline takes them. ``databricks``: connected, jobs [{job_id, name}], read
    (databricks_billing's last read, with ``runs`` per job_run_id). ``snowflake``: connected, tags,
    read (snowflake_query_credits' last read)."""
    if days not in DAYS:
        raise ValueError(f"Compare the last {', '.join(map(str, DAYS))} days.")
    if not str(workload.get("key") or "").strip():
        raise ValueError("Pick a workload to compare.")
    now = now or dt.datetime.now(dt.timezone.utc)
    start = now - dt.timedelta(days=days)
    period = {"days": days, "start": start.isoformat(timespec="seconds"), "end": now.isoformat(timespec="seconds")}
    pcs = [r for r in pc_runs if _in_period(r.get("at"), start, now)]
    fabric = [r for r in fabric_runs if _in_period(r.get("at"), start, now)]
    cards = [_pc_card(pcs, period), _fabric_card(workload, fabric, pcs, period, now),
             _databricks_card(databricks, period, start, now), _snowflake_card(snowflake, pcs, period, start, now)]
    out: dict[str, Any] = {"workload": workload, "period": period, "scenario": None, "cards": cards,
                           "comparison": None, "note": ""}
    if scenario is None:
        out["note"] = NO_SCENARIO
        return out
    out["scenario"] = {k: scenario.get(k) for k in ("scenario", "pricing_version", "currency", "source", "confidence",
                                                     "pricing_model", "effective_from", "rate_per_cu_hour",
                                                     "rate_per_dbu", "rate_per_credit")}
    for card in cards:
        if card["platform"] != PC:
            card["money"] = _price(card, scenario)
    out["comparison"] = sentence(cards)
    if out["comparison"] is None:
        out["note"] = "Fewer than two platforms have money at this scenario, so nothing is compared."
    return out
