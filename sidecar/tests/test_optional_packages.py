"""The engine starts without a package only one feature needs: that feature says what's missing, the rest works.

A developer's own Python can be older than the engine (PyYAML arrived with the Ossie converter), and one missing
package used to stop every start with "No module named 'yaml'". Each case runs in a fresh Python that refuses
to import the package, then starts the JSON-lines server and asks it two things.
"""

import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

SIDECAR = Path(__file__).resolve().parents[1]

# Packages that only one feature needs: Ossie, notebook charts and pandas, the AI tools' local API, Format cell
# (ruff is run as a program, never imported), and Data warehouse queries.
OPTIONAL = ["yaml", "matplotlib", "pandas", "plotly", "bokeh", "seaborn", "altair", "mcp", "mssql_python"]

_SERVE = textwrap.dedent("""
    import importlib.abc, sys
    blocked = sys.argv[1]
    class Block(importlib.abc.MetaPathFinder):
        def find_spec(self, name, path, target=None):
            if name == blocked or name.startswith(blocked + "."):
                raise ModuleNotFoundError(f"No module named {name!r}", name=name)
    sys.meta_path.insert(0, Block())
    from fabric_desktop.server import main
    main()
""")


def _serve(blocked: str, home: Path, requests: list[dict]) -> list[dict]:
    env = {**os.environ, "PYTHONPATH": str(SIDECAR), "FABRIC_DESKTOP_HOME": str(home), "HOME": str(home),
           "USERPROFILE": str(home), "LOCALAPPDATA": str(home), "APPDATA": str(home)}
    stdin = "".join(json.dumps(r) + "\n" for r in requests)
    done = subprocess.run([sys.executable, "-c", _SERVE, blocked], input=stdin, capture_output=True, text=True,
                          env=env, timeout=120, check=False)
    assert done.returncode == 0, done.stderr[-2000:]
    return sorted((json.loads(line) for line in done.stdout.splitlines() if line.strip()), key=lambda r: r["id"])


@pytest.mark.parametrize("blocked", OPTIONAL)
def test_the_engine_starts_and_answers_without_an_optional_package(blocked, tmp_path):
    replies = _serve(blocked, tmp_path, [{"id": 1, "method": "no_such_method"}])
    assert replies == [{"id": 1, "error": "unknown method 'no_such_method'"}]


def test_without_pyyaml_only_ossie_is_off_and_says_why(tmp_path):
    script = textwrap.dedent("""
        import importlib.abc, json, sys
        class Block(importlib.abc.MetaPathFinder):
            def find_spec(self, name, path, target=None):
                if name == "yaml" or name.startswith("yaml."):
                    raise ModuleNotFoundError(f"No module named {name!r}", name=name)
        sys.meta_path.insert(0, Block())
        from fabric_desktop import model_ossie
        out = {"missing": model_ossie.MISSING, "unavailable": model_ossie.unavailable()}
        for fn, args in ((model_ossie.parse, ("name: x",)), (model_ossie.export, ({},)), (model_ossie.to_tmsl, ("x",))):
            try:
                fn(*args)
                out[fn.__name__] = "ran"
            except RuntimeError as exc:
                out[fn.__name__] = str(exc)
        print(json.dumps(out))
    """)
    done = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True, timeout=120, check=False,
                          env={**os.environ, "PYTHONPATH": str(SIDECAR)})
    assert done.returncode == 0, done.stderr[-2000:]
    out = json.loads(done.stdout)
    assert out["missing"] == "PyYAML"
    assert "need the Python package PyYAML" in out["unavailable"]
    assert "pip install -e . -c constraints.txt" in out["unavailable"]
    assert out["parse"] == out["export"] == out["to_tmsl"] == out["unavailable"]


def test_with_pyyaml_ossie_is_on():
    from fabric_desktop import model_ossie
    assert model_ossie.MISSING == ""
    assert model_ossie.unavailable() == ""
    model_ossie.require()
