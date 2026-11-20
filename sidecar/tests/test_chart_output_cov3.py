"""chart_output.py's remaining paths, on a separately loaded copy of the module: plotly's renderers patched
once and PLOTLY_RENDERER kept, an import no finder can find, and install() patching what's already loaded."""

from __future__ import annotations

import importlib.util
import sys
import types

import pytest

from fabric_desktop import chart_output


@pytest.fixture
def co(monkeypatch):
    spec = importlib.util.spec_from_file_location("chart_output_cov3_copy", chart_output.__file__)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(sys, "meta_path", list(sys.meta_path))
    return module


class Renderers(dict):
    default = "browser"


@pytest.fixture
def fake_plotly(monkeypatch):
    """A stand-in plotly.io.base_renderers: importing the real plotly here, before a kernel installs its
    import hook, would leave plotly unpatched for later tests in the same process."""
    base = types.ModuleType("plotly.io.base_renderers")
    base.ExternalRenderer = type("ExternalRenderer", (), {})
    for name, module in (("plotly", types.ModuleType("plotly")), ("plotly.io", types.ModuleType("plotly.io")),
                         ("plotly.io.base_renderers", base)):
        monkeypatch.setitem(sys.modules, name, module)


def test_plotly_renderers_are_patched_once(co, monkeypatch, fake_plotly):
    monkeypatch.delenv("PLOTLY_RENDERER", raising=False)
    module = types.SimpleNamespace(renderers=Renderers())
    co._patch_plotly(module)
    first = module.renderers["kratosforge"]
    assert module.renderers.default == "kratosforge"
    module.renderers.default = "notebook"     # the notebook chose another one afterwards
    co._patch_plotly(module)
    assert module.renderers["kratosforge"] is first and module.renderers.default == "notebook"


def test_a_plotly_renderer_set_on_purpose_is_kept(co, monkeypatch, fake_plotly):
    monkeypatch.setenv("PLOTLY_RENDERER", "png")
    module = types.SimpleNamespace(renderers=Renderers())
    co._patch_plotly(module)
    assert "kratosforge" in module.renderers and module.renderers.default == "browser"


def test_an_import_no_other_finder_finds_is_left_to_python(co, monkeypatch):
    class Nothing:
        @staticmethod
        def find_spec(name, path, target=None):
            return None

    hook = co._PatchOnImport()
    monkeypatch.setattr(sys, "meta_path", [hook, object(), Nothing()])
    assert hook.find_spec(co.ALTAIR, None) is None
    assert hook.find_spec("json", None) is None   # not one it patches


def test_install_patches_a_module_already_imported(co, monkeypatch):
    seen = []
    fake = types.ModuleType("fake_chart_lib")
    monkeypatch.setattr(co, "_PATCHES", {"fake_chart_lib": seen.append, "not_imported_lib": seen.append})
    monkeypatch.setitem(sys.modules, "fake_chart_lib", fake)
    monkeypatch.delitem(sys.modules, "not_imported_lib", raising=False)
    emit, running = (lambda _o: True), (lambda: True)
    co.install(emit, running)
    assert seen == [fake]
    assert co._emit is emit and co._cell_running is running
    assert isinstance(sys.meta_path[0], co._PatchOnImport)
