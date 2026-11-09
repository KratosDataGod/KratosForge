"""Every setting the policy file takes, described once: its kind, its allowed values and a sentence for IT.

``app_settings.POLICY_KEYS`` is the list of settings the app reads from policy; this module says, for
each, what an administrator sees in a template. The kind comes from app_settings' own TYPES, CHOICES
and ranges wherever it has them, so a type changed there changes here. The tests fail when a policy
key has no entry here, or an entry names a key the app doesn't read (tests/test_policy_schema.py).

Kinds:
    bool   true or false
    enum   one of ``choices``
    int    a whole number from ``low`` to ``high``
    text   a string
    list   a list of strings
    json   a structured value (an object, or a list of objects); only the policy file can hold it
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from . import app_settings as s


@dataclass(frozen=True)
class Setting:
    key: str
    kind: str
    category: str
    text: str
    choices: tuple[str, ...] = ()
    low: int = 0
    high: int = 0
    user: bool = False  # the person can also set it in Settings; policy then fixes it


CATEGORIES = {
    "signin": "Sign-in and updates",
    "fabric": "Fabric, data and privacy",
    "notebooks": "Notebooks",
    "packages": "Packages",
    "sql": "SQL and Warehouse",
    "integrations": "AI tools and editors",
    "appstudio": "App Studio",
    "appearance": "Appearance and guidance",
}

# key -> (category, the sentence IT reads). Kinds and limits come from app_settings below.
_TEXT: dict[str, tuple[str, str]] = {
    "client_id": ("signin", "The Microsoft Entra app (client) ID the app signs in with, for an organization that uses its own app registration."),
    "channel": ("signin", "Which release the app updates to: stable or preview."),
    "auto_update": ("signin", "False stops the app updating itself; IT then rolls out new builds."),
    "allowed_tenants": ("signin", "Microsoft Entra tenant IDs people may sign in to. Any other account is signed out again."),
    "support_url": ("signin", "Where Report a problem goes: an https link or a mailto: address."),
    "engine_download_url": ("signin", "An https folder the app downloads its engine from instead of GitHub, with auto_update false. Read by the app itself; trusted only when the policy file is admin-only."),
    "register_link_scheme": ("signin", "False stops the app registering kratosforge:// links for the Windows user, and removes a registration it made."),
    "fabric_view_sign_in_hosts": ("signin", "Exact host names of the organization's own identity provider (AD FS, Okta, Ping) that the Fabric view may open during sign-in."),
    "show_preview": ("signin", "Whether features still in preview are shown."),
    "telemetry": ("fabric", "Whether daily usage counts are sent. Off unless turned on."),
    "telemetry_connection_string": ("fabric", "An Application Insights connection string, so usage data goes to the organization's own Azure Monitor."),
    "allow_fabric_runs": ("fabric", "False stops the app starting jobs on Fabric capacity; work stays on the PC."),
    "allow_staging_cleanup": ("fabric", "False stops the Estate X-ray deleting leftover Dataflow Gen2 staging lakehouses; it still lists them."),
    "allow_optimize_lakehouse": ("fabric", "False stops Optimize creating, connecting to or writing to a shared optimization lakehouse."),
    "allow_replay_samples": ("fabric", "False removes the sample of input data from replay bundles, so a bundle never holds lakehouse data."),
    "allow_signed_out_schedules": ("fabric", "False stops people setting schedules on this PC to run while they're signed out of Windows."),
    "platform_presence": ("fabric", "True records owners' last-used dates in the platform catalog, which everyone who reads the catalog sees."),
    "platform": ("fabric", "The organization's Platform workspaces, so every PC joins the same platform."),
    "incident_sla": ("fabric", "Minutes to acknowledge and resolve an incident, per priority."),
    "local_retention_days": ("fabric", "Days unused before local copies of lakehouse data are deleted; 0 keeps them."),
    "onelake_writes": ("fabric", "local_first stages a notebook's table writes on the PC and uploads them in the background; direct writes each one to OneLake before the next line runs."),
    "onelake_compression": ("fabric", "The Parquet codec for tables written to OneLake: zstd or snappy."),
    "onelake_skip_unchanged": ("fabric", "Whether an overwrite whose rows match what the app last wrote uploads nothing."),
    "kernel_process": ("notebooks", "Whether each notebook's cells run in a Python process of their own."),
    "cell_time_limit_minutes": ("notebooks", "Minutes a cell may run before it's interrupted; 0 for no limit."),
    "kernel_memory_limit_gb": ("notebooks", "Gigabytes a notebook's process may use before it's ended; 0 for no limit."),
    "kernel_max_sessions": ("notebooks", "How many notebooks keep a process of their own."),
    "kernel_idle_minutes": ("notebooks", "Idle minutes before a notebook's process is stopped; 0 keeps it."),
    "allow_notebook_packages": ("packages", "False stops people installing packages into notebook environments."),
    "notebook_package_folder": ("packages", "A folder of approved .whl files that notebook environments install from first."),
    "allow_pypi_packages": ("packages", "False stops notebook environments reaching PyPI for a package the folder doesn't have."),
    "allow_dbconnect_install": ("packages", "False stops the app installing the Databricks Connect client."),
    "dbconnect_index_url": ("packages", "The package index the Databricks Connect client is fetched from, for networks that mirror PyPI."),
    "sql_page_rows": ("sql", "Rows on each page of a SQL result."),
    "sql_time_limit_minutes": ("sql", "Minutes a SQL query may run before it's stopped; 0 for no limit."),
    "sql_memory_limit_percent": ("sql", "Share of the PC's memory one SQL query may use."),
    "sql_max_concurrent": ("sql", "SQL queries that run at once; more wait in a queue."),
    "sql_temp_dir": ("sql", "Folder for spilled SQL data and result snapshots."),
    "sql_result_retention_hours": ("sql", "Hours a finished query's result is kept; 0 keeps it only until the app closes."),
    "sql_warehouse_writes": ("sql", "False refuses every change to a warehouse, so the Warehouse page only reads."),
    "wh_monitoring_views": ("sql", "False stops the app creating monitoring views in Fabric."),
    "wh_monitoring_auto": ("sql", "Workspace IDs whose warehouses get monitoring views without asking again."),
    "wh_open_in": ("sql", "What opening a warehouse by its name does: the Warehouse page (kratosforge) or Fabric's editor (fabric)."),
    "integrations": ("integrations", "False turns external AI tools off on this PC."),
    "integrations_max_permissions": ("integrations", "The permission categories an external tool may be given."),
    "integrations_http": ("integrations", "False stops the local HTTP API being switched on."),
    "integrations_editors": ("integrations", "False stops editors such as VS Code being paired to run notebooks."),
    "integrations_extensions": ("integrations", "The extensions the person may turn on, by ID."),
    "receipt_redactions": ("integrations", "Parts of a receipt every export leaves out."),
    "appstudio_models": ("appstudio", "False stops App Studio using, adding or testing a model connection."),
    "appstudio_providers": ("appstudio", "The model providers a connection may use, by ID."),
    "appstudio_hosts": ("appstudio", "Exact host names an Azure OpenAI or OpenAI-compatible connection may use."),
    "appstudio_sample_rows": ("appstudio", "False stops any connection sending sample rows."),
    "fabric_full_screen": ("appearance", "Whether the Fabric portal tab fills the window."),
    "forge_animations": ("appearance", "The first motion switch: false means motion level 0 unless motion_level is set."),
    "motion_level": ("appearance", "Visual intensity: 0 Off, 1 Minimal, 2 Refined, 3 Expressive."),
    "motion_wolf": ("appearance", "Whether the wolf effects show at motion levels 2 and 3."),
    "motion_sparks": ("appearance", "Whether spark effects show at motion levels 2 and 3."),
    "motion_achievements": ("appearance", "Whether achievement celebrations show at motion levels 2 and 3."),
    "motion_ambient": ("appearance", "Whether ambient effects show at motion levels 2 and 3."),
    "motion_follow_system": ("appearance", "Whether Windows' reduced-motion setting turns decorative motion off."),
    "ui_explanations": ("appearance", "Whether each page's explanation shows beside it or opens on request."),
    "ui_preview_marks": ("appearance", "How Preview features are marked: tags or dots."),
    "ui_density": ("appearance", "Spacing in tables, lists and toolbars: comfortable or compact."),
    "ui_skin": ("appearance", "The platform skin's ID."),
    "ui_color_mode": ("appearance", "skin follows the skin's own color mode; light or dark overrides it."),
    "learn_mode": ("appearance", "How much learning guidance shows beside work: expert, professional or guided."),
    "learn_mode_sql": ("appearance", "The guidance level for SQL findings only; same follows learn_mode."),
    "learn_mode_notebooks": ("appearance", "The guidance level for notebook findings only; same follows learn_mode."),
}

# Keys app_settings has no type for (read by integrations/policy.py, appstudio/policy.py and the shell).
_KINDS = {"integrations": "bool", "integrations_http": "bool", "integrations_editors": "bool",
          "integrations_max_permissions": "list", "integrations_extensions": "list", "receipt_redactions": "list",
          "appstudio_models": "bool", "appstudio_sample_rows": "bool", "appstudio_providers": "list",
          "appstudio_hosts": "list", "register_link_scheme": "bool", "fabric_view_sign_in_hosts": "list",
          "engine_download_url": "text"}
# Keys whose list holds objects, not strings.
_JSON = {"platform", "incident_sla"}
# Upper limits for numbers app_settings only bounds below (a template needs one).
_HIGH = {"local_retention_days": 3650}


def _kind(key: str) -> tuple[str, dict[str, Any]]:
    if key in _JSON:
        return "json", {}
    if key in s.CHOICES:
        return "enum", {"choices": s.CHOICES[key]}
    if key == "channel":
        return "enum", {"choices": s.CHANNELS}
    t = s.TYPES.get(key)
    if t is None:
        return _KINDS[key], {}
    if t is bool:
        return "bool", {}
    if t is list:
        return "list", {}
    if t is str:
        return "text", {}
    if key in s.SQL_RANGES:
        low, high = s.SQL_RANGES[key]
        return "int", {"low": low, "high": high}
    if key == "motion_level":
        return "int", {"low": s.MOTION_LEVELS[0], "high": s.MOTION_LEVELS[1]}
    return "int", {"low": 0, "high": int(s.LIMITS.get(key, _HIGH.get(key, 0)))}


def settings() -> list[Setting]:
    """One Setting per policy key, in app_settings' order. KeyError for a key with no entry."""
    out = []
    for key in s.POLICY_KEYS:
        category, text = _TEXT[key]
        kind, extra = _kind(key)
        out.append(Setting(key, kind, category, text, user=key in s.USER_KEYS, **extra))
    return out


# ------------------------------------------------------------------ Group Policy templates (ADMX/ADML)

REGISTRY_KEY = r"Software\Policies\KratosBI\KratosForge"
NAMESPACE = "KratosBI.Policies.KratosForge"
REVISION = "1.0"


def _x(text: str) -> str:
    from xml.sax.saxutils import escape
    return escape(text, {'"': "&quot;"})


def _cat(category: str) -> str:
    return f"cat_{category}"


def admx() -> str:
    """The ADMX template: one Machine policy per setting a template can hold (every kind but json),
    writing ``HKLM\\Software\\Policies\\KratosBI\\KratosForge``. A list is a subkey named after the
    setting, one string value per item."""
    out = ['<?xml version="1.0" encoding="utf-8"?>',
           '<!-- Generated by fabric_desktop.policy_schema: python -m fabric_desktop.policy_schema. Don\'t edit by hand. -->',
           f'<policyDefinitions xmlns:xsd="http://www.w3.org/2001/XMLSchema" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" '
           f'revision="{REVISION}" schemaVersion="1.0" xmlns="http://schemas.microsoft.com/GroupPolicy/2006/07/PolicyDefinitions">',
           "  <policyNamespaces>",
           f'    <target prefix="kratosforge" namespace="{NAMESPACE}" />',
           "  </policyNamespaces>",
           f'  <resources minRequiredRevision="{REVISION}" />',
           "  <supportedOn>", "    <definitions>",
           '      <definition name="SUPPORTED_KratosForge" displayName="$(string.SUPPORTED_KratosForge)" />',
           "    </definitions>", "  </supportedOn>",
           "  <categories>",
           '    <category name="KratosForge" displayName="$(string.KratosForge)" />']
    for name in CATEGORIES:
        out += [f'    <category name="{_cat(name)}" displayName="$(string.{_cat(name)})">',
                '      <parentCategory ref="KratosForge" />', "    </category>"]
    out += ["  </categories>", "  <policies>"]
    for st in settings():
        if st.kind == "json":
            continue
        head = (f'    <policy name="{st.key}" class="Machine" displayName="$(string.{st.key})" '
                f'explainText="$(string.{st.key}_Help)" key="{REGISTRY_KEY}"')
        body = [f'      <parentCategory ref="{_cat(st.category)}" />',
                '      <supportedOn ref="SUPPORTED_KratosForge" />']
        if st.kind == "bool":
            out.append(head + f' valueName="{st.key}">')
            out += body + ['      <enabledValue><decimal value="1" /></enabledValue>',
                           '      <disabledValue><decimal value="0" /></disabledValue>']
        else:
            out.append(head + f' presentation="$(presentation.{st.key})">')
            out += body + ["      <elements>"]
            if st.kind == "enum":
                out.append(f'        <enum id="{st.key}" valueName="{st.key}" required="true">')
                for c in st.choices:
                    out.append(f'          <item displayName="$(string.{st.key}_{c})"><value><string>{_x(c)}</string></value></item>')
                out.append("        </enum>")
            elif st.kind == "int":
                out.append(f'        <decimal id="{st.key}" valueName="{st.key}" required="true" '
                           f'minValue="{st.low}" maxValue="{st.high}" />')
            elif st.kind == "text":
                out.append(f'        <text id="{st.key}" valueName="{st.key}" required="true" maxLength="2048" />')
            elif st.kind == "list":
                out.append(f'        <list id="{st.key}" key="{REGISTRY_KEY}\\{st.key}" />')
            out.append("      </elements>")
        out.append("    </policy>")
    out += ["  </policies>", "</policyDefinitions>", ""]
    return "\n".join(out)


# No build reads registry policy yet, so every policy says so until a release adds that support.
_NOT_READ_YET = (" No current KratosForge build reads this policy from the registry. Until the release notes say"
                 " registry policy is supported, set it in %ProgramData%\\FabricDesktop\\policy.json instead.")


def adml() -> str:
    """The en-US ADML: each policy's name and help, and the box it shows."""
    strings = [("KratosForge", "KratosForge"), ("SUPPORTED_KratosForge", "Not read by any current KratosForge build")]
    strings += [(_cat(k), v) for k, v in CATEGORIES.items()]
    presentations = []
    for st in settings():
        if st.kind == "json":
            continue
        help_text = st.text + (" People can also change it in Settings; this policy fixes it." if st.user else "")
        if st.kind == "list":
            help_text += " Add one item per line."
        help_text += f" Policy file key: {st.key}."
        help_text += _NOT_READ_YET
        strings += [(st.key, st.key.replace("_", " ").capitalize()), (f"{st.key}_Help", help_text)]
        strings += [(f"{st.key}_{c}", c) for c in st.choices]
        if st.kind == "enum":
            presentations.append(f'      <presentation id="{st.key}"><dropdownList refId="{st.key}" noSort="true">'
                                 f"{_x(st.key)}</dropdownList></presentation>")
        elif st.kind == "int":
            presentations.append(f'      <presentation id="{st.key}"><decimalTextBox refId="{st.key}" '
                                 f'defaultValue="{st.low}">{_x(st.key)}</decimalTextBox></presentation>')
        elif st.kind == "text":
            presentations.append(f'      <presentation id="{st.key}"><textBox refId="{st.key}"><label>{_x(st.key)}</label>'
                                 "</textBox></presentation>")
        elif st.kind == "list":
            presentations.append(f'      <presentation id="{st.key}"><listBox refId="{st.key}">{_x(st.key)}</listBox></presentation>')
    out = ['<?xml version="1.0" encoding="utf-8"?>',
           '<!-- Generated by fabric_desktop.policy_schema: python -m fabric_desktop.policy_schema. Don\'t edit by hand. -->',
           f'<policyDefinitionResources xmlns:xsd="http://www.w3.org/2001/XMLSchema" '
           f'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" revision="{REVISION}" schemaVersion="1.0" '
           'xmlns="http://schemas.microsoft.com/GroupPolicy/2006/07/PolicyDefinitions">',
           "  <displayName>KratosForge</displayName>",
           "  <description>KratosForge policy settings</description>",
           "  <resources>", "    <stringTable>"]
    out += [f'      <string id="{i}">{_x(t)}</string>' for i, t in strings]
    out += ["    </stringTable>", "    <presentationTable>", *presentations, "    </presentationTable>",
            "  </resources>", "</policyDefinitionResources>", ""]
    return "\n".join(out)


TEMPLATE_DIR = ("deploy", "policy")


def write_templates(repo: Any) -> None:
    """Writes deploy/policy/KratosForge.admx and en-US/KratosForge.adml under ``repo``."""
    from pathlib import Path
    folder = Path(repo).joinpath(*TEMPLATE_DIR)
    (folder / "en-US").mkdir(parents=True, exist_ok=True)
    (folder / "KratosForge.admx").write_bytes(admx().encode("utf-8"))
    (folder / "en-US" / "KratosForge.adml").write_bytes(adml().encode("utf-8"))


if __name__ == "__main__":
    import sys
    from pathlib import Path
    write_templates(sys.argv[1] if len(sys.argv) > 1 else Path(__file__).resolve().parents[2])
