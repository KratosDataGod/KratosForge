"""Source control on real, temporary Git repositories: a local bare repository stands in for the
remote, so nothing here touches the network or a Git provider."""

from __future__ import annotations

import json
import shutil
import subprocess
import time
from pathlib import Path

import pytest

from fabric_desktop.git import cli, ops, remote, secrets
from fabric_desktop.git.cli import GitError
from fabric_desktop.git.repo import Repository, init, notebook_text, parse_unified
from fabric_desktop.git_service import GitMixin

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="needs git")


def raw(cwd, *args):
    return subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, check=True, text=True).stdout


def write(root: Path, rel: str, text: str) -> Path:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8", newline="\n")
    return p


def _config(folder: Path) -> Path:
    cfg = folder / "gitconfig"
    cfg.write_text("[user]\n\tname = Test Person\n\temail = test@example.com\n[init]\n\tdefaultBranch = main\n"
                   "[core]\n\tautocrlf = false\n[protocol \"file\"]\n\tallow = always\n", encoding="utf-8")
    return cfg


@pytest.fixture(autouse=True)
def git_home(tmp_path, monkeypatch):
    """A Git configuration of its own, so the person's settings never leak into a test."""
    cfg = _config(tmp_path)
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(cfg))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    return cfg


@pytest.fixture(scope="module")
def first_commit(tmp_path_factory) -> Path:
    """One repository with its first commit, made once per module and copied for each test: a slow
    runner once took over 60 s (Git's time limit) for one test's own git init."""
    folder = tmp_path_factory.mktemp("template")
    with pytest.MonkeyPatch.context() as mp:
        mp.setenv("GIT_CONFIG_GLOBAL", str(_config(folder)))
        mp.setenv("GIT_CONFIG_NOSYSTEM", "1")
        r = init(folder / "project")
        write(r.root, "entities/orders.toml", "[load]\nmode = \"scd1\"\nkeys = [\"order_id\"]\n")
        r.stage_all()
        r.commit("First commit")
    return r.root


@pytest.fixture
def repo(tmp_path, first_commit) -> Repository:
    shutil.copytree(first_commit, tmp_path / "project")
    return Repository(tmp_path / "project")


@pytest.fixture
def remote_pair(tmp_path):
    """A bare 'remote' with one commit on main, and a clone of it."""
    bare = tmp_path / "remote.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(bare)], check=True)
    seed = init(tmp_path / "seed")
    write(seed.root, "README.md", "hello\n")
    seed.stage_all()
    seed.commit("Seed")
    raw(seed.root, "remote", "add", "origin", str(bare))
    raw(seed.root, "push", "-q", "-u", "origin", "main")
    return bare, seed


def wait(o: ops.Operations, op: dict, timeout: float = 60) -> dict:
    end = time.time() + timeout
    while time.time() < end:
        st = o.status(op["id"])
        if st["status"] != "running":
            return st
        time.sleep(0.05)
    raise AssertionError("operation didn't finish")


def clone(tmp_path, bare, name) -> Repository:
    o = ops.Operations()
    st = wait(o, o.clone(str(bare), str(tmp_path), name))
    assert st["status"] == "ok", st
    return Repository(tmp_path / name)


# ---- repositories


def test_init_makes_a_repository_and_refuses_one_inside_another(tmp_path):
    r = init(tmp_path / "new")
    assert (r.root / ".git").is_dir()
    assert r.status()["summary"] == "Nothing is committed yet."
    (r.root / "sub").mkdir()
    with pytest.raises(GitError) as e:
        init(r.root / "sub")
    assert e.value.kind == "exists"
    with pytest.raises(GitError):
        init(tmp_path / "missing" / "deeper")


def test_open_finds_the_top_of_the_repository_from_a_subfolder(repo):
    assert Repository(repo.root / "entities").root == repo.root


def test_a_folder_outside_git_is_reported(tmp_path):
    (tmp_path / "plain").mkdir()
    with pytest.raises(GitError) as e:
        Repository(tmp_path / "plain")
    assert e.value.kind == "not_repo"
    with pytest.raises(GitError):
        Repository("relative/path")


def test_paths_must_stay_inside_the_repository(repo):
    assert repo.rel("entities\\orders.toml") == "entities/orders.toml"
    assert repo.rel(str(repo.root / "entities" / "orders.toml")) == "entities/orders.toml"
    for bad in ("../outside.txt", "entities/../../x", ".git/config", "", str(repo.root.parent / "x")):
        with pytest.raises(GitError):
            repo.rel(bad)


# ---- status, staging and commits


def test_status_groups_changes_like_source_control(repo):
    write(repo.root, "entities/orders.toml", "[load]\nmode = \"scd2\"\n")
    write(repo.root, "sql/daily.sql", "select 1\n")
    write(repo.root, "notes.md", "x\n")
    repo.stage(["sql/daily.sql"])
    st = repo.status()
    assert [e["path"] for e in st["staged"]] == ["sql/daily.sql"] and st["staged"][0]["state"] == "added"
    assert [(e["path"], e["state"]) for e in st["unstaged"]] == [("entities/orders.toml", "modified")]
    assert [e["path"] for e in st["untracked"]] == ["notes.md"]
    assert st["changed"] == 3 and st["dirty"] and st["branch"] == "main"
    (repo.root / "entities" / "orders.toml").unlink()
    assert repo.status()["unstaged"][0]["state"] == "deleted"


def test_stage_unstage_and_commit(repo):
    write(repo.root, "a.txt", "a\n")
    write(repo.root, "b.txt", "b\n")
    repo.stage_all()
    repo.unstage(["b.txt"])
    st = repo.status()
    assert [e["path"] for e in st["staged"]] == ["a.txt"]
    out = repo.commit("Add a")
    assert out["commit"] and out["status"]["untracked"][0]["path"] == "b.txt"
    assert repo.log()["commits"][0]["subject"] == "Add a"


def test_unstage_before_the_first_commit(tmp_path):
    r = init(tmp_path / "fresh")
    write(r.root, "x.txt", "x\n")
    r.stage_all()
    r.unstage(["x.txt"])
    assert r.status()["untracked"][0]["path"] == "x.txt"


def test_commit_needs_a_message_staged_files_and_a_name(repo, git_home):
    with pytest.raises(GitError) as e:
        repo.commit("   ")
    assert e.value.kind == "invalid"
    with pytest.raises(GitError) as e:
        repo.commit("Nothing")
    assert e.value.kind == "nothing"
    git_home.write_text("[core]\n\tautocrlf = false\n", encoding="utf-8")
    write(repo.root, "c.txt", "c\n")
    repo.stage_all()
    with pytest.raises(GitError) as e:
        repo.commit("No name")
    assert e.value.kind == "identity"
    repo.set_identity("Chris", "chris@example.com")
    assert repo.commit("Named")["commit"]
    assert raw(repo.root, "log", "-1", "--format=%an").strip() == "Chris"
    with pytest.raises(GitError):
        repo.set_identity("Chris", "not-an-email")


def test_a_commit_message_with_quotes_and_lines_is_kept_exactly(repo):
    write(repo.root, "d.txt", "d\n")
    repo.stage_all()
    msg = 'Fix "orders" --amend $(rm -rf /)\n\nSecond line; `x`'
    repo.commit(msg)
    assert raw(repo.root, "log", "-1", "--format=%B").strip() == msg


def test_history_details_compare_and_old_versions(repo):
    first = repo.head()
    write(repo.root, "entities/orders.toml", "[load]\nmode = \"scd2\"\n")
    write(repo.root, "new.txt", "n\n")
    repo.stage_all()
    second = repo.commit("Second")["commit"]
    log = repo.log(limit=1)
    assert log["commits"][0]["commit"] == second and log["more"] is True
    d = repo.commit_detail(second)
    assert {(f["path"], f["code"]) for f in d["files"]} == {("entities/orders.toml", "M"), ("new.txt", "A")}
    root_detail = repo.commit_detail(first)
    assert root_detail["files"][0]["code"] == "A"  # the first commit compares with nothing
    cmp = repo.compare(first, second)
    assert cmp["only_in_target"] == 1 and len(cmp["files"]) == 2
    old = repo.file_at(first, "entities/orders.toml")
    assert old["read_only"] and "scd1" in old["text"]
    with pytest.raises(GitError):
        repo.file_at(first, "new.txt")
    with pytest.raises(GitError):
        repo.resolve("--output=/tmp/x")
    assert repo.log(path="new.txt")["commits"][0]["commit"] == second


# ---- diffs


def test_diffs_of_working_staged_untracked_and_commits(repo):
    write(repo.root, "entities/orders.toml", "[load]\nmode = \"scd2\"\nkeys = [\"order_id\"]\ntrack = [\"status\"]\n")
    d = repo.diff("entities/orders.toml")
    assert d["added"] == 2 and d["removed"] == 1 and d["view"] == "text"
    kinds = [(l["kind"], l["old"], l["new"]) for l in d["hunks"][0]["lines"]]
    assert ("-", 2, None) in kinds and ("+", None, 2) in kinds and (" ", 1, 1) in kinds
    assert repo.diff("entities/orders.toml", staged=True)["hunks"] == []
    repo.stage(["entities/orders.toml"])
    assert repo.diff("entities/orders.toml", staged=True)["added"] == 2
    write(repo.root, "brand_new.py", "print(1)\nprint(2)\n")
    u = repo.diff("brand_new.py")
    assert u["untracked"] and u["added"] == 2 and u["hunks"][0]["lines"][1]["new"] == 2
    second = repo.commit("Track status")["commit"]
    c = repo.diff("entities/orders.toml", base=f"{second}~1", target=second)
    assert c["added"] == 2 and c["removed"] == 1


def test_binary_files_are_not_shown_as_text(repo):
    (repo.root / "logo.png").write_bytes(b"\x89PNG\0\0\x01\x02")
    repo.stage_all()
    assert repo.diff("logo.png", staged=True)["binary"] is True


def nb(cells):
    return json.dumps({"cells": cells, "metadata": {}, "nbformat": 4, "nbformat_minor": 5}, indent=1)


def test_notebook_diffs_compare_code_and_leave_outputs_out(repo):
    write(repo.root, "nb.ipynb", nb([{"cell_type": "code", "source": ["x = 1\n"], "outputs": [], "execution_count": 1}]))
    repo.stage_all()
    repo.commit("Notebook")
    # Only outputs and counts changed: nothing to see in notebook view.
    write(repo.root, "nb.ipynb", nb([{"cell_type": "code", "source": ["x = 1\n"], "execution_count": 7,
                                      "outputs": [{"output_type": "stream", "text": ["1\n"]}]}]))
    d = repo.diff("nb.ipynb")
    assert d["view"] == "notebook" and d["hunks"] == []
    assert repo.diff("nb.ipynb", notebook=False)["added"] > 0
    write(repo.root, "nb.ipynb", nb([{"cell_type": "code", "source": ["x = 2\n"], "outputs": []}]))
    d = repo.diff("nb.ipynb")
    assert [l["text"] for l in d["hunks"][0]["lines"] if l["kind"] != " "] == ["x = 1", "x = 2"]
    assert notebook_text(b"not json") is None


def test_parse_unified_numbers_lines():
    out = parse_unified("diff --git a/x b/x\n--- a/x\n+++ b/x\n@@ -3,2 +3,3 @@ head\n a\n-b\n+c\n+d\n\\ No newline at end of file\n")
    assert out["added"] == 2 and out["removed"] == 1
    assert [(l["old"], l["new"]) for l in out["hunks"][0]["lines"]] == [(3, 3), (4, None), (None, 4), (None, 5)]


# ---- branches


def test_create_switch_and_delete_branches(repo):
    repo.create_branch("feature/orders")
    assert repo.branch() == "feature/orders"
    write(repo.root, "f.txt", "f\n")
    repo.stage_all()
    repo.commit("On the feature")
    repo.switch("main")
    names = {b["name"]: b for b in repo.branches()["local"]}
    assert set(names) == {"main", "feature/orders"} and names["main"]["current"]
    with pytest.raises(GitError) as e:
        repo.delete_branch("feature/orders")
    assert e.value.kind == "confirm"  # nothing is deleted without a yes
    with pytest.raises(GitError) as e:
        repo.delete_branch("feature/orders", confirm=True)
    assert e.value.kind == "unmerged_branch"  # its commit is on no other branch
    repo.delete_branch("feature/orders", confirm=True, force=True)
    assert [b["name"] for b in repo.branches()["local"]] == ["main"]
    with pytest.raises(GitError):
        repo.delete_branch("main", confirm=True)  # the current branch
    for bad in ("has space", "-x", "a..b", "x.lock", ""):
        with pytest.raises(GitError):
            repo.create_branch(bad)
    with pytest.raises(GitError) as e:
        repo.create_branch("main")
    assert e.value.kind == "exists"


def test_a_first_branch_can_be_renamed_before_any_commit(tmp_path):
    r = init(tmp_path / "empty")
    r.create_branch("develop")
    assert r.status()["branch"] == "develop"


def test_switching_with_uncommitted_changes_asks_first_and_never_loses_them(repo):
    repo.create_branch("other", switch=False)
    write(repo.root, "entities/orders.toml", "changed here\n")
    write(repo.root, "scratch.txt", "mine\n")
    with pytest.raises(GitError) as e:
        repo.switch("other")
    assert e.value.kind == "dirty" and {c["path"] for c in e.value.changes} == {"entities/orders.toml", "scratch.txt"}
    assert repo.branch() == "main"
    out = repo.switch("other", mode="stash")
    assert out["stashed"] and repo.branch() == "other" and not repo.status()["dirty"]
    stash = repo.stashes()[0]
    assert "before switching to other" in stash["message"]
    repo.restore_stash(stash["ref"])
    assert (repo.root / "scratch.txt").read_text() == "mine\n"
    assert (repo.root / "entities" / "orders.toml").read_text() == "changed here\n"
    out = repo.switch("main", mode="carry")
    assert not out["stashed"] and repo.status()["dirty"]


def test_carrying_changes_that_clash_is_refused_and_keeps_them(repo):
    repo.create_branch("clash")
    write(repo.root, "entities/orders.toml", "clash version\n")
    repo.stage_all()
    repo.commit("Clash")
    repo.switch("main")
    write(repo.root, "entities/orders.toml", "my edit\n")
    with pytest.raises(GitError) as e:
        repo.switch("clash", mode="carry")
    assert e.value.kind == "overwrite"
    assert repo.branch() == "main" and (repo.root / "entities" / "orders.toml").read_text() == "my edit\n"


def test_a_failed_switch_after_stashing_puts_the_changes_back(repo, monkeypatch):
    repo.create_branch("other", switch=False)
    write(repo.root, "entities/orders.toml", "keep me\n")
    real = repo.git

    def failing(*args, **kw):
        if args[:1] == ("switch",):
            raise GitError("simulated", kind="failed")
        return real(*args, **kw)
    monkeypatch.setattr(repo, "git", failing)
    with pytest.raises(GitError):
        repo.switch("other", mode="stash")
    monkeypatch.undo()
    assert (repo.root / "entities" / "orders.toml").read_text() == "keep me\n" and repo.stashes() == []


def test_discard_needs_a_yes_and_only_touches_unstaged_changes(repo):
    write(repo.root, "entities/orders.toml", "edited\n")
    write(repo.root, "junk.txt", "junk\n")
    with pytest.raises(GitError) as e:
        repo.discard(["entities/orders.toml", "junk.txt"])
    assert e.value.kind == "confirm"
    assert (repo.root / "junk.txt").exists()
    repo.discard(["entities/orders.toml", "junk.txt"], confirm=True)
    assert not repo.status()["dirty"] and not (repo.root / "junk.txt").exists()


# ---- remotes: clone, fetch, pull, push, conflicts


def test_clone_push_and_pull_through_a_remote(tmp_path, remote_pair):
    bare, seed = remote_pair
    a = clone(tmp_path, bare, "a")
    assert a.status()["upstream"] == "origin/main" and (a.root / "README.md").exists()
    a.create_branch("feature/x")
    write(a.root, "x.txt", "x\n")
    a.stage_all()
    a.commit("X")
    o = ops.Operations()
    st = wait(o, o.push(a))
    assert st["status"] == "ok", st
    assert a.status()["upstream"] == "origin/feature/x"
    assert "feature/x" in raw(bare, "branch")
    b = clone(tmp_path, bare, "b")
    b.switch("origin/feature/x")
    assert b.branch() == "feature/x" and (b.root / "x.txt").exists()
    write(b.root, "y.txt", "y\n")
    b.stage_all()
    b.commit("Y")
    assert wait(o, o.push(b))["status"] == "ok"
    assert wait(o, o.fetch(a))["status"] == "ok"
    assert a.status()["behind"] == 1
    st = wait(o, o.pull(a))
    assert st["status"] == "ok" and (a.root / "y.txt").exists()


def test_push_rejected_and_pull_conflicts_are_explained(tmp_path, remote_pair):
    bare, seed = remote_pair
    a, b = clone(tmp_path, bare, "a"), clone(tmp_path, bare, "b")
    for r, text in ((a, "from a\n"), (b, "from b\n")):
        write(r.root, "README.md", text)
        r.stage_all()
        r.commit(f"Edit {text.strip()}")
    o = ops.Operations()
    assert wait(o, o.push(a))["status"] == "ok"
    st = wait(o, o.push(b))
    assert st["status"] == "failed" and st["error_kind"] == "rejected" and "Pull first" in st["message"]
    st = wait(o, o.pull(b))  # fast-forward only: they diverged
    assert st["status"] == "failed" and st["error_kind"] == "diverged"
    st = wait(o, o.pull(b, mode="merge"))
    assert st["status"] == "failed" and st["error_kind"] == "conflict"
    assert st["result"]["conflicts"] == ["README.md"]
    s = b.status()
    assert s["merging"] and s["conflicts"][0]["how"] == "changed on both sides"
    d = b.diff("README.md")  # the file as it is now, with each side marked
    assert d["view"] == "conflict" and d["conflicts"] == 1
    assert [(l["side"], l["text"]) for l in d["hunks"][0]["lines"] if l["side"] in ("here", "remote")] == \
        [("here", "from b"), ("remote", "from a")]
    with pytest.raises(GitError):
        b.commit("Too early")
    with pytest.raises(GitError):
        b.switch("main")
    with pytest.raises(GitError) as e:
        b.abort_merge()
    assert e.value.kind == "confirm"
    b.abort_merge(confirm=True)
    assert not b.status()["merging"] and (b.root / "README.md").read_text() == "from b\n"


def test_a_resolved_conflict_can_be_committed(tmp_path, remote_pair):
    bare, _ = remote_pair
    a, b = clone(tmp_path, bare, "a"), clone(tmp_path, bare, "b")
    for r, text in ((a, "a\n"), (b, "b\n")):
        write(r.root, "README.md", text)
        r.stage_all()
        r.commit("Edit")
    o = ops.Operations()
    wait(o, o.push(a))
    wait(o, o.pull(b, mode="merge"))
    write(b.root, "README.md", "a and b\n")
    b.stage(["README.md"])
    assert b.commit("Merge origin/main")["commit"]
    assert not b.status()["merging"]


def test_clone_failures_are_explained_and_leave_nothing_behind(tmp_path):
    o = ops.Operations()
    missing = tmp_path / "nope.git"
    missing.mkdir()
    st = wait(o, o.clone(str(missing), str(tmp_path), "copy"))
    assert st["status"] == "failed" and st["error_kind"] in ("not_found", "failed")
    assert not (tmp_path / "copy").exists()
    (tmp_path / "taken").mkdir()
    (tmp_path / "taken" / "file").write_text("x")
    with pytest.raises(GitError) as e:
        o.clone(str(missing), str(tmp_path), "taken")
    assert e.value.kind == "exists"


def test_pull_and_push_need_a_remote_and_a_branch(repo):
    o = ops.Operations()
    with pytest.raises(GitError) as e:
        o.push(repo)
    assert e.value.kind == "no_remote"
    with pytest.raises(GitError) as e:
        o.pull(repo)
    assert e.value.kind == "no_upstream"
    with pytest.raises(GitError):
        o.fetch(repo)


def test_cancel_stops_a_running_operation(tmp_path, monkeypatch):
    o = ops.Operations()
    script = tmp_path / "slow.py"
    script.write_text("import time\ntime.sleep(30)\n")
    import sys
    monkeypatch.setattr(cli, "command", lambda cwd, args: [sys.executable, str(script)])
    op = o._start("fetch", None, ["fetch"], target=str(tmp_path / "x"))
    time.sleep(0.3)
    o.cancel(op["id"])
    st = wait(o, op, timeout=20)
    assert st["status"] == "cancelled"


def test_auth_and_network_failures_read_as_plain_words():
    cases = {
        "fatal: Authentication failed for 'https://dev.azure.com/org/p/_git/r/'": "auth",
        "fatal: could not read Username for 'https://github.com': terminal prompts disabled": "auth",
        "git@github.com: Permission denied (publickey).\nfatal: Could not read from remote repository.": "ssh_key",
        "Host key verification failed.": "ssh_host",
        "fatal: unable to access 'https://x/': Could not resolve host: x": "network",
        "remote: Repository not found.\nfatal: repository 'https://github.com/a/b/' not found": "not_found",
        "remote: error: GH006: Protected branch update failed": "protected",
        "fatal: transport 'ext' not allowed": "protocol",
    }
    for stderr, kind in cases.items():
        assert cli.explain(stderr).kind == kind, stderr
    err = cli.explain("fatal: unable to access 'https://user:s3cr3tpass@github.com/a/b/': The requested URL returned error: 500")
    assert "s3cr3tpass" not in err.details and "user" not in err.details


def test_addresses_are_checked_before_git_sees_them(tmp_path):
    for ok in ("https://github.com/KratosDataGod/fabric-desktop.git", "git@github.com:a/b.git",
               "ssh://git@ssh.dev.azure.com/v3/org/proj/repo", "https://org@dev.azure.com/org/proj/_git/repo",
               str(tmp_path)):
        assert remote.check_url(ok)
    for bad in ("http://github.com/a/b", "https://me:token@github.com/a/b", "https://ghp_" + "a" * 36 + "@github.com/a/b",
                "ext::sh -c touch% /tmp/pwned", "--upload-pack=evil", "fd::17", "ftp://x/y", "", "https://x y/z",
                str(tmp_path / "missing")):
        with pytest.raises(GitError):
            remote.check_url(bad)
    assert remote.repo_name("git@github.com:KratosDataGod/fabric-desktop.git") == "fabric-desktop"
    assert remote.safe_display("https://me:pw@github.com/a/b") == "https://github.com/a/b"


def test_add_remote_checks_name_and_address(repo):
    repo.add_remote("origin", "https://github.com/a/b.git")
    assert repo.remotes()[0]["provider"] == "github"
    with pytest.raises(GitError):
        repo.add_remote("origin", "https://github.com/a/c.git")
    with pytest.raises(GitError):
        repo.add_remote("-bad", "https://github.com/a/c.git")
    with pytest.raises(GitError):
        repo.add_remote("other", "https://me:pw@github.com/a/c.git")


def test_provider_links_for_github_and_azure_devops():
    gh = remote.links("git@github.com:KratosDataGod/fabric-desktop.git", "feature/x", "main", "abc123")
    assert gh["repository"] == "https://github.com/KratosDataGod/fabric-desktop"
    assert gh["new_pull_request"] == "https://github.com/KratosDataGod/fabric-desktop/compare/main...feature/x?expand=1"
    assert gh["commit"].endswith("/commit/abc123") and gh["label"] == "GitHub"
    for url in ("https://org@dev.azure.com/org/Proj/_git/repo", "git@ssh.dev.azure.com:v3/org/Proj/repo",
                "https://org.visualstudio.com/Proj/_git/repo"):
        ado = remote.links(url, "feature/x", "main")
        assert ado["repository"] == "https://dev.azure.com/org/Proj/_git/repo", url
        assert ado["new_pull_request"] == ("https://dev.azure.com/org/Proj/_git/repo/pullrequestcreate"
                                           "?sourceRef=feature%2Fx&targetRef=main")
    generic = remote.links("https://git.example.com/team/repo.git", "b")
    assert generic["provider"] == "git" and generic["repository"] == "https://git.example.com/team/repo"
    assert generic["new_pull_request"] is None
    assert remote.links("ssh://git@git.example.com/team/repo.git")["repository"] is None


# ---- secrets


KEY = "AccountKey=" + "Ab1+" * 21 + "=="


def test_staged_secrets_block_a_commit_until_confirmed(repo):
    write(repo.root, "nb.ipynb", nb([{"cell_type": "code", "source": ["x = 1\n"], "outputs": []},
                                     {"cell_type": "code", "source": [f"conn = 'DefaultEndpointsProtocol=https;{KEY}'\n"],
                                      "outputs": []}]))
    write(repo.root, ".env", "A=1\n")
    repo.stage_all()
    found = repo.scan_staged()
    kinds = {(f["path"], f["kind"]) for f in found}
    assert ("nb.ipynb", "Storage account key") in kinds and (".env", "Sensitive file") in kinds
    nb_finding = next(f for f in found if f["path"] == "nb.ipynb")
    assert nb_finding["where"] == "cell 2" and "Ab1+" in nb_finding["match"] and KEY[20:40] not in json.dumps(found)
    with pytest.raises(GitError) as e:
        repo.commit("Add notebook")
    assert e.value.kind == "secrets"
    assert repo.commit("Add notebook", allow_secrets=True)["secrets_ignored"] == len(found)


def test_scanning_ignores_placeholders_and_finds_tokens_in_outputs():
    assert secrets.scan_text('password = "<password>"\nPassword=${DB_PASSWORD};') == []
    jwt = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.abcdefghijklmnop"
    out = secrets.scan_file("n.ipynb", nb([{"cell_type": "code", "source": "token()",
                                            "outputs": [{"output_type": "stream", "text": jwt}]}]).encode())
    assert out and out[0]["where"] == "cell 1 output"
    assert secrets.scan_text("Server=x;User Id=a;Password=Hunter2pass;")[0]["kind"] == "Password in a connection string"
    assert secrets.scan_text("ghp_" + "A" * 36)[0]["kind"] == "GitHub token"
    assert secrets.scan_text("host = x\ntoken = dapi" + "0123456789abcdef" * 2)[0]["kind"] == "Databricks token"
    bare = secrets.scan_text("w = Workspace('dapi" + "0123456789abcdef" * 2 + "-3')")
    assert [f["kind"] for f in bare] == ["Databricks token"]
    assert secrets.scan_text("commit dapi" + "0123456789abcdef" * 2 + "0") == []  # 33 hex: not a token
    assert secrets.sensitive_name("certs/site.pfx") and not secrets.sensitive_name(".env.example")


def test_redaction_hides_credentials_in_messages():
    text = ("fatal: unable to access 'https://chris:ghp_" + "B" * 36 + "@github.com/a/b.git/'\n"
            "Authorization: Bearer eyJabc.def.ghijklmnop\n" + KEY + "\nhttp.extraheader=AUTHORIZATION: basic Zm9vOmJhcg==")
    out = secrets.redact(text)
    assert "ghp_" + "B" * 10 not in out and "chris" not in out and "Zm9vOmJhcg" not in out
    assert "Ab1+…[hidden]" in out and "https://***@github.com" in out


# ---- the service the page calls


class FakeNotebooks:
    def __init__(self, folder):
        self.folder = folder

    def path(self, name):
        return self.folder / f"{name}.ipynb"


class Svc(GitMixin):
    def __init__(self, root):
        self.root = root
        self.notebooks = FakeNotebooks(root / "notebooks")


def test_service_only_works_in_repositories_it_opened(tmp_path, repo):
    svc = Svc(tmp_path / "appdata")
    with pytest.raises(GitError):
        svc.git_status(str(repo.root))
    svc.git_open(str(repo.root / "entities"))
    assert svc.git_info()["current"] == str(repo.root)
    assert svc.git_status(str(repo.root))["running"] is None
    svc.git_forget(str(repo.root))
    assert svc.git_repos()["repos"] == [] and (repo.root / ".git").exists()  # forgetting deletes nothing


def test_service_returns_choices_instead_of_failing(tmp_path, repo):
    svc = Svc(tmp_path / "appdata")
    svc.git_open(str(repo.root))
    svc.git_create_branch(str(repo.root), "other", switch=False)
    write(repo.root, "entities/orders.toml", "dirty\n")
    out = svc.git_switch(str(repo.root), "other")
    assert out["needs_choice"] and out["changes"][0]["path"] == "entities/orders.toml"
    write(repo.root, "secret.txt", KEY + "\n")
    svc.git_stage_all(str(repo.root))
    out = svc.git_commit(str(repo.root), "Oops")
    assert out["blocked"] == "secrets" and out["findings"]


def test_service_clone_is_added_to_the_list_and_notebooks_show_their_branch(tmp_path, remote_pair):
    bare, _ = remote_pair
    svc = Svc(tmp_path / "appdata")
    op = svc.git_clone_start(str(bare), str(tmp_path), "cloned")
    while svc.git_op_status(op["id"])["status"] == "running":
        time.sleep(0.05)
    assert svc.git_op_status(op["id"])["status"] == "ok"
    assert svc.git_info()["current"] == str((tmp_path / "cloned").resolve())
    nbdir = svc.notebooks.folder
    svc.git_init(str(nbdir))
    write(nbdir, "Sales.ipynb", nb([]))
    info = svc.git_notebook_info("Sales")
    assert info["branch"] == "main" and info["state"] == "untracked" and info["path"] == "Sales.ipynb"
    assert svc.git_path_info(str(tmp_path / "elsewhere.txt")) is None


def test_a_failed_clone_leaves_no_read_only_git_files_behind(tmp_path, monkeypatch):
    """Windows refuses to delete a read-only file, and Git keeps its objects and packs read-only: the
    cleanup after a failed clone makes them writable, so the next clone to that folder can start."""
    import os
    import stat

    from fabric_desktop.git.ops import remove_clone

    dest = tmp_path / "half-cloned"
    pack = dest / ".git" / "objects" / "pack" / "pack-1.pack"
    pack.parent.mkdir(parents=True)
    pack.write_bytes(b"PACK")
    (dest / "README.md").write_text("x")
    os.chmod(pack, stat.S_IREAD)
    real = os.unlink

    def like_windows(path, *, dir_fd=None):  # Windows checks the link's own attributes, not its target's
        if not os.stat(path, dir_fd=dir_fd, follow_symlinks=False).st_mode & stat.S_IWRITE:
            raise PermissionError(13, "Access is denied", str(path))
        real(path, dir_fd=dir_fd)

    monkeypatch.setattr(os, "unlink", like_windows)
    shutil_left = tmp_path / "ignored"
    shutil_left.mkdir()
    (shutil_left / "p.pack").write_bytes(b"PACK")
    os.chmod(shutil_left / "p.pack", stat.S_IREAD)
    import shutil
    shutil.rmtree(shutil_left, ignore_errors=True)
    assert shutil_left.exists(), "the stand-in refuses read-only files, as Windows does"
    remove_clone(dest)
    assert not dest.exists()


def test_a_failed_clone_cleanup_never_changes_what_a_link_in_it_points_at(tmp_path):
    """A link in the half-made clone may point outside it: the cleanup removes the link and leaves
    the file it points at as it was, read-only."""
    import os
    import stat

    from fabric_desktop.git import ops

    outside = tmp_path / "outside.txt"
    outside.write_text("keep")
    os.chmod(outside, stat.S_IREAD)
    dest = tmp_path / "half-cloned"
    dest.mkdir()
    link = dest / "link.txt"
    try:
        link.symlink_to(outside)
    except OSError:
        pytest.skip("this account can't make symlinks")
    before = stat.S_IMODE(outside.stat().st_mode)
    ops._writable_then_retry(os.unlink, str(link), PermissionError(13, "Access is denied"))
    assert stat.S_IMODE(outside.stat().st_mode) == before and link.is_symlink()  # the link was left alone
    ops.remove_clone(dest)
    assert not dest.exists()
    assert outside.read_text() == "keep" and stat.S_IMODE(outside.stat().st_mode) == before
