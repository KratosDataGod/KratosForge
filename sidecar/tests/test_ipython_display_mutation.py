"""Mutation batch 65: the IPython.display stand-in. A PNG's format, a text object's view when it has
both data and a URL, JSON text read as text, the display_* helpers' raw switch, the version this stands
in for, package specs, and install() leaving a real IPython alone. sys.modules and sys.meta_path are
restored by monkeypatch."""

from __future__ import annotations

import base64
import sys
import types

from fabric_desktop import ipython_display as ipd

PNG = b"\x89PNG\r\n\x1a\nrest"


def test_png_bytes_are_sniffed_as_png_and_shown_as_png():
    img = ipd.Image(PNG)
    assert img.format == "png"
    assert img._repr_mimebundle_() == {"image/png": base64.b64encode(PNG).decode("ascii")}
    assert ipd._sniff(PNG) == "png" and ipd._sniff(b"\xff\xd8\xff") == "jpeg" and ipd._sniff(b"GIF87a") == "gif"
    assert ipd._sniff(b"") == "png" and ipd._sniff(b"plain") == "png"


def test_an_object_with_data_and_a_url_isnt_named_by_its_url_nor_one_with_neither():
    both = ipd.HTML("<b>x</b>", url="https://example.com/x")
    assert repr(both) == "<fabric_desktop.ipython_display.HTML object>"
    assert repr(ipd.HTML()) == "<fabric_desktop.ipython_display.HTML object>"


def test_json_text_given_as_data_stays_text_and_expanded_is_off_by_default():
    j = ipd.JSON('{"a": 1}')
    assert j._repr_json_() == '{"a": 1}' and j.expanded is False
    assert ipd.JSON({"a": 1})._repr_json_() == {"a": 1}


class Shown:
    def __init__(self):
        self.calls = []

    def __call__(self, *objs, **kw):
        self.calls.append((objs, kw))


def test_display_helpers_wrap_values_unless_raw_and_wrap_text_even_when_raw():
    shown = Shown()
    mod = ipd.build(shown, lambda **_kw: None).display
    payload = {"a": 1}
    mod.display_json(payload)
    (obj,), _ = shown.calls[-1]
    assert isinstance(obj, ipd.JSON) and obj.data == payload
    mod.display_json(payload, raw=True)
    assert shown.calls[-1] == ((payload,), {})
    mod.display_html("<b>x</b>", raw=True)
    (obj,), _ = shown.calls[-1]
    assert isinstance(obj, ipd.HTML) and obj.data == "<b>x</b>"
    mod.display_png(PNG)
    (obj,), _ = shown.calls[-1]
    assert isinstance(obj, ipd.Image) and obj.format == "png"


def test_the_stand_in_says_which_ipython_display_it_stands_in_for():
    pkg = ipd.build(Shown(), lambda **_kw: None)
    assert pkg.version_info == (8, 30, 0, "") and pkg.__version__ == "8.30.0"
    assert pkg.get_ipython() is None


def test_only_ipython_and_ipython_core_are_packages(monkeypatch):
    finder = ipd._Finder()
    finder.pkg = ipd.build(Shown(), lambda **_kw: None)
    assert finder.find_spec("IPython").submodule_search_locations is not None
    assert finder.find_spec("IPython.core").submodule_search_locations is not None
    assert finder.find_spec("IPython.display").submodule_search_locations is None
    assert finder.find_spec("IPython.core.display").submodule_search_locations is None
    assert finder.find_spec("numpy") is None
    assert ipd._Finder().find_spec("IPython") is None   # nothing installed yet


def test_install_leaves_a_real_ipython_module_alone(monkeypatch):
    monkeypatch.setattr(sys, "meta_path", [f for f in sys.meta_path if f is not ipd._finder])
    monkeypatch.setattr(ipd._finder, "pkg", ipd._finder.pkg)
    real = types.ModuleType("IPython")
    real.marker = "real"
    monkeypatch.setitem(sys.modules, "IPython", real)
    pkg = ipd.build(Shown(), lambda **_kw: None)
    ipd.install(pkg)
    assert vars(real).get("display") is None and real.marker == "real"
    assert pkg.__kratosforge__ is True and ipd._finder.pkg is pkg and sys.meta_path[-1] is ipd._finder


def test_install_points_a_stand_in_a_cell_already_imported_at_the_new_kernel(monkeypatch):
    monkeypatch.setattr(sys, "meta_path", [f for f in sys.meta_path if f is not ipd._finder])
    monkeypatch.setattr(ipd._finder, "pkg", ipd._finder.pkg)
    old = ipd.build(Shown(), lambda **_kw: None)
    old.__kratosforge__ = True
    monkeypatch.setitem(sys.modules, "IPython", old)
    monkeypatch.setitem(sys.modules, "IPython.display", old.display)
    new_display = Shown()
    ipd.install(ipd.build(new_display, lambda **_kw: None))
    sys.modules["IPython.display"].display("x")
    assert new_display.calls == [(("x",), {})]
