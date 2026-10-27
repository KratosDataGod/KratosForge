"""display(..., display_id=True), handle.update and update_display: an output redrawn in place."""

from __future__ import annotations

from fabric_desktop.notebook import Kernel


def test_display_handles_update_their_output_in_place():
    k = Kernel()
    out = k.run("from IPython.display import display, update_display, Markdown, DisplayHandle\n"
                "h = display('start', display_id=True)\nprint('between')\n"
                "for i in range(3):\n    h.update(f'step {i}')\n"
                "display(Markdown('**a**'), display_id='m')\nupdate_display(Markdown('**b**'), display_id='m')\n"
                "update_display('ignored', display_id='never shown')\n"
                "d = DisplayHandle()\nd.display('one')\nd.update('two')")
    assert out["status"] == "ok"
    shown = [{k2: v for k2, v in o.items() if k2 != "display_id"} for o in out["outputs"]]
    assert shown == [{"type": "text", "text": "'step 2'"}, {"type": "stream", "text": "between\n"},
                     {"type": "markdown", "text": "**b**"}, {"type": "text", "text": "'two'"}]
    assert out["outputs"][2]["display_id"] == "m"
    assert k.run("repr(h).startswith('<DisplayHandle display_id=')")["outputs"][0]["text"] == "True"
    # An update outside ``with out:`` still reaches the Output widget it was shown in, from a later cell too.
    k.run("import ipywidgets as w\no = w.Output()\nwith o:\n    g = display('in', display_id=True)")
    k.run("g.update('changed')")
    assert k.run("o.outputs[0]['text']")["outputs"][0]["text"] == '"\'changed\'"'
    assert k.run("update_display('x')")["outputs"][0]["ename"] == "TypeError"  # as IPython: display_id is required


def test_an_update_cant_take_a_cell_or_an_output_widget_past_the_rich_cap(monkeypatch):
    import json

    from fabric_desktop import notebook
    monkeypatch.setattr(notebook, "RICH_CHARS", 1_000)
    k = Kernel()
    out = k.run("from IPython.display import display, HTML\nh = display(HTML('x'), display_id=True)\n"
                "h.update(HTML('y' * 5_000))")
    (o,) = out["outputs"]
    assert o["type"] == "text" and o["text"].startswith("Output truncated: this update") and o["display_id"]
    assert sum(len(json.dumps(v, default=str)) for v in out["outputs"]) < 1_000
    # A smaller update still lands where the big one would have gone.
    out = k.run("h2 = display(HTML('a'), display_id=True)\nh2.update(HTML('b' * 5_000))\nh2.update(HTML('c'))")
    assert [v.get("html") for v in out["outputs"]] == ["c"]
    # The same inside an ipywidgets Output.
    k.run("import ipywidgets as w\no = w.Output()\nwith o:\n    g = display(HTML('in'), display_id=True)\n"
          "    g.update(HTML('z' * 5_000))")
    assert k.run("o.outputs[0]['text'].startswith('Output truncated: this update')")["outputs"][0]["text"] == "True"
    k.run("g.update(HTML('small'))")
    assert k.run("o.outputs[0].get('html')")["outputs"][0]["text"] == "'small'"


def test_clear_output_inside_an_output_widget_clears_only_that_widget():
    k = Kernel()
    out = k.run("import ipywidgets as w\nfrom IPython.display import clear_output\no = w.Output()\n"
                "with o:\n    print('a')\n    clear_output()\n    print('b')\nprint('cell')")
    assert out["status"] == "ok", out
    assert [v.get("text") for v in out["outputs"]] == ["cell\n"]
    assert k.run("o.outputs")["outputs"][0]["text"] == "[{'type': 'stream', 'text': 'b\\n'}]"
