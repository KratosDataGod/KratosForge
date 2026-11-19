"""An Apache Ossie document checked against the pinned JSON Schema for the version it declares (OSSIE-003a).

The schemas are vendored in ``_vendor/ossie_schema`` (see its README for the commits and hashes) and read from
there; nothing is fetched. Each check uses a ``referencing`` registry that holds only that vendored schema and has
no way to retrieve anything, so a ``$ref`` to any other URL is unresolved and reported, never downloaded.

``validate`` takes a parsed document or untrusted text. Text goes through ``model_ossie.load_text``, the same
size limit and alias-refusing safe loader ``model_ossie.parse`` uses. The result lists diagnostics, each with a
JSON path, the schema rule it breaks, a short form of the value, a message and a fix, sorted by path. A version
with no pinned schema gets one named diagnostic and is never a pass. The result informs and never blocks an import
(``EFFECT``). The check stops after MAX_RAW_ERRORS schema errors, and runs on the schema with its local ``$ref``s
inlined (``inline_local_refs``), so a file that is wrong everywhere costs bounded time.

Which rules apply is the document's profile (OSSIE-003b, ``ossie_profiles``): ``validate`` detects it from the
version and layout when none is given, and ``validate_files`` checks the limits Fabric sets on a set of files.
"""

from __future__ import annotations

import functools
import json
import re
import reprlib
from pathlib import Path
from typing import Any

from . import model_ossie

FOLDER = Path(__file__).resolve().parent / "_vendor" / "ossie_schema"
_RAW = "https://raw.githubusercontent.com/apache/ossie/{commit}/core-spec/ossie-schema.json"
# Version -> the vendored file, its upstream commit and the key a vendor's own data goes under in that version.
SCHEMAS: dict[str, dict[str, str]] = {
    "0.2.0.dev0": {"file": "ossie-schema-0.2.0.dev0.json", "commit": "21d14d7a4044e71a19ea46d024c974a57323174a",  # pragma: allowlist secret (upstream git commit sha)
                   "extensions": "custom_extensions"},
    "0.3.0.dev0": {"file": "ossie-schema-0.3.0.dev0.json", "commit": "aa500d37894c3d62c96cdce4192cbecea4d7f06a",  # pragma: allowlist secret (upstream git commit sha)
                   "extensions": "nonportable_extensions"},
}
MAX_DIAGNOSTICS = 200  # more than this are counted in one "N more" line
# The check stops after this many schema errors, so a file that is wrong everywhere costs bounded time; the rest
# are then counted as "at least N more". Errors arrive in document order, so those shown are from the file's start.
MAX_RAW_ERRORS = 5_000
# The result informs and never blocks: Import goes ahead whatever it says, because the converter already refuses
# what it can't read. A caller that wants to stop on it decides that itself.
EFFECT = "informs"
MAX_VALUE = 80  # characters of a value's short form
MAX_DEPTH = 64  # a real Ossie model nests about 8 levels; deeper is refused before the schema check recurses
_ONCE = frozenset({"required", "additionalProperties"})  # one diagnostic per property, however often it is seen
_IDENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_REPR = reprlib.Repr(maxlevel=2, maxdict=4, maxlist=4, maxtuple=4, maxset=4, maxstring=MAX_VALUE - 10,
                     maxother=MAX_VALUE - 10, maxlong=40)
_ARTICLE = {"object": "an object", "array": "a list", "string": "text", "integer": "a whole number",
            "number": "a number", "boolean": "true or false", "null": "null"}


def _schema(version: str) -> dict[str, Any]:
    with open(FOLDER / SCHEMAS[version]["file"], encoding="utf-8") as f:
        return dict(json.load(f))


def _validator_for(schema: dict[str, Any]) -> Any:
    """A draft 2020-12 validator whose registry holds only ``schema`` (plus jsonschema's bundled metaschemas)
    and retrieves nothing, so any other ``$ref`` is unresolvable. No format checker, so no format reaches out."""
    from jsonschema import Draft202012Validator
    from referencing import Registry, Resource
    from referencing.jsonschema import DRAFT202012

    resource = Resource.from_contents(schema, default_specification=DRAFT202012)
    registry: Registry[Any] = Registry().with_resource(str(schema.get("$id") or ""), resource)
    return Draft202012Validator(schema, registry=registry)


def _pointer(root: Any, ref: str) -> Any:
    node = root
    for token in ref[2:].split("/"):
        token = token.replace("~1", "/").replace("~0", "~")
        node = node[int(token)] if isinstance(node, list) else node[token]
    return node


MAX_INLINED_NODES = 100_000  # a diamond of $defs grows exponentially when inlined; past this, keep the plain schema


class _TooLarge(Exception):
    """The inlined schema would pass MAX_INLINED_NODES."""


def inline_local_refs(schema: dict[str, Any]) -> dict[str, Any]:
    """``schema`` with each ``{"$ref": "#/..."}`` that has no other keys replaced by what it points at, so the
    check doesn't look each one up again for every value (about a third of its time). A ref that points back into
    itself, a ref with other keys beside it and any other ref (another document, a URL) stay as they are, and
    ``$defs`` stays, so the result validates exactly as ``schema`` does. ``schema`` itself is not changed.
    When inlining would pass ``MAX_INLINED_NODES`` objects and lists (``$defs`` that refer to each other as a
    diamond), ``schema`` is returned unchanged instead, which validates the same, only slower."""
    count = [0]

    def resolve(node: Any, active: frozenset[str]) -> Any:
        if isinstance(node, (list, dict)):
            count[0] += 1
            if count[0] > MAX_INLINED_NODES:
                raise _TooLarge
        if isinstance(node, list):
            return [resolve(v, active) for v in node]
        if not isinstance(node, dict):
            return node
        ref = node.get("$ref")
        if isinstance(ref, str) and ref.startswith("#/") and len(node) == 1 and ref not in active:
            try:
                target = _pointer(schema, ref)
            except (KeyError, IndexError, ValueError, TypeError):  # narrow: a dangling pointer stays a $ref
                return dict(node)
            return resolve(target, active | {ref})
        return {k: resolve(v, active) for k, v in node.items()}

    try:
        return dict(resolve(schema, frozenset()))
    except _TooLarge:
        return schema


@functools.lru_cache(maxsize=len(SCHEMAS))
def _validator(version: str) -> Any:
    return _validator_for(inline_local_refs(_schema(version)))


def schema_info(version: str) -> dict[str, str]:
    """Where the pinned schema for ``version`` came from."""
    commit = SCHEMAS[version]["commit"]
    return {"version": version, "commit": commit, "url": _RAW.format(commit=commit)}


def json_path(parts: Any) -> str:
    """``$.datasets[0].fields[2].name`` for the parts ("datasets", 0, "fields", 2, "name")."""
    out = "$"
    for p in parts:
        if isinstance(p, int) and not isinstance(p, bool):
            out += f"[{p}]"
        elif isinstance(p, str) and _IDENT.fullmatch(p):
            out += f".{p}"
        else:
            out += "[" + json.dumps(_short_text(str(p)), ensure_ascii=False) + "]"
    return out


def _short_text(text: str) -> str:
    return text if len(text) <= MAX_VALUE else f"{text[:MAX_VALUE - 1]}…"


def short(value: Any) -> str:
    """A value's short form, at most MAX_VALUE characters (reprlib never builds the whole repr of a big value)."""
    return _short_text(_REPR.repr(value))


def _sort_key(d: dict[str, Any]) -> tuple[Any, ...]:
    return (tuple((0, p, "") if isinstance(p, int) else (1, 0, str(p)) for p in d["_parts"]), d["rule"], d["message"])


def _diag(parts: Any, rule: str, value: str, message: str, fix: str) -> dict[str, Any]:
    return {"_parts": tuple(parts), "path": json_path(parts), "rule": rule, "value": value, "message": message,
            "fix": fix}


def _shape(doc: Any) -> list[dict[str, Any]]:
    """Problems the schema check can't be trusted with, found without recursion: nesting deeper than MAX_DEPTH
    (the check would recurse that deep) and keys that aren't text (YAML allows them, JSON doesn't). Only
    containers are visited, and a path is built only for a problem (each entry links to its parent)."""
    found: list[dict[str, Any]] = []
    stack: list[tuple[Any, int, Any]] = [(doc, 0, None)]  # (value, depth, (parent link, key) or None)
    containers = (dict, list)
    while stack:
        value, depth, link = stack.pop()
        if depth > MAX_DEPTH:
            return [_diag(_parts(link)[:MAX_DEPTH], "depth", "", f"nested more than {MAX_DEPTH} levels deep",
                          "An Ossie model nests a few levels; take out the extra nesting.")]
        if isinstance(value, dict):
            for k, v in value.items():
                if not isinstance(k, str):
                    found.append(_diag(_parts(link), "key", short(k), f"has a key that isn't text: {short(k)}",
                                       "Quote the key so it is text."))
                if isinstance(v, containers):
                    stack.append((v, depth + 1, (link, k if isinstance(k, str) else str(k))))
        else:  # a list
            stack.extend((v, depth + 1, (link, i)) for i, v in enumerate(value) if isinstance(v, containers))
    return found


def _parts(link: Any) -> tuple[Any, ...]:
    out = []
    while link is not None:
        link, key = link
        out.append(key)
    return tuple(reversed(out))


def _type_names(types: Any) -> str:
    names = [types] if isinstance(types, str) else list(types or [])
    return " or ".join(_ARTICLE.get(t, t) for t in names)


def _message(error: Any) -> tuple[str, str]:
    """A message and a fix for one schema error, written from the rule, not from jsonschema's text (which holds
    the full repr of the value)."""
    rule, expected = error.validator, error.validator_value
    if rule == "type":
        return f"should be {_type_names(expected)}", f"Write it as {_type_names(expected)}."
    if rule == "required":
        missing = [p for p in expected if isinstance(error.instance, dict) and p not in error.instance]
        return f"needs {' and '.join(short(p) for p in missing)}", f"Add {' and '.join(missing)}."
    if rule == "enum":
        listed = ", ".join(short(v) for v in list(expected)[:12]) + (", …" if len(expected) > 12 else "")
        return f"should be one of {listed}", "Use one of the listed values."
    if rule == "const":
        return f"should be {short(expected)}", f"Write {short(expected)}."
    if rule == "minLength":
        return ("should not be empty", "Fill it in.") if expected == 1 else \
            (f"should be at least {expected} characters", "Write a longer value.")
    if rule == "maxLength":
        return f"should be at most {expected} characters", "Write a shorter value."
    if rule == "minItems":
        return f"should have at least {expected} item{'s' if expected != 1 else ''}", "Add the missing items."
    if rule == "maxItems":
        return f"should have at most {expected} items", "Take out the extra items."
    if rule == "uniqueItems":
        return "has the same item twice", "Take out the repeated item."
    if rule == "pattern":
        return f"should match {short(expected)}", "Change the value to match the pattern."
    if rule in ("minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum"):
        return f"breaks {rule} {short(expected)}", "Change the number."
    return f"breaks the schema's {rule} rule", "Change it to match the Ossie specification."


def _flatten(error: Any, ext_key: str) -> list[dict[str, Any]]:
    """Diagnostics for one jsonschema error: one per missing required property, one per unexpected property,
    and for anyOf and oneOf the branch the value nearly matched (else one line for the rule)."""
    parts = tuple(error.absolute_path)
    rule, instance = error.validator, error.instance
    if rule == "required" and isinstance(instance, dict):
        return [_diag((*parts, p), "required", "(missing)", f"is required{_where(parts)}", f"Add {p}.")
                for p in error.validator_value if p not in instance]
    if rule == "additionalProperties" and isinstance(instance, dict):
        props = error.schema.get("properties") or {}
        # Python's re, not ECMA-262 as JSON Schema specifies (and jsonschema uses re too). The pinned schemas have
        # no pattern or patternProperties (tests/test_ossie_schema.py checks), so review this before vendoring one.
        patterns = list((error.schema.get("patternProperties") or {}).keys())
        extra = [k for k in instance if k not in props and not any(re.search(p, str(k)) for p in patterns)]
        return [_diag((*parts, k), "additionalProperties", short(instance[k]), f"isn't a property here{_where(parts)}",
                      f"Remove {short(k)}, or keep vendor-specific data under {ext_key}.") for k in extra]
    if rule in ("anyOf", "oneOf") and error.context:
        deeper = [e for e in error.context if len(e.absolute_path) > len(parts)]
        if deeper:
            branches: dict[Any, list[Any]] = {}
            for e in error.context:
                branches.setdefault(e.relative_schema_path[0], []).append(e)
            near = [b for b in branches.values() if any(len(e.absolute_path) > len(parts) for e in b)]
            best = min(near, key=len)  # the branch whose type matched, with the fewest problems
            return [d for e in best for d in _flatten(e, ext_key)]
        options = sorted({_message(e)[0] for e in error.context})
        return [_diag(parts, rule, short(instance), "matches none of its allowed forms: " + "; ".join(options[:6]),
                      "Write it in one of those forms.")]
    message, fix = _message(error)
    return [_diag(parts, rule, short(instance), message, fix)]


def _where(parts: tuple[Any, ...]) -> str:
    return f" in {json_path(parts)}" if parts else ""


def _result(version: str, diagnostics: list[dict[str, Any]], checked: bool, total: int | None = None,
            stopped: bool = False, profile: str = "", warnings: list[dict[str, Any]] | None = None,
            unverified: bool = False, rules: list[str] | None = None,
            notes: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """The answer: diagnostics sorted by path, cut at MAX_DIAGNOSTICS with one ``more`` line for the rest.
    ``total`` is how many there were when the caller already cut the list to keep memory bounded; ``stopped``
    says the check stopped at MAX_RAW_ERRORS, so there may be more than ``total``. ``profile`` is the profile
    that checked it ("" when none could), ``warnings`` (same shape, never failing the check) what the target
    would drop, ``notes`` (same shape) what limits the check itself, and ``unverified`` a profile with no pinned
    schema: its result is never valid, and its status is "Unverified" only when its documented rules found no
    error ("Invalid" when they did)."""
    total = len(diagnostics) if total is None else total
    diagnostics.sort(key=_sort_key)
    more = max(1 if stopped else 0, total - MAX_DIAGNOSTICS)  # stopped: the error it stopped at is one more
    shown = diagnostics[:MAX_DIAGNOSTICS]
    for d in shown:
        d.pop("_parts", None)
    if more or stopped:
        count = f"at least {more}" if stopped else str(more)
        shown.append({"path": "$", "rule": "more", "value": "", "message": f"{count} more problems not shown",
                      "fix": "Fix the problems above and check again."})
    warned, noted = sorted(warnings or [], key=_sort_key), sorted(notes or [], key=_sort_key)
    for d in warned + noted:
        d.pop("_parts", None)
    valid = checked and total == 0 and not stopped and not unverified
    errors = bool(total or stopped)
    status = ("Invalid" if errors and (checked or profile) else "Unverified" if unverified else "Valid" if valid
              else "Invalid" if checked else "Not checked")
    return {"valid": valid, "effect": EFFECT, "version": version,
            "checked": checked, "schema": schema_info(version) if checked and version in SCHEMAS else None,
            "diagnostics": shown, "more": more, "stopped": stopped, "profile": profile, "status": status,
            "warnings": warned, "notes": noted, "rules": list(rules or [])}


def validate(doc_or_text: Any, profile: str | None = None) -> dict[str, Any]:
    """Check an Ossie document (a dict, or YAML or JSON text) by ``profile``'s rules, or, when ``profile`` is
    None, by the profile its version and layout name (``ossie_profiles.detect_profile``); a document that names
    no profile is one ``version`` (or ``profile``) diagnostic and never a pass.

    Returns ``valid`` (true only when a pinned schema checked it and found nothing), ``effect`` ("informs": the
    result never blocks an import), ``version``, ``checked``
    (whether a schema check ran), ``schema`` (version, commit, url), ``diagnostics`` (``path``, ``rule``,
    ``value``, ``message``, ``fix``, sorted by path, at most MAX_DIAGNOSTICS then one ``more`` line), ``more``
    and ``stopped`` (the check stopped at MAX_RAW_ERRORS, so ``more`` is "at least"). Text that can't be read (too large, not YAML, aliases, too deep) is one ``parse`` diagnostic.
    Also ``profile``, ``status`` ("Valid", "Invalid", "Unverified" or "Not checked"), ``warnings`` (what the
    target drops; never failing) and ``rules`` (the ids of the profile rules that ran, ``ossie_profiles.RULES``)."""
    from . import ossie_profiles

    if profile is not None and profile not in ossie_profiles.PROFILES:
        raise ValueError(f"There's no Ossie profile {profile!r}; the profiles are {', '.join(ossie_profiles.PROFILES)}")
    doc = doc_or_text
    if isinstance(doc_or_text, (str, bytes)):
        try:
            doc = model_ossie.load_text(doc_or_text if isinstance(doc_or_text, str) else doc_or_text.decode("utf-8"))
        except (ValueError, TypeError) as exc:  # narrow: load_text's plain refusals, shown as the diagnostic
            return _result("", [_diag((), "parse", "", str(exc), "Open the file and fix what the message names.")],
                           False)
    if not isinstance(doc, dict):
        return _result("", [_diag((), "type", short(doc), "should be an object with version, name and datasets",
                                  "Write one semantic model: version, name and datasets at the top.")], False)
    shape = _shape(doc)
    raw_version = doc.get("version")
    version = raw_version if isinstance(raw_version, str) else ""
    if any(d["rule"] == "depth" for d in shape):
        return _result(version, shape, False)
    if profile is None:
        if "version" not in doc:
            return _result("", [_diag(("version",), "required", "(missing)", "is required",
                                      f"Add version: {model_ossie.OSSIE_VERSION}, the version this app reads and "
                                      "writes.")], False)
        try:
            profile = ossie_profiles.detect_profile(doc)
        except ossie_profiles.UnknownProfile as exc:  # narrow: the named refusal, shown as the diagnostic
            message = f"no pinned schema for {short(raw_version)}: {exc}" if exc.rule == "version" else str(exc)
            return _result(version, [_diag(exc.parts, exc.rule, short(doc.get(exc.parts[0]) if exc.parts else doc),
                                           message, f"Write the file as Ossie {model_ossie.OSSIE_VERSION}, the "
                                           "version this app converts, or as one of the versions named.")], False)
    if shape:
        return _result(version, shape, False, profile=profile)
    return ossie_profiles.check(doc, profile)


def validate_files(files: Any, profile: str = "fabric-semantic-view", tmdl: Any = None) -> dict[str, Any]:
    """A set of files checked by ``profile``'s file-set rules and each file by its document rules
    (``ossie_profiles.validate_files``)."""
    from . import ossie_profiles

    return ossie_profiles.validate_files(files, profile, tmdl)


def check(doc: Any, validator: Any, version: str) -> dict[str, Any]:
    """Run ``validator`` on ``doc`` and build the result; an unresolvable ``$ref`` is a diagnostic, not a fetch."""
    diagnostics, total, stopped = schema_errors(doc, validator, version)
    return _result(version, diagnostics, True, total, stopped)


def schema_errors(doc: Any, validator: Any, version: str) -> tuple[list[dict[str, Any]], int, bool]:
    """``validator``'s errors on ``doc`` as diagnostics (still holding their ``_parts``, for ``_result``), how
    many there were, and whether the check stopped at MAX_RAW_ERRORS."""
    from referencing.exceptions import Unresolvable

    ext_key = SCHEMAS.get(version, {}).get("extensions", "the extensions property")
    diagnostics: list[dict[str, Any]] = []
    total = raw = 0
    stopped = False
    seen: set[tuple[str, str]] = set()
    try:
        for error in validator.iter_errors(doc):
            raw += 1
            if raw > MAX_RAW_ERRORS:
                stopped = True
                break
            # jsonschema raises one required error per missing property; each becomes every missing one, so once
            found = [d for d in _flatten(error, ext_key) if d["rule"] not in _ONCE or (d["path"], d["rule"]) not in seen]
            seen.update((d["path"], d["rule"]) for d in found if d["rule"] in _ONCE)
            total += len(found)
            diagnostics.extend(found)
            if len(diagnostics) > 4 * MAX_DIAGNOSTICS:  # memory stays bounded: only the first by path are shown
                diagnostics.sort(key=_sort_key)
                del diagnostics[MAX_DIAGNOSTICS:]
    except Unresolvable as exc:  # narrow: a $ref outside the vendored schema; the registry can't fetch it
        ref = short(str(getattr(exc, "ref", "")))
        diagnostics.append(_diag((), "$ref", ref, f"refers to {ref}, which isn't a vendored schema",
                                 "Nothing to change in the file; the schema refers outside what is vendored."))
        total += 1
    except RecursionError:
        diagnostics.append(_diag((), "depth", "", "nested too deeply to check", "Take out the extra nesting."))
        total += 1
    return diagnostics, total, stopped
