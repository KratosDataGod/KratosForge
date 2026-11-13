"""The IPython.display stand-in's objects, read without installing it: each one's view, files read from
this PC, URLs named and never fetched, image formats, and display handles. sys.modules isn't touched."""

from __future__ import annotations

import base64
import json

from fabric_desktop import ipython_display as ipd


def test_an_object_with_only_a_url_names_it_and_one_with_data_doesnt():
    assert repr(ipd.HTML(url="https://example.com/x")) == "HTML: https://example.com/x"
    assert repr(ipd.HTML("<b>x</b>")) == "<fabric_desktop.ipython_display.HTML object>"


def test_text_kinds_show_their_own_text():
    assert ipd.Latex(r"$x^2$")._repr_latex_() == r"$x^2$" and ipd.Math is ipd.Latex
    assert repr(ipd.Pretty("plain")) == "plain" and repr(ipd.Pretty()) == ""
    code = ipd.Code("print(1)", language="python")
    assert repr(code) == "print(1)" and code.language == "python"
    assert repr(ipd.Javascript("alert(1)")) == "alert(1)"


def test_json_and_code_read_their_file_from_this_pc(tmp_path):
    (tmp_path / "d.json").write_text(json.dumps({"a": [1, 2]}), encoding="utf-8")
    assert ipd.JSON(filename=str(tmp_path / "d.json"), expanded=True)._repr_json_() == {"a": [1, 2]}
    (tmp_path / "c.py").write_text("x = 1\n", encoding="utf-8")
    assert repr(ipd.Code(filename=str(tmp_path / "c.py"))) == "x = 1\n"


def test_an_image_string_is_a_url_or_a_file_and_its_format_is_sniffed(tmp_path):
    web = ipd.Image("https://example.com/cat.png")
    assert web.data is None and web._repr_mimebundle_() == {"text/plain": "Image: https://example.com/cat.png"}
    jpeg = b"\xff\xd8\xff\xe0rest"
    (tmp_path / "pic").write_bytes(jpeg)
    pic = ipd.Image(str(tmp_path / "pic"))
    assert pic.format == "jpeg"
    assert pic._repr_mimebundle_() == {"image/jpeg": base64.b64encode(jpeg).decode("ascii")}
    assert ipd.Image(b"GIF89a....").format == "gif"
    assert ipd.Image(b"unknown").format == "png"


def test_a_display_handle_draws_and_updates_through_its_kernel():
    drawn = []
    handle = ipd.DisplayHandle("h1", lambda obj, **kw: drawn.append((obj, kw)))
    handle.display("a", metadata={})
    handle.update("b")
    assert drawn == [("a", {"display_id": "h1", "metadata": {}}), ("b", {"display_id": "h1", "update": True})]
    assert repr(handle) == "<DisplayHandle display_id=h1>"
    loose = ipd.DisplayHandle()
    assert len(loose.display_id) == 32
    loose.display("ignored")
    loose.update("ignored")  # no kernel: nothing happens


def test_the_built_module_binds_update_display_and_handles_to_one_kernel():
    drawn = []
    pkg = ipd.build(lambda *objs, **kw: drawn.append((objs, kw)), lambda **kw: None)
    pkg.display.update_display("x", display_id="d1")
    handle = pkg.display.DisplayHandle("d2")
    handle.update("y")
    assert drawn == [(("x",), {"display_id": "d1", "update": True}), (("y",), {"display_id": "d2", "update": True})]
    assert pkg.get_ipython() is None and pkg.core.display is pkg.display
