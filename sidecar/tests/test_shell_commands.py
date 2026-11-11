"""The shell's commands (app/src-tauri/src/main.rs) are what the app's page can ask the exe to do. Each
one is listed here with what it does with text the page sends: the check it runs on that text, found in
its body, or why it takes none. A new command, or a new argument on one, fails here until it is listed.

The origin and window checks in front of every command are the shell's own Rust tests
(only_the_apps_own_page_can_use_the_apps_commands); the page's CSP is pinned there too
(the_main_windows_policy_allows_no_eval_wildcard_or_remote_connection)."""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SHELL = ROOT / "app" / "src-tauri" / "src"
MAIN = (SHELL / "main.rs").read_text(encoding="utf-8")


def _without_tests(text: str) -> str:
    """The code less its #[cfg(test)] modules (they start and spawn whatever they like). rustfmt's
    layout: a module ends at the first closing brace on its own line at the module's indent."""
    out = []
    while (at := re.search(r"^( *)#\[cfg\(test\)\]\n *mod \w+ \{\n", text, re.M)):
        close = "\n" + at.group(1) + "}\n"
        stop = text.index(close, at.end() - 1) + len(close)
        out.append(text[:at.start()])
        text = text[stop:]
    return "".join(out) + text


CODE = _without_tests(MAIN)

# Argument types that carry nothing from the page: Tauri fills them in itself.
FROM_TAURI = ("tauri::AppHandle", "tauri::Window", "tauri::Webview", "tauri::State<")
# Plain numbers and flags can't name a path, a link or a program.
PLAIN = ("bool", "f64", "Option<bool>")

# command -> (text its body must contain: the check on what the page sent, or None; what it does)
COMMANDS: dict[str, tuple[str | None, str]] = {
    "rpc": (None, ("passes method and params to the engine over its pipe; the engine refuses an unknown "
                   "method and checks each method's own arguments, so the shell reads neither")),
    "stop_kernel": (None, "no page text: ends the running engine"),
    "confirm_started": (None, "no page text; main window only"),
    "check_components": (None, "no page text; main window only"),
    "check_for_update": (None, "no page text; main window only"),
    "update_sidecar": (None, "no page text; main window only"),
    "update_app": ("update_guard::is_commit(&commit)", "commit must be a build commit before any download"),
    "fabric_view_show": ("fabric_page(&page, tenant.as_deref())?",
                         "page must be an https app.fabric.microsoft.com page; tenant is used only as a GUID"),
    "fabric_view_hide": (None, "no page text"),
    "fabric_view_action": ('_ => Err(format!("unknown action {action}"))',
                           "action is one of four fixed words; page goes through fabric_page"),
    "fabric_view_url": (None, "no page text"),
    "fabric_view_sign_out": (None, "no page text"),
    "open_doc": ('!page.contains("..")', "only letters, digits and -_.#/ with no .., under docs/ on GitHub"),
    "open_link": ('"https" => open_in_browser(&url)', "only an https link to the browser, or a mailto: address"),
    "open_in_vscode": ("vscode_link::inside(&p, &notebook_folders())",
                       ("a full local path, checked as text first, then canonicalised and kept inside the "
                        "notebooks folder or an opened repository")),
    "add_to_vscode": ("vscode_link::install_link(&launcher, &client_id, insiders)?",
                      "the link is built from the app's own launcher path; vscode_link checks client_id"),
    "pick_folder": ("folder_pick::pick(owner, start)",
                    "start only sets where the dialog opens, and only for a local folder (folder_pick.rs)"),
    "take_pending_link": (None, "no page text; main window only"),
    "pop_out_open": ("pop_out_title(&section)?", "section must be one of the fixed pop-out sections"),
    "pop_out_close": ("pop_out_title(&section)?", "section must be one of the fixed pop-out sections"),
    "pop_out_on_top": (None, "a flag, for the calling pop-out window only"),
    "pop_out_list": (None, "no page text"),
    "pop_out_open_in_fabric": ("fabric_page(&page, None)?", "page must be an https app.fabric.microsoft.com page"),
}


def _handler() -> list[str]:
    listed = MAIN.split("invoke_handler(guarded(tauri::generate_handler![", 1)[1].split("]", 1)[0]
    return [c.strip() for c in listed.split(",") if c.strip()]


def _signature_and_body(command: str) -> tuple[str, str]:
    at = re.search(r"#\[tauri::command\]\s*\n(?:\s*#\[[^\n]*\]\s*\n)*\s*(?:async\s+)?fn " + command + r"\(", MAIN)
    assert at, f"{command} is a #[tauri::command] fn in main.rs"
    rest = MAIN[at.end():]
    depth, i = 1, 0
    while depth:
        depth += {"(": 1, ")": -1}.get(rest[i], 0)
        i += 1
    signature, body = rest[:i - 1], rest[i:].split("\n}\n", 1)[0]
    return signature, body


def _arguments(signature: str) -> list[tuple[str, str]]:
    out, depth, part = [], 0, ""
    for ch in signature + ",":
        if ch == "," and depth == 0:
            if part.strip():
                name, _, kind = part.partition(":")
                out.append((name.strip(), " ".join(kind.split())))
            part = ""
            continue
        depth += {"<": 1, ">": -1, "(": 1, ")": -1}.get(ch, 0)
        part += ch
    return out


def test_every_command_the_page_can_call_is_listed_here():
    handler = _handler()
    assert len(handler) == len(set(handler))
    assert sorted(handler) == sorted(COMMANDS), (
        f"new: {sorted(set(handler) - set(COMMANDS))}, gone: {sorted(set(COMMANDS) - set(handler))}. "
        "List each new command above with the check its body runs on what the page sends, or why it takes none")
    # Nothing outside the guarded handler can be called: one handler, and no command left out of it.
    assert CODE.count("invoke_handler(") == 1
    defined = set(re.findall(r"#\[tauri::command\]\s*\n(?:\s*#\[[^\n]*\]\s*\n)*\s*(?:async\s+)?fn (\w+)\(", MAIN))
    assert defined == set(handler)
    for other in SHELL.glob("*.rs"):
        if other.name != "main.rs":
            assert "#[tauri::command]" not in other.read_text(encoding="utf-8"), other.name


def test_each_command_checks_what_the_page_sends_before_using_it():
    for command, (check, why) in COMMANDS.items():
        signature, body = _signature_and_body(command)
        from_page = [(n, k) for n, k in _arguments(signature)
                     if not k.startswith(FROM_TAURI) and k not in PLAIN]
        if command == "rpc":
            assert [n for n, _ in from_page] == ["method", "params"], from_page
            continue
        if not from_page:
            assert check is None, f"{command} takes no text from the page; its entry needs no check"
            continue
        assert check, f"{command} takes {from_page} from the page: name the check its body runs ({why})"
        assert check in body, f"{command}'s body no longer contains its check: {check}"


def test_the_shell_starts_programs_only_by_their_full_path():
    """A bare program name is looked up on PATH, where a folder the person can write may come first.
    Outside tests, the shell names its programs by full path (system_tool, the engine's python, its own
    exe); xdg-open is the non-Windows development build's only exception."""
    for rs in SHELL.glob("*.rs"):
        code = _without_tests(rs.read_text(encoding="utf-8"))
        for name in re.findall(r'Command::new\("([^"]+)"\)', code):
            assert name == "xdg-open", f"{rs.name} starts {name!r} by name"
