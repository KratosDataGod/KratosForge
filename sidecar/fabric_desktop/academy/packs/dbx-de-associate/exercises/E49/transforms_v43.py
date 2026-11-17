# Release 43, rolled back: the transforms as they were in that release. Don't change this file.


def clean_amount(text):
    """An amount as the store files write it ("1,234.50", " 12.00 ") as a float.
    A blank or missing amount stays missing (None): it isn't zero."""
    if text is None or str(text).strip() == "":
        return None
    return float(str(text).strip().replace(",", ""))


def region_code(text):
    """A region as a trimmed, upper-case code ("  uk " -> "UK"). Blank or missing is "UNKNOWN"."""
    if text is None or str(text).strip() == "":
        return "UNKNOWN"
    return str(text).strip().upper()


def latest_per_order(rows):
    """One row per order_id: the one with the latest updated_at (ISO text, so it sorts in time order).
    Returned in order_id order."""
    latest = {}
    for row in rows:
        key = row["order_id"]
        if key not in latest:
            latest[key] = row
    return [latest[k] for k in sorted(latest)]
