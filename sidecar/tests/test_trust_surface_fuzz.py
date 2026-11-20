"""Hostile input at the engine's Windows trust surfaces (WR-413): the path a kratosforge://notebook link
names (notebook_name_for_path), the notebook name a link or a page asks for, the folder a VS Code window
pairs from, and the token a paired window sends. Each is refused cleanly: a ValueError (or the
surface's own refusal), never another exception, never a path outside its folder, and never a token
that wasn't issued.

The fuzz is seeded (random.Random(seed) over SEEDS), so a failure names the seed and repeats exactly.
The shell's own parsing of the link is fuzzed in deep_link.rs."""

from __future__ import annotations

import hmac
import ntpath
import os
import random
import secrets
import string
from pathlib import Path

import pytest

from fabric_desktop import editor_pairing as ep
from fabric_desktop import editor_service as es
from fabric_desktop.editor_pairing import Pairings
from fabric_desktop.notebook import check_notebook_name
from fabric_desktop.service import Service

SEEDS = range(40)
ROUNDS = 60

# Paths and names a link, a window or a page could send to do harm.
HOSTILE = [
    "", " ", ".", "..", "../", "..\\", "../../etc/passwd", "..\\..\\Windows\\win.ini", "a/../../b", "a\\..\\..\\b",
    "%2e%2e%2f", "%2e%2e/%2e%2e/x", "%252e%252e%252f", "..%c0%af", "..%c1%9c", "%c0%ae%c0%ae/",
    "/etc/passwd", "/", "//", "\\", "C:", "C:\\", "C:/", "C:x.ipynb", "c:..\\x", "Z:\\..\\..\\x.ipynb",
    "\\\\server\\share\\x.ipynb", "//server/share/x.ipynb", "\\\\server@SSL\\dav\\x.ipynb", "\\\\server@SSL@443\\DavWWWRoot\\x",
    "\\\\?\\C:\\x.ipynb", "\\\\.\\C:\\x.ipynb", "\\\\?\\UNC\\server\\share\\x.ipynb", "\\\\.\\UNC\\server\\share",
    "\\\\?\\GLOBALROOT\\Device\\Mup\\server\\share", "\\??\\UNC\\server\\share", "\\\\.\\pipe\\x", "/\\server\\share",
    "x\x00.ipynb", "x.ipynb\x00", "\x00", "a\nb", "a\rb", "a\tb", "a\x1bb", "a\x7fb", "a\x85b", "a\u2028b",
    "a\u202eb", "Daily\u202ebnpi.exe", "\u200f", "\u2066x\u2069", "a\u061cb",
    "a\ud800b", "\udcff", "\ufeffx", "C\uff1a\uff3cx.ipynb", "\uff0e\uff0e\uff0fx", "\u2024\u2024/x", "x\u2215..\u2215y",
    "CON", "con.ipynb", "NUL", "COM1", "LPT9.txt", "x.ipynb:stream", "x.ipynb::$DATA", "x.ipynb.", "x.ipynb ",
    "a" * 300, "a" * 5000, "a/" * 50 + "x", "%" * 20, "%zz", "%", "a" * 70_000,
]

# Pieces a random hostile value is built from.
PIECES = ["..", ".", "/", "\\", "\\\\", "//", "C:", ":", "%2e", "%2f", "%5c", "%25", "%00", "\x00", "\n", "\x1b",
          "\u202e", "\u200f", "\ud800", "\uff0e", "\uff0f", "\uff3c", "?", "*", "<", ">", "|", '"', "UNC", "@SSL",
          "server", "share", "x", "Sales", "Daily", ".ipynb", ".resources", " ", "CON", "é", "📊"]


def fuzzed(rng: random.Random) -> str:
    if rng.random() < 0.2:
        return "".join(chr(rng.randrange(0, 0x110000)) for _ in range(rng.randrange(0, 40)))
    return "".join(rng.choice(PIECES) for _ in range(rng.randrange(0, 14)))


def values(seed: int) -> list[str]:
    rng = random.Random(seed)
    return [fuzzed(rng) for _ in range(ROUNDS)]


def under(path: str, root: str) -> bool:
    """Whether ``path`` is ``root`` or below it, once both are made absolute."""
    try:
        full, top = os.path.normcase(os.path.abspath(path)), os.path.normcase(os.path.abspath(root))
        return os.path.commonpath([full, top]) == top
    except ValueError:  # another drive on Windows
        return False


# ---- notebook names (kratosforge://notebook?name=..., and every page call that takes one)


@pytest.mark.parametrize("seed", SEEDS)
def test_a_hostile_notebook_name_is_refused_or_stays_in_the_notebooks_folder(seed, tmp_path):
    folder = tmp_path / "notebooks"
    for name in HOSTILE + values(seed):
        try:
            checked = check_notebook_name(name)
        except ValueError:
            continue
        path = folder / f"{checked}.ipynb"
        assert under(str(path), str(folder)), (seed, name)
        assert ".." not in Path(checked).parts and not ntpath.isabs(checked) and not os.path.isabs(checked), (seed, name)
        assert not any(c in checked for c in "\x00\\:"), (seed, name)


# ---- the path a kratosforge://notebook?path=... link names


@pytest.fixture(scope="module")
def svc(tmp_path_factory):
    root = tmp_path_factory.mktemp("trust")
    s = Service(root=root / "app")
    s.save_notebook("Sales/Daily", [{"type": "code", "source": "x = 1"}])
    yield s
    s.kernel.shutdown() if hasattr(s.kernel, "shutdown") else None


@pytest.mark.parametrize("seed", SEEDS)
def test_a_hostile_link_path_names_no_notebook_and_raises_nothing(seed, svc):
    home = svc.notebook_file_path("Sales/Daily")["path"]
    folder = svc.notebook_file_path("Sales/Daily")["notebooks_folder"]
    tried = HOSTILE + values(seed) + [home + h for h in values(seed + 1000)[:20]] + [
        os.path.join(folder, h) for h in HOSTILE] + [os.path.join(folder, "Sales", "..", "..", "..", "x.ipynb")]
    for path in tried:
        found = svc.notebook_name_for_path(path)
        assert set(found) <= {"name", "why"}, (seed, path)
        if found["name"] is not None:  # only a real notebook in the folder, by the name it's saved under
            assert found["name"] == "Sales/Daily", (seed, path)
            assert under(path, folder) and os.path.normcase(os.path.abspath(path)) == os.path.normcase(home), (seed, path)
    for path in (None, 5, b"C:\\x.ipynb", ["x"], {"p": 1}):
        assert svc.notebook_name_for_path(path) == {"name": None}


# ---- the folder a VS Code window pairs from, and the files it may then reach


@pytest.mark.parametrize("seed", SEEDS)
def test_a_hostile_pairing_folder_is_refused_in_words_without_an_exception(seed, tmp_path):
    for folder in HOSTILE + values(seed) + [None, 3, b"/x", ["x"]]:
        problem = es._folder_problem(folder)
        assert problem is None or isinstance(problem, str), (seed, folder)
        if isinstance(folder, str) and (any(c in folder for c in "\x00\n\r\t\x1b\x7f\x85\u202e\u200f\u2066\u061c")
                                        or not (os.path.isabs(folder) or ntpath.isabs(folder))):
            assert problem, (seed, folder)
    # Shares in any form, device paths, and paths on whichever drive is current: never a folder to pair from.
    for elsewhere in ("\\\\server\\share\\x", "//server/share/x", "\\\\?\\UNC\\server\\share\\x", "\\\\server@SSL\\dav\\x",
                      "\\\\.\\UNC\\server\\share\\x", "\\\\?\\GLOBALROOT\\Device\\Mup\\server\\share\\x",
                      "\\??\\UNC\\server\\share\\x", "\\\\.\\pipe\\x", "//./pipe/x", "\\Users\\ann\\repo", "/\\server\\share\\x"):
        assert es._folder_problem(elsewhere), elsewhere
    for local in ("C:\\Users\\ann\\repo", "C:/Users/ann/repo", "\\\\?\\C:\\Users\\ann\\repo"):
        assert es._folder_problem(local) is None, local


@pytest.mark.parametrize("seed", SEEDS)
def test_a_paired_window_reaches_only_files_below_its_folder(seed, tmp_path):
    window = str(tmp_path / "repo")
    rng = random.Random(seed)
    tried = HOSTILE + values(seed) + [os.path.join(window, h) for h in HOSTILE] + [
        window + rng.choice(["/", "\\", ""]) + fuzzed(rng) for _ in range(ROUNDS)] + [window, window + "-other/x.ipynb"]
    for path in tried:
        inside = es._inside(path, window)
        assert isinstance(inside, bool), (seed, path)
        if inside:
            assert "\x00" not in path and under(path, window), (seed, path)
            assert os.path.normcase(os.path.abspath(path)) != os.path.normcase(os.path.abspath(window)), (seed, path)
    for path in (None, 5, b"x"):
        assert es._inside(path, window) is False  # type: ignore[arg-type]
    assert es._inside(os.path.join(window, "x.ipynb"), "") is False


# ---- pairing tokens and requests


def _paired(p: Pairings, keep: str) -> str:
    req = p.request("C:/work/repo", "KratosForge for VS Code")
    p.decide(req["id"], True, keep=keep)
    return p.collect(req["id"])["token"]


@pytest.mark.parametrize("seed", SEEDS)
def test_only_an_issued_token_is_taken(seed, tmp_path):
    p = Pairings(tmp_path)
    issued = {_paired(p, "session"), _paired(p, "7d")}
    rng = random.Random(seed)
    alphabet = string.ascii_letters + string.digits + "-_"
    guesses = [ep.TOKEN_PREFIX + "".join(rng.choice(alphabet) for _ in range(rng.randrange(0, 60))) for _ in range(ROUNDS)]
    for token in issued:  # near misses: one character off, cut short, one longer, other case, other prefix
        i = rng.randrange(len(ep.TOKEN_PREFIX), len(token))
        guesses += [token[:i] + ("A" if token[i] != "A" else "B") + token[i + 1:], token[:-1], token + "A",
                    token.swapcase(), "KFP_" + token[4:], token[4:], " " + token, token + "\x00", token + "\n",
                    token.replace("-", "\u2010"), token + "\u202e", token[:10] + "\ud800" + token[11:]]
    guesses += [ep.TOKEN_PREFIX + h for h in HOSTILE + values(seed)] + HOSTILE + values(seed)
    guesses += [ep.TOKEN_PREFIX + "a" * 1_000_000, ep.TOKEN_PREFIX + "\udcff" * 10]
    for guess in guesses:
        if guess in issued:
            continue
        assert p.check(guess) is None, (seed, guess[:80])
    for odd in (None, 0, 1.5, b"kfp_x", ["kfp_x"], {"t": 1}):
        assert p.check(odd) is None
    for token in issued:
        assert p.check(token) is not None, "the issued tokens still work"


def test_a_token_is_compared_in_constant_time(tmp_path, monkeypatch):
    p = Pairings(tmp_path)
    token = _paired(p, "7d")
    _paired(p, "session")
    calls = []
    real = hmac.compare_digest
    monkeypatch.setattr(ep.hmac, "compare_digest", lambda a, b: calls.append((a, b)) or real(a, b))
    assert p.check(token) is not None
    assert len(calls) == 2, "every pairing is compared, the saved one and the session one"
    calls.clear()
    assert p.check(ep.TOKEN_PREFIX + secrets.token_urlsafe(32)) is None
    assert len(calls) == 2, "a wrong token is compared with each pairing too, not looked up"


@pytest.mark.parametrize("seed", SEEDS[:10])
def test_hostile_pairing_requests_are_refused_cleanly(seed, tmp_path):
    p = Pairings(tmp_path)
    for value in HOSTILE[:20] + values(seed)[:20] + [None, 3, ["x"], {"x": 1}]:
        assert p.collect(value) == {"state": "expired"}, (seed, value)
        with pytest.raises(LookupError):
            p.decide(value, True)  # type: ignore[arg-type]
        with pytest.raises(ValueError):
            p.decide("pq_0", True, keep=value)  # type: ignore[arg-type]
        try:
            req = p.request(value, value)
        except PermissionError:
            continue
        shown = p.waiting()[-1]
        assert shown["window"] == value and all(c.isprintable() for c in shown["client"]) and len(shown["client"]) <= 80
        p.decide(req["id"], False)
        assert p.collect(req["id"]) == {"state": "denied"}
    assert p.list() == []
