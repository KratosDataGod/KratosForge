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
