"""Rich cell outputs: displayHTML, IPython.display without IPython, mime bundles, clear_output."""

import base64
import sys

import pytest

from fabric_desktop import notebook
from fabric_desktop.notebook import Kernel, display

PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg==")


def _outs(code):
    out = Kernel().run(code)
    assert out["status"] == "ok", out
    return out["outputs"]


def test_displayhtml_goes_to_the_sandboxed_html_output():
    assert _outs("displayHTML('<b>hi</b>')") == [{"type": "html", "html": "<b>hi</b>"}]


@pytest.mark.skipif("IPython" in sys.modules and not getattr(sys.modules["IPython"], "__kratosforge__", False),
                    reason="the real IPython is installed")
def test_ipython_display_works_without_ipython():
    outs = _outs("from IPython.display import display, HTML, Markdown, JSON, SVG\n"
                 "display(HTML('<i>x</i>'), Markdown('# Title'), JSON({'a': [1, 2]}))\n"
                 "SVG('<svg xmlns=\"http://www.w3.org/2000/svg\"/>')")
    assert outs[0] == {"type": "html", "html": "<i>x</i>"}
    assert outs[1] == {"type": "markdown", "text": "# Title"}
    assert outs[2] == {"type": "json", "data": {"a": [1, 2]}}
    assert outs[3]["mime"] == "image/svg+xml"


def test_image_from_a_file_and_a_url(tmp_path):
    from fabric_desktop import ipython_display as ipd
    (tmp_path / "p.png").write_bytes(PNG)
    out = display(ipd.Image(filename=str(tmp_path / "p.png")))
    assert out == {"type": "image", "mime": "image/png", "data": base64.b64encode(PNG).decode()}
    jpeg = display(ipd.Image(b"\xff\xd8\xff\xe0rest"))
    assert jpeg["mime"] == "image/jpeg"
    assert display(ipd.Image(url="https://example.com/x.png")) == {"type": "text", "text": "Image: https://example.com/x.png"}
    assert display(ipd.IFrame("https://example.com")) == {"type": "text", "text": "IFrame: https://example.com"}


def test_mime_bundles_pick_the_richest_view():
    class Chart:  # what Altair and other libraries return
        def _repr_mimebundle_(self, include=None, exclude=None):
            return {"text/plain": "chart", "image/png": "aGk=\n"}, {}

    class Broken:
        def _repr_html_(self):
            raise ValueError("no")

        def _repr_markdown_(self):
            return "**ok**"

    assert display(Chart()) == {"type": "image", "mime": "image/png", "data": "aGk="}
    assert display(Broken()) == {"type": "markdown", "text": "**ok**"}
    assert display(3) == {"type": "text", "text": "3"}


def test_clear_output_clears_now_or_on_the_next_output():
    assert _outs("from IPython.display import clear_output\nprint('a')\nclear_output()\nprint('b')") == \
        [{"type": "stream", "text": "b\n"}]
    assert _outs("from IPython.display import clear_output\nprint('a')\nclear_output(wait=True)\ndisplayHTML('x')") == \
        [{"type": "html", "html": "x"}]


def test_displayhtml_is_not_a_user_variable():
    assert "displayHTML" in Kernel._base_namespace_keys()
