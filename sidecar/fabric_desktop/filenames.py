"""File names built from names people chose (a lakehouse, a query, a report)."""

# Names Windows keeps for devices, with any extension: a file called NUL.ipynb is the null device, so
# what is written to it is lost, and CON.json can't be swapped into place. ¹²³ count as digits there.
WINDOWS_DEVICES = frozenset({"CON", "PRN", "AUX", "NUL", "CONIN$", "CONOUT$", "CLOCK$",
                             *(f"{d}{n}" for d in ("COM", "LPT") for n in "0123456789¹²³")})


def file_stem(name: str, default: str = "SQL") -> str:
    """A name safe to use in a file name: letters, digits, spaces, - and _ only (no separators or ..).
    A name Windows keeps for a device (CON, NUL, COM1, ...) gets ``default`` after it: "CON notebook"."""
    stem = "".join(ch for ch in (name or default) if ch.isalnum() or ch in " -_").strip()[:60] or default
    return f"{stem} {default.lower()}" if stem.upper() in WINDOWS_DEVICES else stem
