"""Hover, signature help and syntax checks: a cursor off the text, objects with no signature or broken
docs, strings with escapes, and classes whose first parameter is self. Pure text and inspect, no kernel."""

from __future__ import annotations

from fabric_desktop import intellisense as isn


def test_a_cursor_outside_the_code_is_on_no_name():
    assert isn.dotted_at("df", -1) is None and isn.dotted_at("df", 3) is None


class _NoSignature:
    """Callable, but inspect can't read how."""

    @property
    def __signature__(self):
        raise ValueError("no signature")

    def __call__(self):
        return None


class _BrokenDoc:
    @property
    def __doc__(self):
        raise RuntimeError("docs went away")


def test_an_object_without_a_signature_or_with_broken_docs_still_describes():
    assert isn.signature_text(_NoSignature()) == "(...)"
    assert isn.call_help(_NoSignature(), "f", 0, None) is None
    assert isn._doc(_BrokenDoc()) == ""


def test_open_call_skips_escapes_and_stops_at_a_line_break_in_a_short_string():
    assert isn.open_call('f("a\\"b", ') == ("f", 1, None)
    assert isn.open_call("f('a\n, x") == ("f", 1, None)
    assert isn.open_call("(1, ") is None  # a bracket with no name before it


def test_a_class_drops_self_and_extra_positionals_go_to_star_args():
    def f(a, *rest, **kw):
        """Help."""

    got = isn.call_help(f, "f", 5, None)
    assert got["active"] == 1 and got["params"] == ["a", "*rest", "**kw"] and got["doc"] == "Help."

    class WithSelf:
        __signature__ = None

    import inspect

    def init(self, x):
        return None

    WithSelf.__signature__ = inspect.signature(init)
    got = isn.call_help(WithSelf, "WithSelf", 0, None)
    assert got["params"] == ["x"] and got["active"] == 0


def test_a_syntax_error_with_no_usable_end_points_at_one_character(monkeypatch):
    err = SyntaxError("bad", ("<cell>", 2, 5, "x", 1, 1))

    def refuse(*a, **k):
        raise err

    monkeypatch.setattr(isn, "compile", refuse, raising=False)
    [got] = isn.syntax_problems("a = 1\nb")
    assert (got["line"], got["col"], got["end_line"], got["end_col"]) == (2, 4, 2, 5)
