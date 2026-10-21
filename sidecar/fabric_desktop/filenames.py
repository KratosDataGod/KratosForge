"""File names built from names people chose (a lakehouse, a query, a report)."""


def file_stem(name: str, default: str = "SQL") -> str:
    """A name safe to use in a file name: letters, digits, spaces, - and _ only (no separators or ..)."""
    return "".join(ch for ch in (name or default) if ch.isalnum() or ch in " -_").strip()[:60] or default
