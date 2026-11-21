"""Where a notebook's imports stand (envprofile.py): the package-metadata edges the main tests don't reach.
The metadata calls are stood in for, so nothing about the real engine's packages is assumed."""

from __future__ import annotations

from types import SimpleNamespace

from fabric_desktop import envprofile


class Missing(Exception):
    pass


def metadata(packages, versions, broken=False):
    def packages_distributions():
        if broken:
            raise RuntimeError("a package's metadata is broken")
        return packages

    def version(name):
        if name not in versions:
            raise Missing(name)
        return versions[name]

    return SimpleNamespace(packages_distributions=packages_distributions, version=version, PackageNotFoundError=Missing)


def test_a_module_is_matched_to_the_first_distribution_that_is_installed(monkeypatch):
    monkeypatch.setattr(envprofile, "md", metadata({"yaml": ["gone-yaml", "PyYAML"]}, {"PyYAML": "6.0.2"}))
    assert envprofile._distribution("yaml") == ("PyYAML", "6.0.2")


def test_broken_metadata_falls_back_to_the_module_name(monkeypatch):
    monkeypatch.setattr(envprofile, "md", metadata({}, {"requests": "2.32.0"}, broken=True))
    assert envprofile._distribution("requests") == ("requests", "2.32.0")


def test_a_module_no_distribution_provides_has_no_version(monkeypatch):
    monkeypatch.setattr(envprofile, "md", metadata({"mine": ["also-gone"]}, {}))
    assert envprofile._distribution("mine") == (None, None)


def test_spark_is_provided_on_this_pc_and_in_fabric():
    assert envprofile.on_this_pc("pyspark") == {"status": "provided", "text": "On this PC, spark is DuckDB's PySpark API"}
    assert envprofile.in_fabric("pyspark")["status"] == "provided"
    assert "Spark" in envprofile.in_fabric("pyspark")["text"]


def test_a_name_import_machinery_refuses_reads_as_missing(monkeypatch):
    def refuse(name):
        raise ValueError(f"{name}.__spec__ is None")

    monkeypatch.setattr(envprofile.importlib.util, "find_spec", refuse)
    assert envprofile.on_this_pc("halfloaded")["status"] == "missing"


def test_an_install_line_below_blank_lines_is_reported_on_its_own_line():
    src = "import os\n\n\n%pip install foo\n  \n\t!pip install bar\n%conda install baz\n"
    assert envprofile.imports(src)["dynamic"] == [4, 6, 7]


def test_imports_keep_the_first_line_and_optional_only_where_every_import_is_optional():
    source = "\n".join([
        "def f():",
        "    import a.x",
        "    lambda: __import__('z')",
        "try:",
        "    import b, c",
        "    import a",
        "except ImportError:",
        "    import d",
        "finally:",
        "    from e import y",
        "class K:",
        "    import c",
        "from .rel import r",
        "import f as g, b",
    ])
    out = envprofile.imports(source)
    assert out["parsed"] and out["modules"] == [
        {"module": "a", "line": 2, "optional": True}, {"module": "b", "line": 5, "optional": False},
        {"module": "c", "line": 5, "optional": False}, {"module": "d", "line": 8, "optional": False},
        {"module": "e", "line": 10, "optional": False}, {"module": "f", "line": 14, "optional": False}]


def test_imports_read_a_cell_with_an_expression_thousands_of_terms_long():
    """A long sum or string concatenation is a tree thousands deep; it used to raise RecursionError."""
    source = "import os\nx = " + " + ".join(["1"] * 5000) + "\ntry:\n    import yaml\nexcept ImportError:\n    pass\n"
    out = envprofile.imports(source)
    assert out["parsed"] and out["modules"] == [{"module": "os", "line": 1, "optional": False},
                                                {"module": "yaml", "line": 4, "optional": True}]
