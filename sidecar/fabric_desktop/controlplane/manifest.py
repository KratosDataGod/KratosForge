"""The KratosForge Environment manifest (CP-001; spec in docs/product/central-control-plane-plan.md, section 6).

A manifest says which resources make up one environment, by id: its provider, namespace, analytical
store, optional operational store, deployment bindings, the schema versions last written, and which
services use it. It lives inside the environment (``Files/_kratosforge/environment.json`` in Fabric),
so a fresh installation that can read the store can reconnect without anything from another PC.

Rules this module enforces:

- ``environment_id`` is ``kfenv_`` and 32 lowercase hex characters, random at creation.
- Every resource reference is an id (a GUID in Fabric). Display names are labels only.
- No secrets: a key named like a credential (see ``_SECRET_WORDS``), or a value that ``redact.secrets``
  would change, is refused.
- The owner is an Entra object id only: no email or sign-in name.
- Keys this version doesn't know are kept when the manifest is rewritten, and a manifest written by a
  newer ``manifest_version`` is opened read-only (``writable`` is False).

Pure: no network and no files.
"""

from __future__ import annotations

import copy
import datetime as dt
import json
import re
import uuid
from typing import Any

from .. import __version__
from ..redact import secrets

MANIFEST_VERSION = 1
PROVIDERS = ("fabric", "databricks", "snowflake", "local")
ANALYTICAL_KINDS = {"fabric": "fabric_lakehouse", "local": "local_folder"}
OPERATIONAL_KINDS = {"fabric": "fabric_sql_udf", "local": "local_sqlite"}
DEPLOYMENTS = ("dev", "test", "prod", "keys")
MIGRATION_STATES = ("idle", "running", "failed")
MAX_NAME = 120
MAX_TEXT = 400

_ENV_ID = re.compile(r"^kfenv_[0-9a-f]{32}$")
_GUID = re.compile(r"^[0-9a-fA-F]{8}-(?:[0-9a-fA-F]{4}-){3}[0-9a-fA-F]{12}$")
_PREFIX = re.compile(r"^[a-z][a-z0-9]{1,9}_$")
_SERVICE = re.compile(r"^[a-z][a-z0-9_]{0,40}$")
_VERSION = re.compile(r"^\d+(?:\.\d+){0,3}$")
_STAMP = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})$")
# Keys that hold credentials somewhere else, matched on whole words of the key name (split at "_", "-"
# and camelCase), singular or plural, and on the words run together ("connectionstring"). A key whose
# last word is "key" (storage_key, primaryKey, api_key) is refused too; "keys" (the shared keys
# workspace) and words that merely end in "key" (monkey) are not.
_SECRET_WORDS = {"token", "secret", "password", "passwd", "pwd", "credential", "sas", "apikey",
                 "connectionstring", "accountkey", "privatekey", "clientsecret"}
MAX_BYTES = 64 * 1024  # a manifest is a few kilobytes; anything far larger isn't one
# Path-like values the manifest may hold, under Files/_kratosforge/ only.
_CONFIG_REF = re.compile(r"^Files/_kratosforge/[A-Za-z0-9_./-]{1,200}$")


class ManifestError(ValueError):
    """A manifest that breaks a rule. The message says which field and why."""


def now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def new_environment_id() -> str:
    return "kfenv_" + uuid.uuid4().hex


def _words(key: str) -> list[str]:
    spaced = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", key)
    return [p.lower() for p in re.split(r"[^A-Za-z0-9]+", spaced) if p]


def _secret_key(key: str) -> bool:
    if key.endswith("_id"):
        return False  # resource ids: workspace_id, functions_id
    words = _words(key)
    singular = {w[:-1] if w.endswith("s") and len(w) > 3 else w for w in words}
    return bool((singular | set(words) | {"".join(words)}) & _SECRET_WORDS) or (bool(words) and words[-1] == "key")


def _unicode(text: str, path: str) -> None:
    """Text that can be written back as UTF-8: JSON may carry a lone surrogate (\\ud800) that can't."""
    try:
        text.encode("utf-8")
    except UnicodeEncodeError:
        raise ManifestError(f"{path}: text must be valid Unicode") from None


def _check_no_secrets(value: Any, path: str) -> None:
    if isinstance(value, dict):
        for k, v in value.items():
            if not isinstance(k, str):
                raise ManifestError(f"{path}: keys must be text")
            _unicode(k, path)
            if _secret_key(k):
                raise ManifestError(f"{path}.{k}: a manifest never holds credentials; keep them in the "
                                    "provider's own sign-in or a secret store")
            _check_no_secrets(v, f"{path}.{k}")
    elif isinstance(value, list):
        for i, v in enumerate(value):
            _check_no_secrets(v, f"{path}[{i}]")
    elif isinstance(value, str):
        _unicode(value, path)
        if secrets(value) != value:
            raise ManifestError(f"{path}: looks like a credential, which a manifest never holds")


def _text(value: Any, path: str, required: bool = True, limit: int = MAX_TEXT) -> str:
    if value is None or value == "":
        if required:
            raise ManifestError(f"{path} is required")
        return ""
    if not isinstance(value, str) or len(value) > limit or any(ord(c) < 32 for c in value):
        raise ManifestError(f"{path} must be one line of text, at most {limit} characters")
    return value


def _guid(value: Any, path: str, required: bool = True) -> str:
    if (value is None or value == "") and not required:
        return ""
    if not isinstance(value, str) or not _GUID.fullmatch(value):
        raise ManifestError(f"{path} must be an id (a GUID), never a display name")
    return value.lower()


def _ref(provider: str, value: Any, path: str, required: bool = True) -> None:
    """A resource id. Fabric uses GUIDs; a local environment uses folder-safe ids."""
    if provider == "fabric":
        _guid(value, path, required)
    elif provider == "local":
        if (value is None or value == "") and not required:
            return
        if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}", value):
            raise ManifestError(f"{path} must be a local id: letters, digits, '_', '.', '-'")
    else:
        # A provider this app can't connect to yet: its manifest is read, never written (CP-ADR-007).
        _text(value, path, required)


def _stamp(value: Any, path: str) -> None:
    if not isinstance(value, str) or not _STAMP.fullmatch(value):
        raise ManifestError(f"{path} must be an ISO 8601 UTC time")


def _store(provider: str, store: Any, path: str, kinds: dict[str, str], ids: tuple[str, ...]) -> None:
    if not isinstance(store, dict):
        raise ManifestError(f"{path} must be an object")
    want = kinds.get(provider)
    if want and store.get("kind") != want:
        raise ManifestError(f"{path}.kind must be {want!r} for a {provider} environment")
    _text(store.get("kind"), f"{path}.kind")
    for name in ids:
        _ref(provider, store.get(name), f"{path}.{name}")
    _text(store.get("display_name"), f"{path}.display_name", required=False, limit=MAX_NAME)


def validate(manifest: Any) -> dict[str, Any]:
    """The manifest, checked. Raises ManifestError naming the first field that breaks a rule.

    A manifest with a newer ``manifest_version`` passes when its common fields are sound, so it can
    be shown; ``writable`` says whether this app may rewrite it."""
    try:
        return _validate(manifest)
    except RecursionError:
        raise ManifestError("The manifest is nested too deeply to be one KratosForge wrote") from None


def _validate(manifest: Any) -> dict[str, Any]:
    if not isinstance(manifest, dict):
        raise ManifestError("A manifest is a JSON object")
    version = manifest.get("manifest_version")
    if not isinstance(version, int) or isinstance(version, bool) or version < 1:
        raise ManifestError("manifest_version must be a whole number, 1 or more")
    _check_no_secrets(manifest, "manifest")
    env_id = manifest.get("environment_id")
    if not isinstance(env_id, str) or not _ENV_ID.fullmatch(env_id):
        raise ManifestError("environment_id must be 'kfenv_' and 32 lowercase hex characters")
    _text(manifest.get("name"), "name", limit=MAX_NAME)
    provider = manifest.get("provider")
    if provider not in PROVIDERS:
        raise ManifestError(f"provider must be one of {', '.join(PROVIDERS)}")
    if version > MANIFEST_VERSION:
        return manifest  # read-only: the rest may have changed shape in a later version

    ns = manifest.get("namespace")
    if not isinstance(ns, dict):
        raise ManifestError("namespace must be an object")
    _ref(provider, ns.get("workspace_id"), "namespace.workspace_id")
    _text(ns.get("display_name"), "namespace.display_name", required=False, limit=MAX_NAME)

    _store(provider, manifest.get("analytical_store"), "analytical_store", ANALYTICAL_KINDS,
           ("workspace_id", "lakehouse_id"))
    if not isinstance(manifest["analytical_store"].get("schemas", True), bool):
        raise ManifestError("analytical_store.schemas must be true or false")
    op = manifest.get("operational_store")
    if op is not None:  # a lakehouse-only environment has none (CP-ADR-006)
        _store(provider, op, "operational_store", OPERATIONAL_KINDS, ("workspace_id",))
        if provider == "fabric":
            _guid(op.get("functions_id"), "operational_store.functions_id")
            _guid(op.get("database_id"), "operational_store.database_id", required=False)

    deployments = manifest.get("deployments", {})
    if not isinstance(deployments, dict):
        raise ManifestError("deployments must be an object")
    for name, binding in deployments.items():
        if name not in DEPLOYMENTS:
            raise ManifestError(f"deployments.{name}: must be one of {', '.join(DEPLOYMENTS)}")
        if not isinstance(binding, dict) or not binding:
            raise ManifestError(f"deployments.{name} must be an object of ids")
        for k, v in binding.items():
            if not k.endswith("_id"):
                raise ManifestError(f"deployments.{name}.{k}: only ids (names ending in _id) are kept here")
            _ref(provider, v, f"deployments.{name}.{k}")

    schema = manifest.get("schema", {})
    if not isinstance(schema, dict):
        raise ManifestError("schema must be an object")
    op_version = schema.get("operational")
    if op_version is not None and (not isinstance(op_version, int) or isinstance(op_version, bool) or op_version < 1):
        raise ManifestError("schema.operational must be a whole number, 1 or more")
    families = schema.get("families", {})
    if not isinstance(families, dict):
        raise ManifestError("schema.families must be an object")
    for prefix, v in families.items():
        if not _PREFIX.fullmatch(prefix):
            raise ManifestError(f"schema.families.{prefix}: a family prefix looks like 'fdopt_'")
        if not isinstance(v, int) or isinstance(v, bool) or v < 1:
            raise ManifestError(f"schema.families.{prefix} must be a whole number, 1 or more")

    compat = manifest.get("compatibility", {})
    if not isinstance(compat, dict):
        raise ManifestError("compatibility must be an object")
    for k in ("min_app_version", "written_by_app_version"):
        if k in compat and not (isinstance(compat[k], str) and _VERSION.fullmatch(compat[k])):
            raise ManifestError(f"compatibility.{k} must be a version like 0.20.0")

    owner = manifest.get("owner")
    if owner is not None:
        if not isinstance(owner, dict) or set(owner) - {"object_id"}:
            raise ManifestError("owner holds an Entra object id only (owner.object_id): no email or name")
        _guid(owner.get("object_id"), "owner.object_id")

    for k in ("created_at", "updated_at"):
        _stamp(manifest.get(k), k)

    for k in ("services", "capabilities"):
        values = manifest.get(k, [])
        if not isinstance(values, list) or any(not isinstance(s, str) or not _SERVICE.fullmatch(s) for s in values):
            raise ManifestError(f"{k} must be a list of short lowercase names")
        if len(set(values)) != len(values):
            raise ManifestError(f"{k} lists a name twice")

    migration = manifest.get("migration", {"state": "idle"})
    if not isinstance(migration, dict) or migration.get("state") not in MIGRATION_STATES:
        raise ManifestError(f"migration.state must be one of {', '.join(MIGRATION_STATES)}")
    if migration["state"] == "running":
        _text(migration.get("by"), "migration.by", limit=64)
        _stamp(migration.get("expires_at"), "migration.expires_at")
    if migration["state"] == "failed":
        _text(migration.get("step"), "migration.step")

    refs = manifest.get("config_refs", {})
    if not isinstance(refs, dict):
        raise ManifestError("config_refs must be an object")
    for k, v in refs.items():
        if not isinstance(v, str) or not _CONFIG_REF.fullmatch(v) or ".." in v:
            raise ManifestError(f"config_refs.{k} must be a path under Files/_kratosforge/")
    return manifest


def check_request_fields(*, name: Any = None, workspace_name: Any = None, lakehouse_name: Any = None,
                         owner_object_id: Any = None) -> str:
    """Why a manifest would refuse these labels and owner, or "": the same rules ``validate`` applies,
    for a caller that checks a request before anything is created (provisioning's plan)."""
    try:
        for key, value in (("name", name), ("workspace_name", workspace_name), ("lakehouse_name", lakehouse_name)):
            _text(value, key, required=False, limit=MAX_NAME)
            _check_no_secrets(value, key)
        _guid(owner_object_id, "owner_object_id", required=False)
    except ManifestError as exc:
        return str(exc)
    return ""


def writable(manifest: dict[str, Any]) -> bool:
    """Whether this app may rewrite the manifest: not one a newer app wrote."""
    return int(manifest.get("manifest_version", 0)) <= MANIFEST_VERSION


def new_manifest(*, name: str, provider: str, namespace: dict[str, Any], analytical_store: dict[str, Any],
                 operational_store: dict[str, Any] | None = None, deployments: dict[str, Any] | None = None,
                 families: dict[str, int] | None = None, operational_version: int | None = None,
                 owner_object_id: str = "", services: list[str] | None = None,
                 capabilities: list[str] | None = None, at: str = "") -> dict[str, Any]:
    """A new, valid manifest with a fresh environment id."""
    stamp = at or now()
    manifest: dict[str, Any] = {
        "manifest_version": MANIFEST_VERSION,
        "environment_id": new_environment_id(),
        "name": name,
        "provider": provider,
        "namespace": dict(namespace),
        "analytical_store": {"kind": ANALYTICAL_KINDS.get(provider, ""), "schemas": True} | dict(analytical_store),
        "operational_store": ({"kind": OPERATIONAL_KINDS.get(provider, "")} | dict(operational_store))
                             if operational_store else None,
        "deployments": dict(deployments or {}),
        "schema": {"families": dict(families or {})} | (
            {"operational": operational_version} if operational_version else {}),
        "compatibility": {"min_app_version": __version__, "written_by_app_version": __version__},
        "created_at": stamp,
        "updated_at": stamp,
        "services": sorted(set(services or [])),
        "capabilities": sorted(set(capabilities or (["analytical_store"] + (["operational_store"] if operational_store else [])))),
        "migration": {"state": "idle"},
        "config_refs": {},
    }
    if owner_object_id:
        manifest["owner"] = {"object_id": owner_object_id}
    return validate(manifest)


def updated(manifest: dict[str, Any], changes: dict[str, Any], at: str = "") -> dict[str, Any]:
    """A copy with ``changes`` applied (top-level keys), keys this app doesn't know kept, and
    ``updated_at`` and ``written_by_app_version`` set. The id, provider and creation time can't change."""
    if not writable(manifest):
        raise ManifestError("A newer version of KratosForge wrote this environment's manifest. Update "
                            "KratosForge to change it; until then it's read-only.")
    fixed = {"environment_id", "provider", "created_at", "manifest_version"} & set(changes)
    if fixed:
        raise ManifestError(f"{', '.join(sorted(fixed))} can't change after an environment is created")
    out = copy.deepcopy(manifest) | copy.deepcopy(changes)
    out["updated_at"] = at or now()
    out["compatibility"] = dict(out.get("compatibility") or {}) | {"written_by_app_version": __version__}
    return validate(out)


def to_json(manifest: dict[str, Any]) -> bytes:
    """Stable bytes: sorted keys, so the same manifest always writes the same file."""
    return (json.dumps(validate(manifest), indent=2, sort_keys=True, ensure_ascii=False) + "\n").encode("utf-8")


def from_json(data: bytes | str) -> dict[str, Any]:
    if len(data) > MAX_BYTES:
        raise ManifestError(f"The manifest is larger than {MAX_BYTES // 1024} KB, so it isn't one KratosForge wrote")
    try:
        parsed = json.loads(data)
    except ValueError as exc:  # JSONDecodeError, UnicodeDecodeError, and an integer too long to convert
        raise ManifestError(f"The manifest isn't valid JSON: {exc.__class__.__name__}") from None
    except RecursionError:
        raise ManifestError("The manifest is nested too deeply to be one KratosForge wrote") from None
    return validate(parsed)


def summary(manifest: dict[str, Any]) -> dict[str, Any]:
    """What the app shows and caches about an environment: ids and labels, nothing else."""
    a = manifest.get("analytical_store") or {}
    o = manifest.get("operational_store") or {}
    return {"environment_id": manifest["environment_id"], "name": manifest.get("name", ""),
            "provider": manifest.get("provider", ""), "writable": writable(manifest),
            "workspace_id": (manifest.get("namespace") or {}).get("workspace_id", ""),
            "workspace_name": (manifest.get("namespace") or {}).get("display_name", ""),
            "lakehouse_id": a.get("lakehouse_id", ""), "lakehouse_name": a.get("display_name", ""),
            "has_operational_store": bool(o), "operational_name": o.get("display_name", ""),
            "families": dict((manifest.get("schema") or {}).get("families") or {}),
            "updated_at": manifest.get("updated_at", "")}


# ---- connection files (export configuration, CP-010)
CONNECTION_FORMAT = "kratosforge-environment-connection"
CONNECTION_VERSION = 1
_CONNECTION_KEYS = ("format", "version", "environment_id", "name", "provider", "workspace_id", "workspace_name",
                    "lakehouse_id", "lakehouse_name")


def connection(entry: dict[str, Any]) -> dict[str, Any]:
    """A connection file for one environment, from this PC's cached summary: where its manifest is and
    the labels to show, so another PC can connect. Ids and labels only: no owner, token, email or secret."""
    return {"format": CONNECTION_FORMAT, "version": CONNECTION_VERSION,
            **{k: entry.get(k, "") for k in _CONNECTION_KEYS[2:]}}


def check_connection(obj: Any) -> dict[str, Any]:
    """A connection file someone gives this PC, checked before anything reads it: the format this app
    writes, only its keys, and real ids. It says where to look; the manifest found there decides."""
    if not isinstance(obj, dict):
        raise ManifestError("This isn't a KratosForge Environment connection file.")
    if obj.get("format") != CONNECTION_FORMAT:
        raise ManifestError("This isn't a KratosForge Environment connection file.")
    if obj.get("version") != CONNECTION_VERSION or isinstance(obj.get("version"), bool):
        raise ManifestError("This connection file is from another version of KratosForge. Ask for a new one.")
    extra = sorted(str(k) for k in obj if k not in _CONNECTION_KEYS)
    if extra:
        raise ManifestError(f"This connection file has fields KratosForge doesn't write: {', '.join(extra)[:120]}")
    env_id = obj.get("environment_id")
    if not isinstance(env_id, str) or not _ENV_ID.fullmatch(env_id):
        raise ManifestError("environment_id must be 'kfenv_' and 32 lowercase hex characters")
    provider = obj.get("provider")
    if provider not in PROVIDERS:
        raise ManifestError(f"provider must be one of {', '.join(PROVIDERS)}")
    _ref(provider, obj.get("workspace_id"), "workspace_id")
    _ref(provider, obj.get("lakehouse_id"), "lakehouse_id")
    for k in ("name", "workspace_name", "lakehouse_name"):
        _text(obj.get(k, ""), k, required=False)
    out = {k: obj.get(k, "") for k in _CONNECTION_KEYS}
    if provider == "fabric":
        out["workspace_id"], out["lakehouse_id"] = out["workspace_id"].lower(), out["lakehouse_id"].lower()
    return out
