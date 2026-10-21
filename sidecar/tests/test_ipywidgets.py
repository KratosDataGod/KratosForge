"""ipywidgets in notebook cells (widgets.py): controls, interact, Output, and events from the app."""

from __future__ import annotations

import pytest

from fabric_desktop import widgets
from fabric_desktop.notebook import WIDGET_GONE, Kernel


def _widget(run):
    return next(o for o in run["outputs"] if o["type"] == "widget")


def _find(view, kind):
    if view["kind"] == kind:
        return view
    for child in view.get("children", []):
        hit = _find(child, kind)
        if hit:
            return hit
    return None


def test_a_displayed_widget_is_drawn_from_its_state():
    k = Kernel()
    out = _widget(k.run("import ipywidgets as w\nw.IntSlider(value=5, min=0, max=10, description='n')"))
    assert out["view"] == {"id": out["model"], "kind": "slider", "description": "n", "disabled": False,
                           "value": 5, "min": 0, "max": 10, "step": 1}
    for code, kind in (("w.FloatSlider(2.5)", "slider"), ("w.Dropdown(options=['a', 'b'])", "dropdown"),
                       ("w.Checkbox(True)", "checkbox"), ("w.Text('x')", "text"), ("w.Button(description='Go')", "button"),
                       ("w.HBox([w.Label('a'), w.Textarea()])", "hbox"), ("w.RadioButtons(options={'One': 1})", "radio"),
                       ("w.SelectMultiple(options=[1, 2], value=[2])", "multiselect"), ("w.IntRangeSlider()", "range")):
        assert _widget(k.run(f"import ipywidgets as w\n{code}"))["view"]["kind"] == kind, code
    # IPython.display.display and from ipywidgets import widgets work too.
    out = _widget(k.run("from IPython.display import display\nfrom ipywidgets import widgets\ndisplay(widgets.ToggleButton(True))"))
    assert out["view"]["kind"] == "toggle" and out["view"]["value"] is True


def test_interact_builds_controls_and_reruns_the_function_on_a_change():
    k = Kernel()
    run = k.run("from ipywidgets import interact\n"
                "@interact(n=(1, 10), name=['a', 'b'], flag=True, scale=0.5)\n"
                "def f(n, name, flag, scale):\n    print(n, name, flag)\n    return n * 2\n")
    assert run["status"] == "ok"
    box = _widget(run)
    kinds = [c["kind"] for c in box["view"]["children"]]
    assert kinds == ["slider", "dropdown", "checkbox", "slider", "output"]
    slider, dropdown = box["view"]["children"][:2]
    assert (slider["min"], slider["max"], slider["value"]) == (1, 10, 5)
    assert box["view"]["children"][-1]["outputs"] == [{"type": "stream", "text": "5 a True\n"}, {"type": "text", "text": "10"}]
    ev = k.ipywidget_event(slider["id"], {"value": 9}, box["model"])
    assert ev["status"] == "ok" and ev["outputs"] == []
    assert _find(ev["view"], "output")["outputs"][0]["text"] == "9 a True\n"
    assert _find(ev["view"], "slider")["value"] == 9
    ev = k.ipywidget_event(dropdown["id"], {"index": 1}, box["model"])
    assert _find(ev["view"], "output")["outputs"][0]["text"] == "9 b True\n"
    # A value past the slider's ends is held at them.
    ev = k.ipywidget_event(slider["id"], {"value": 99}, box["model"])
    assert _find(ev["view"], "slider")["value"] == 10


def test_observe_and_on_click_run_in_the_kernel_and_their_output_comes_back():
    k = Kernel()
    k.run("import ipywidgets as w\nseen = []\ns = w.IntSlider()\ns.observe(lambda c: seen.append((c.old, c['new'])), names='value')\n"
          "b = w.Button(description='Go')\nb.on_click(lambda b: print('clicked'))")
    s = k.namespace["s"]
    b = k.namespace["b"]
    assert k.ipywidget_event(s.model_id, {"value": 7})["status"] == "ok"
    assert k.namespace["seen"] == [(0, 7)]
    assert k.ipywidget_event(b.model_id, {"click": True})["outputs"] == [{"type": "stream", "text": "clicked\n"}]
    # Setting a value in code tells observers too, as traitlets does.
    k.run("s.value = 3")
    assert k.namespace["seen"][-1] == (7, 3)


def test_output_widget_captures_prints_displays_and_figures():
    k = Kernel()
    run = k.run("import ipywidgets as w\nimport polars as pl\nout = w.Output()\nwith out:\n    print('hi')\n"
                "    display(pl.DataFrame({'a': [1]}))\nprint('cell')\nout")
    assert run["outputs"][0] == {"type": "stream", "text": "cell\n"}
    view = _widget(run)["view"]
    assert view["outputs"][0] == {"type": "stream", "text": "hi\n"}
    assert view["outputs"][1]["type"] == "table" and view["outputs"][1]["frame"]
    run = k.run("import matplotlib\nmatplotlib.use('Agg')\nimport matplotlib.pyplot as plt\nout2 = w.Output()\n"
                "with out2:\n    plt.plot([1, 2])\nout2")
    assert [o["type"] for o in _widget(run)["view"]["outputs"]] == ["image"]
    assert [o["type"] for o in run["outputs"]] == ["widget"], "the figure went to the Output, not the cell"


def test_a_handler_error_shows_under_the_control_and_the_kernel_keeps_going():
    k = Kernel()
    k.run("import ipywidgets as w\nb = w.Button()\nb.on_click(lambda b: 1 / 0)")
    ev = k.ipywidget_event(k.namespace["b"].model_id, {"click": True})
    assert ev["status"] == "error" and ev["outputs"][0]["ename"] == "ZeroDivisionError"
    assert k.run("1 + 1")["status"] == "ok"


def test_a_slow_handler_is_stopped(monkeypatch):
    import fabric_desktop.notebook as nb
    monkeypatch.setattr(nb, "WIDGET_SECONDS", 0.5)
    k = Kernel()
    k.run("import ipywidgets as w, time\nb = w.Button()\ndef spin(_b):\n    while True:\n        pass\nb.on_click(spin)\n"
          "c = w.Button()\nc.on_click(lambda b: time.sleep(5))")
    ev = k.ipywidget_event(k.namespace["b"].model_id, {"click": True})
    assert ev["status"] == "cancelled" and ev["outputs"][-1]["text"].startswith("Stopped: the handler ran longer")
    ev = k.ipywidget_event(k.namespace["c"].model_id, {"click": True})
    assert ev["status"] == "cancelled" and ev["outputs"][-1]["text"].startswith("Stopped waiting")


def test_after_a_restart_the_control_asks_for_the_cell_again():
    k = Kernel()
    model = _widget(k.run("import ipywidgets as w\nw.IntSlider()"))["model"]
    k.restart()
    assert k.ipywidget_event(model, {"value": 1}) == {"gone": True, "message": WIDGET_GONE}


def test_events_wait_for_a_running_cell():
    k = Kernel()
    k.run("import ipywidgets as w\ns = w.IntSlider()")
    k.start("import time\ntime.sleep(1)")
    with pytest.raises(RuntimeError, match="A cell is running"):
        k.ipywidget_event(k.namespace["s"].model_id, {"value": 2})
    k._current.done.wait()


def test_disabled_controls_and_password_values_stay_in_the_kernel():
    k = Kernel()
    k.run("import ipywidgets as w\ns = w.IntSlider(disabled=True)\np = w.Password(value='hunter2')")
    k.ipywidget_event(k.namespace["s"].model_id, {"value": 50})
    assert k.namespace["s"].value == 0
    assert widgets.view(k.namespace["p"]) == {"id": k.namespace["p"].model_id, "kind": "password", "description": "",
                                              "disabled": False, "value": "", "placeholder": "", "filled": True}
    k.ipywidget_event(k.namespace["p"].model_id, {"value": "new"})
    assert k.namespace["p"].value == "new"


def test_a_box_that_contains_itself_is_drawn_once():
    box = widgets.VBox()
    box.children = (box, widgets.Label("x"))
    view = widgets.view(box)
    assert view["children"][0]["value"] == "(shown above)"


def test_the_process_kernel_runs_widget_events_in_the_cell_process():
    from fabric_desktop.kernel_proc import ProcessKernel
    k = ProcessKernel()
    try:
        snap = k.start("import ipywidgets as w\nb = w.Button()\nb.on_click(lambda b: print('pid'))\nb")
        while snap["status"] == "running":
            snap = k.poll(snap["version"], 0.5)
        model = _widget(snap)["model"]
        assert k.ipywidget_event(model, {"click": True})["outputs"] == [{"type": "stream", "text": "pid\n"}]
    finally:
        k.stop()
    assert k.ipywidget_event(model, {"click": True})["gone"]


def _slow_button(k):
    return _widget(k.run("import ipywidgets as w, time\nb = w.Button()\nb.on_click(lambda _b: time.sleep(3))\nb"))["model"]


def test_a_handler_stuck_in_native_code_holds_cells_and_controls_until_it_returns(monkeypatch):
    import fabric_desktop.notebook as nb
    monkeypatch.setattr(nb, "WIDGET_SECONDS", 0.3)
    k = Kernel()
    model = _slow_button(k)
    assert k.ipywidget_event(model, {"click": True})["status"] == "cancelled"
    with pytest.raises(RuntimeError, match="handler is still running"):
        k.start("x = 1")
    with pytest.raises(RuntimeError, match="still running"):
        k.ipywidget_event(model, {"click": True})
    k._widget_thread.join()
    assert k.run("x = 1")["status"] == "ok"
    # Restart is the way out: the new namespace doesn't share the stuck handler's.
    model = _slow_button(k)
    k.ipywidget_event(model, {"click": True})
    k.restart()
    assert k.run("y = 2")["status"] == "ok"


def test_widget_events_wait_for_a_proof_and_a_replay(tmp_path, monkeypatch):
    from fabric_desktop.service import Service
    s = Service(root=tmp_path / "app")
    model = _widget(s.kernel.run("import ipywidgets as w\nb = w.Button()\nb"))["model"]
    monkeypatch.setattr(Service, "proof_busy", lambda self: True)
    with pytest.raises(RuntimeError, match="proof is running"):
        s.notebook_ipywidget_event(model, {"click": True})
    monkeypatch.setattr(Service, "proof_busy", lambda self: False)
    monkeypatch.setattr(Service, "_replay_live", lambda self: {"name": "Sales replay"})
    with pytest.raises(RuntimeError, match="replaying"):
        s.notebook_ipywidget_event(model, {"click": True})
    monkeypatch.setattr(Service, "_replay_live", lambda self: None)
    assert s.notebook_ipywidget_event(model, {"click": True})["status"] == "ok"



def test_an_event_goes_to_its_notebooks_session(tmp_path, monkeypatch):
    from fabric_desktop.service import Service
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local"))
    monkeypatch.setenv("ProgramData", str(tmp_path / "pd"))
    s = Service(root=tmp_path / "app")
    s.save_app_settings(changes={"kernel_process": True})
    try:
        k = s.sessions.kernel("Sales")
        snap = k.start("import ipywidgets as w\nb = w.Button()\nb.on_click(lambda _b: print('sales'))\nb")
        while snap["status"] == "running":
            snap = k.poll(snap["version"], 0.5)
        model = _widget(snap)["model"]
        assert s.notebook_ipywidget_event(model, {"click": True}, notebook="Sales")["outputs"] == [
            {"type": "stream", "text": "sales\n"}]
        # A notebook with no session has no widgets, and none is started for it.
        assert s.notebook_ipywidget_event(model, {"click": True}, notebook="Other") == {"gone": True, "message": WIDGET_GONE}
        assert s.sessions.session("Other", create=False) is None
    finally:
        for kernel in s.sessions.kernels():
            getattr(kernel, "close", lambda: None)()


def test_an_output_widget_is_capped_like_a_cell(monkeypatch):
    from fabric_desktop import notebook
    monkeypatch.setattr(notebook, "STREAM_CHARS", 10)
    monkeypatch.setattr(notebook, "RICH_CHARS", 1_000)
    k = Kernel()
    k.run("import ipywidgets as w\nout = w.Output()\nb = w.Button()\n"
          "def click(_):\n    with out:\n        print('abcdef')\n        displayHTML('x' * 400)\n"
          "b.on_click(click)")
    for _ in range(4):
        k.ipywidget_event(k.namespace["b"].model_id, {"click": True})
    outs = k.namespace["out"].outputs
    assert "".join(o["text"] for o in outs if o["type"] == "stream") == "abcdef\nabc"
    notes = [o["text"] for o in outs if o["type"] == "text"]
    assert len(notes) == 2 and "more than 10 characters" in notes[0]
    assert sum(o["type"] == "html" for o in outs) == 2
    k.namespace["out"].clear_output()
    k.ipywidget_event(k.namespace["b"].model_id, {"click": True})
    assert [o["type"] for o in k.namespace["out"].outputs] == ["stream", "html"]


def test_an_error_in_an_output_widget_shows_past_the_cap(monkeypatch):
    from fabric_desktop import notebook
    monkeypatch.setattr(notebook, "RICH_CHARS", 500)
    out = widgets.Output()
    for _ in range(3):
        out._add({"type": "html", "html": "x" * 300})
    out._add({"type": "error", "ename": "ValueError", "evalue": "boom", "traceback": ""})
    assert [o["type"] for o in out.outputs] == ["html", "text", "error"]
