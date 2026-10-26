"""Entra ID security groups for a domain's roles: what to make, what to call it, and scripts to make it.

Each data mesh domain gives access through groups, never to people, so a joiner or leaver is one
membership change. For every role (core.ENTRA_ROLES) this says who goes in and what the group gets in
Fabric, names the group from the platform's naming pattern, or uses a group someone already has and
linked to the role. The scripts are for an Entra admin to read and run, or to build the groups by hand:

    powershell   Microsoft Graph PowerShell: creates the suggested groups that don't exist yet
    az           the same with the Azure CLI (bash)
    csv          every group, its description and owners, for building them by hand
    roles        PowerShell that gives the groups their Fabric roles (domain roles and workspace roles)

Nothing here calls Graph or Fabric: the app has no app registration and holds no secret, so the person
running a script signs in as themselves. Every value written into a script is either checked to be
letters, digits, -, _ and . (group names, mail nicknames, IDs, emails) or quoted for its shell.
"""

from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass
from typing import Any

from . import core

ENV_NAMES = {"dev": "Dev", "test": "Test", "prod": "Prod"}
_MAIL_BAD = re.compile(r"[^a-z0-9_.-]")
_EMAIL = re.compile(r"^[A-Za-z0-9._%+'-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$")
_SAFE = re.compile(r"^[A-Za-z0-9_.-]+$")
FABRIC = "https://api.fabric.microsoft.com/v1"


@dataclass(frozen=True)
class Role:
    key: str
    label: str
    envs: tuple[str, ...]  # the environments it is given in by default; () for a role that spans them
    who: str
    gets: str
    fabric: tuple[str, str] | None = None  # ("domain", "Admins"|"Contributors") or ("workspace", role)


ROLES = (
    Role("domain_admins", "Domain owners", (), "The people accountable for {domain} data",
         "Domain admin on the {domain} domain in Fabric", ("domain", "Admins")),
    Role("domain_contributors", "Domain contributors", (), "Workspace admins allowed to put workspaces in {domain}",
         "Domain contributor on the {domain} domain in Fabric", ("domain", "Contributors")),
    Role("workspace_admins", "Workspace admins", ("dev", "test", "prod"), "{domain} platform leads (keep Prod to a few named people)",
         "Admin on {domain} {env} workspaces", ("workspace", "Admin")),
    Role("creators", "Creators", ("dev", "test"), "{domain} data engineers and analysts who build",
         "Contributor on {domain} {env} workspaces", ("workspace", "Contributor")),
    Role("readers", "Data readers", ("prod",), "Other teams approved to read {domain} data products",
         "Read on {domain} {env} lakehouses through a OneLake data access role; no workspace role"),
    Role("viewers", "Report viewers", ("prod",), "Business users of {domain} reports",
         "Viewer on {domain} {env} workspaces, or the audience of its app", ("workspace", "Viewer")),
    Role("stewards", "Data stewards", (), "People who keep {domain}'s glossary terms and descriptions",
         "No Fabric role: {domain}'s glossary in KratosForge, and Purview data curator if you use it"),
)
BY_KEY = {r.key: r for r in ROLES}
assert set(BY_KEY) == set(core.ENTRA_ROLES)


def _case(text: str, case: str) -> str:
    return text.upper() if case == "upper" else text.lower() if case == "lower" else text


def _part(text: str) -> str:
    """A domain's id or name as part of a group name: letters, digits, -, _ and . only."""
    return re.sub(r"[^A-Za-z0-9_.-]+", "-", str(text or "")).strip("-_.")


def group_name(naming: dict[str, Any], role: str, domain: dict[str, Any], parent: dict[str, Any] | None,
               env: str = "") -> str:
    """The suggested group name for a role of a domain (a subdomain names its parent as {DOMAIN})."""
    key = "name" if naming.get("domain_part") == "name" else "id"
    top, sub = (parent, domain) if parent else (domain, None)
    values = {"PREFIX": naming.get("prefix") or "", "DOMAIN": _part(top.get(key) or top.get("id")),
              "SUBDOMAIN": _part(sub.get(key) or sub.get("id")) if sub else "",
              "ROLE": naming["words"].get(role) or role, "ENV": ENV_NAMES.get(env, "") if env else ""}
    name = re.sub(r"\{(PREFIX|DOMAIN|SUBDOMAIN|ROLE|ENV)\}", lambda m: values[m.group(1)], naming["pattern"])
    # A part left empty (no prefix, a top-level domain, a role that spans environments) takes its separator with it.
    name = re.sub(r"([-_.])[-_.]+", r"\1", name).strip("-_.")
    return _case(name, naming.get("case", "upper"))[:256]


def mail_nickname(name: str) -> str:
    """Entra's mail nickname: lower case, no spaces or @()\\[]";:<>, at most 64 characters."""
    return _MAIL_BAD.sub("", name.lower())[:64] or "group"


def owners_of(domain: dict[str, Any]) -> list[str]:
    """The domain's owners that are emails, so a script can make them the groups' owners."""
    return [x.strip() for x in re.split(r"[,;]", str(domain.get("owners") or "")) if _EMAIL.match(x.strip())]


def plan(domains: list[dict[str, Any]], domain_id: str, links: list[dict[str, Any]], naming: dict[str, Any],
         envs: list[str] | None = None, subdomains: bool = False) -> list[dict[str, Any]]:
    """One row per group a domain needs: role, environment, the suggested name, and the linked group if any.
    ``envs`` limits the environments (default all three); ``subdomains`` adds each subdomain's own groups."""
    known = {d["id"]: d for d in domains}
    domain = known.get(domain_id)
    if domain is None:
        raise ValueError(f"{domain_id} isn't a domain")
    wanted = [e for e in (envs or core.ENTRA_ENVS) if e in core.ENTRA_ENVS]
    targets = [domain] + ([d for d in domains if d.get("parent_id") == domain_id] if subdomains else [])
    linked = {(link["domain_id"], link["role"], link["env"] or ""): link for link in links}
    off = set(naming.get("roles_off") or [])
    rows = []
    for d in targets:
        parent = known.get(d.get("parent_id") or "")
        for role in ROLES:
            if role.key in off:
                continue
            for env in [e for e in role.envs if e in wanted] if role.envs else [""]:
                words = {"domain": d.get("name") or d["id"], "env": ENV_NAMES.get(env, "")}
                link = linked.get((d["id"], role.key, env))
                name = group_name(naming, role.key, d, parent, env)
                rows.append({
                    "domain_id": d["id"], "domain": d.get("name") or d["id"], "fabric_domain": d.get("fabric_domain") or "",
                    "role": role.key, "label": role.label + (f", {ENV_NAMES[env]}" if env else ""), "env": env,
                    "suggested": name, "mail_nickname": mail_nickname(name),
                    "description": f"KratosForge: {role.label} of the {words['domain']} domain" + (f" ({words['env']})" if env else ""),
                    "who": role.who.format(**words), "gets": role.gets.format(**words),
                    "fabric": list(role.fabric) if role.fabric else None,
                    "linked": {"group_id": link["group_id"], "group_name": link["group_name"]} if link else None,
                    "name": link["group_name"] if link else name,
                })
    return rows


# --------------------------------------------------------------------------- scripts

def _ps(text: str) -> str:
    """A PowerShell single-quoted string."""
    return "'" + str(text).replace("'", "''").replace("\r", " ").replace("\n", " ") + "'"


def _sh(text: str) -> str:
    """A bash single-quoted string."""
    return "'" + str(text).replace("'", "'\\''").replace("\r", " ").replace("\n", " ") + "'"


def _comment(text: str) -> str:
    return re.sub(r"[\r\n]+", " ", str(text))


def _header(rows: list[dict[str, Any]], by: str, at: str, mark: str = "#") -> list[str]:
    domain = rows[0]["domain"] if rows else ""
    new = [r for r in rows if not r["linked"]]
    linked = [r for r in rows if r["linked"]]
    out = [f"{mark} KratosForge: Entra ID groups for the {_comment(domain)} domain. Generated {at}" + (f" by {_comment(by)}." if by else "."),
           f"{mark} {len(new)} new group{'s' if len(new) != 1 else ''}. Safe to run again: a group that already exists is left alone."]
    if linked:
        out.append(f"{mark} Linked to groups you already have, so not created here: "
                   + "; ".join(f"{_comment(r['linked']['group_name'])} ({r['label']})" for r in linked) + ".")
    return out


def powershell(rows: list[dict[str, Any]], owners: list[str], by: str = "", at: str = "") -> str:
    """Microsoft Graph PowerShell that creates the suggested groups that don't exist yet."""
    new = [r for r in rows if not r["linked"] and _SAFE.match(r["suggested"])]
    lines = _header(rows, by, at) + [
        "# Run as a Groups Administrator (or anyone allowed to create security groups).",
        "# Needs the Microsoft.Graph module: Install-Module Microsoft.Graph -Scope CurrentUser",
        "",
        'Connect-MgGraph -Scopes "Group.ReadWrite.All","User.Read.All" -NoWelcome',
        "",
        "$groups = @(",
    ]
    lines += [f"  @{{ Name = {_ps(r['suggested'])}; MailNickname = {_ps(r['mail_nickname'])}; Description = {_ps(r['description'])} }}"
              for r in new]
    lines += [")", "$owners = @(" + ", ".join(_ps(o) for o in owners) + ")", "", """foreach ($g in $groups) {
  $found = Get-MgGroup -Filter "displayName eq '$($g.Name)'" -ConsistencyLevel eventual -CountVariable n
  if ($found) { Write-Host "Already there: $($g.Name)"; continue }
  $new = New-MgGroup -DisplayName $g.Name -MailNickname $g.MailNickname -Description $g.Description `
    -MailEnabled:$false -SecurityEnabled:$true
  foreach ($o in $owners) {
    $u = Get-MgUser -UserId $o -ErrorAction SilentlyContinue
    if (-not $u) { Write-Warning "No user ${o}: add the group's owners by hand"; continue }
    New-MgGroupOwnerByRef -GroupId $new.Id -BodyParameter @{ '@odata.id' = "https://graph.microsoft.com/v1.0/users/$($u.Id)" }
  }
  Write-Host "Created: $($g.Name) ($($new.Id))"
}"""]
    return "\n".join(lines) + "\n"


def az(rows: list[dict[str, Any]], owners: list[str], by: str = "", at: str = "") -> str:
    """The same with the Azure CLI, for bash (Cloud Shell, WSL, macOS or Linux)."""
    new = [r for r in rows if not r["linked"] and _SAFE.match(r["suggested"])]
    lines = ["#!/usr/bin/env bash"] + _header(rows, by, at) + [
        "# Run as a Groups Administrator after az login (--allow-no-subscriptions works without an Azure subscription).",
        "set -euo pipefail",
        "",
        "owners=(" + " ".join(_sh(o) for o in owners) + ")",
        "",
        "make_group() {",
        '  local name="$1" nick="$2" description="$3"',
        '  if [ -n "$(az ad group list --display-name "$name" --query "[?displayName==\'$name\'].id" -o tsv)" ]; then',
        '    echo "Already there: $name"; return',
        "  fi",
        '  local id; id=$(az ad group create --display-name "$name" --mail-nickname "$nick" --description "$description" --query id -o tsv)',
        '  for o in "${owners[@]}"; do',
        '    local uid; uid=$(az ad user show --id "$o" --query id -o tsv 2>/dev/null) || { echo "No user $o: add the group\'s owners by hand" >&2; continue; }',
        '    az ad group owner add --group "$id" --owner-object-id "$uid"',
        "  done",
        '  echo "Created: $name ($id)"',
        "}",
        "",
    ]
    lines += [f"make_group {_sh(r['suggested'])} {_sh(r['mail_nickname'])} {_sh(r['description'])}" for r in new]
    return "\n".join(lines) + "\n"


def table(rows: list[dict[str, Any]], owners: list[str]) -> str:
    """Every group as CSV, for building them by hand in the Entra admin center."""
    out = io.StringIO()
    w = csv.writer(out, lineterminator="\r\n")
    w.writerow(["Domain", "Role", "Environment", "Group name", "Mail nickname", "Description", "Owners", "Who goes in",
                "What it gets in Fabric", "Linked group ID"])
    for r in rows:
        name = r["linked"]["group_name"] if r["linked"] else r["suggested"]
        w.writerow([_cell(x) for x in (r["domain"], r["label"], ENV_NAMES.get(r["env"], "All"), name,
                                       "" if r["linked"] else r["mail_nickname"], r["description"],
                                       ", ".join(owners), r["who"], r["gets"], r["linked"]["group_id"] if r["linked"] else "")])
    return out.getvalue()


def _cell(value: Any) -> str:
    """A CSV cell Excel won't run as a formula."""
    text = str(value or "")
    return "'" + text if text[:1] in ("=", "+", "-", "@", "\t", "\r") else text


MANUAL_STEPS = (
    "1. In the Microsoft Entra admin center, open Groups > All groups > New group.",
    "2. Group type Security, the name and description from the file, membership type Assigned.",
    "3. Add the owners from the file, then the members (or let the owners add them).",
    "4. Then give each group its Fabric role: Fabric's Admin portal > Domains for domain owners and contributors, "
    "each workspace's Manage access for workspace roles, and the lakehouse's Manage OneLake data access for readers.",
)


def roles(rows: list[dict[str, Any]], workspaces: dict[str, list[dict[str, str]]], by: str = "", at: str = "") -> str:
    """PowerShell that gives the groups their Fabric roles, once they exist. ``workspaces`` maps each
    environment to the domain's workspaces there [{id, name}]. Uses the Azure CLI's sign-in for a Fabric
    token; a domain role needs a Fabric administrator, a workspace role an admin of that workspace."""
    lines = _header(rows, by, at)[:1] + [
        "# Gives each group its Fabric role. Run after the groups exist, signed in with az login.",
        "# Domain roles need a Fabric administrator; workspace roles need an admin of each workspace.",
        "# A group that already has a role in a workspace is reported and left as it is.",
        "",
        '$token = az account get-access-token --resource https://api.fabric.microsoft.com --query accessToken -o tsv',
        '$headers = @{ Authorization = "Bearer $token" }',
        "",
        "function Get-GroupId($name, $id) {",
        "  if ($id) { return $id }",
        '  $found = az ad group list --display-name $name --query "[?displayName==\'$name\'].id" -o tsv',
        '  if (-not $found) { Write-Warning "No group $name yet: create the groups first"; return $null }',
        "  return $found",
        "}",
        "",
        "function Grant-Workspace($workspace, $label, $groupId, $role) {",
        "  if (-not $groupId) { return }",
        '  $body = @{ principal = @{ id = $groupId; type = "Group" }; role = $role } | ConvertTo-Json',
        f'  try {{ Invoke-RestMethod -Method Post -Uri "{FABRIC}/workspaces/$workspace/roleAssignments" -Headers $headers -ContentType "application/json" -Body $body | Out-Null',
        '        Write-Host "$label gets $role" }',
        '  catch { Write-Warning "$label in workspace ${workspace}: $($_.Exception.Message)" }',
        "}",
        "",
    ]
    manual = []
    for r in rows:
        gid = r["linked"]["group_id"] if r["linked"] else ""
        name = r["linked"]["group_name"] if r["linked"] else r["suggested"]
        ref = f"(Get-GroupId {_ps(name)} {_ps(gid)})"
        kind = (r.get("fabric") or [None, None])
        if kind[0] == "domain":
            if not r["fabric_domain"]:
                manual.append(f"# {_comment(r['domain'])} isn't in Fabric yet: give {_comment(name)} the {kind[1]} role there once it is.")
                continue
            lines += [f"# {_comment(r['label'])}: {_comment(name)}",
                      f"$gid = {ref}",
                      "if ($gid) {",
                      f'  $body = @{{ type = {_ps(kind[1])}; principals = @(@{{ id = $gid; type = "Group" }}) }} | ConvertTo-Json -Depth 4',
                      f'  Invoke-RestMethod -Method Post -Uri "{FABRIC}/admin/domains/{r["fabric_domain"]}/roleAssignments/bulkAssign" -Headers $headers -ContentType "application/json" -Body $body | Out-Null',
                      f'  Write-Host {_ps(name + " is " + kind[1] + " of " + r["domain"])}',
                      "}", ""] if _SAFE.match(r["fabric_domain"]) else []
        elif kind[0] == "workspace":
            spaces = workspaces.get(r["env"]) or []
            if not spaces:
                manual.append(f"# No {ENV_NAMES.get(r['env'], '')} workspace of {_comment(r['domain'])} is known yet: "
                              f"give {_comment(name)} {kind[1]} in each one's Manage access.")
                continue
            lines += [f"# {_comment(r['label'])}: {_comment(name)}", f"$gid = {ref}"]
            lines += [f"Grant-Workspace {_ps(w['id'])} {_ps(name + ' in ' + w.get('name', w['id']))} $gid {_ps(kind[1])}"
                      for w in spaces if _SAFE.match(w["id"])]
            lines.append("")
        elif r["role"] == "readers":
            manual.append(f"# {_comment(name)}: in each {ENV_NAMES.get(r['env'], '')} lakehouse that holds {_comment(r['domain'])} data products, "
                          "Manage OneLake data access > add it to a role that reads the product's tables.")
    if manual:
        lines += ["# Do these by hand:"] + manual
    return "\n".join(lines) + "\n"


KINDS = {"powershell": ("ps1", powershell), "az": ("sh", az), "csv": ("csv", table), "roles": ("ps1", roles)}
