"""What kind of failure a run's error message most likely is, and whether retrying alone helps.

A hint, matched from the error text against known patterns, never a diagnosis: it is shown as
"Likely: ..." beside the evidence, and anything it doesn't recognize is "Unrecognized". The
order matters: the first pattern that matches wins.
"""

from __future__ import annotations

import re
from typing import Any

# (category, label, retry_helps, hint, patterns)
_RULES: list[tuple[str, str, bool, str, tuple[str, ...]]] = [
    ("access", "Access", False,
     "The identity the run used can't read or write something. Check its workspace role or item permissions.",
     (r"\b40[13]\b", r"forbidden", r"unauthori[sz]ed", r"permission ?(denied|error)", r"insufficientscopes",
      r"authorizationpermissionmismatch", r"access (is )?denied")),
    ("throttled", "Throttled or capacity", True,
     "Fabric asked to slow down, or the capacity is busy or paused. A later retry usually works.",
     (r"\b429\b", r"too many requests", r"capacity (is )?(paused|unavailable|limit)", r"throttl")),
    ("network", "Network", True,
     "The run couldn't reach a service. A retry usually works once the network is back.",
     (r"timed? ?out", r"connectionerror", r"connection (reset|refused|aborted)", r"name resolution",
      r"failed to establish", r"temporar(il)?y unavailable", r"\b50[234]\b")),
    ("busy", "Table busy", True,
     "Another writer held the table. A retry runs once it is free.",
     (r"leasebusy", r"\blease\b", r"commit(failed|conflict)", r"concurrent (write|modification)")),
    ("source_missing", "Source missing", False,
     "The input wasn't there when the run started. Check that the upstream load ran and wrote to the expected path.",
     (r"matched no files", r"filenotfound", r"no such file", r"path does not exist", r"source .* not found")),
    ("schema", "Code or schema", False,
     "A column or field the code expects is missing or has another type. Retrying alone won't fix it; "
     "check schema drift or the last change to the code.",
     (r"keyerror", r"column ['\"]?[\w.]+['\"]? (not found|does not exist)", r"columnnotfound", r"schema",
      r"schemaerror", r"cannot cast", r"invalid type")),
    ("data_quality", "Data quality", False,
     "A check stopped the load. Review the failed check and the quarantined rows.",
     (r"check(s)? failed", r"quarantin", r"expectation", r"data quality")),
    ("code", "Code", False,
     "The transformation raised an error. Retrying alone won't fix it; open the run for the traceback.",
     (r"traceback", r"\w+error\b", r"exception")),
]


def classify(message: str | None) -> dict[str, Any]:
    """{category, label, retry_helps, hint} for a run's error message."""
    low = (message or "").lower()
    for category, label, retry, hint, patterns in _RULES:
        if any(re.search(p, low) for p in patterns):
            return {"category": category, "label": label, "retry_helps": retry, "hint": hint}
    return {"category": "unrecognized", "label": "Unrecognized", "retry_helps": None,
            "hint": "The error didn't match a known pattern. Open the run for its details."}
