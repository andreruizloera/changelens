from __future__ import annotations

from pathlib import Path

from changelens.graph import build_graph
from tests.conftest import write_tree


def test_absolute_import_edge(tmp_path: Path) -> None:
    write_tree(
        tmp_path,
        {
            "pkg/__init__.py": "",
            "pkg/core.py": "x = 1\n",
            "app.py": "import pkg.core\n",
        },
    )
    graph = build_graph(tmp_path)
    assert "pkg.core" in graph.imports["app"]
    assert "app" in graph.dependents_of("pkg.core")


def test_from_import_symbol_vs_submodule(tmp_path: Path) -> None:
    write_tree(
        tmp_path,
        {
            "pkg/__init__.py": "",
            "pkg/core.py": "def run():\n    pass\n",
            "uses_symbol.py": "from pkg.core import run\n",
            "uses_module.py": "from pkg import core\n",
        },
    )
    graph = build_graph(tmp_path)
    assert "pkg.core" in graph.imports["uses_symbol"]
    assert ("pkg.core", "run") in graph.symbol_imports["uses_symbol"]
    # `from pkg import core` is a module import, not a symbol import.
    assert "pkg.core" in graph.imports["uses_module"]
    assert not graph.symbol_imports["uses_module"]


def test_relative_imports_resolved(tmp_path: Path) -> None:
    write_tree(
        tmp_path,
        {
            "pkg/__init__.py": "",
            "pkg/core.py": "x = 1\n",
            "pkg/sibling.py": "from . import core\nfrom .core import x\n",
            "pkg/sub/__init__.py": "from ..core import x\n",
            "pkg/sub/deep.py": "from .. import core\n",
        },
    )
    graph = build_graph(tmp_path)
    assert "pkg.core" in graph.imports["pkg.sibling"]
    assert "pkg.core" in graph.imports["pkg.sub"]
    assert "pkg.core" in graph.imports["pkg.sub.deep"]


def test_package_init_import_edge(tmp_path: Path) -> None:
    write_tree(
        tmp_path,
        {
            "pkg/__init__.py": "from pkg.core import run\n",
            "pkg/core.py": "def run():\n    pass\n",
            "app.py": "import pkg\n",
        },
    )
    graph = build_graph(tmp_path)
    assert "pkg" in graph.imports["app"]
    assert "pkg.core" in graph.imports["pkg"]


def test_src_layout_module_names(tmp_path: Path) -> None:
    write_tree(
        tmp_path,
        {
            "src/mylib/__init__.py": "",
            "src/mylib/core.py": "x = 1\n",
            "tests/test_core.py": "from mylib.core import x\n",
        },
    )
    graph = build_graph(tmp_path)
    assert graph.module_for_path("src/mylib/core.py") == "mylib.core"
    assert "mylib.core" in graph.imports["tests.test_core"]


def test_star_import_recorded(tmp_path: Path) -> None:
    write_tree(
        tmp_path,
        {
            "pkg/__init__.py": "",
            "pkg/core.py": "x = 1\n",
            "app.py": "from pkg.core import *\n",
        },
    )
    graph = build_graph(tmp_path)
    assert "app" in graph.star_importers_of("pkg.core")
    assert "pkg.core" in graph.imports["app"]  # still an edge


def test_dynamic_import_flagged(tmp_path: Path) -> None:
    write_tree(
        tmp_path,
        {"loader.py": "import importlib\n\nmod = importlib.import_module('pkg.core')\n"},
    )
    graph = build_graph(tmp_path)
    assert "loader" in graph.dynamic_importers()


def test_unparseable_file_recorded(tmp_path: Path) -> None:
    write_tree(tmp_path, {"bad.py": "def broken(:\n"})
    graph = build_graph(tmp_path)
    assert "bad.py" in graph.unparseable


def test_call_resolution_through_import_bindings(tmp_path: Path) -> None:
    write_tree(
        tmp_path,
        {
            "pkg/__init__.py": "",
            "pkg/core.py": "def run():\n    pass\n\n\nclass Tool:\n    pass\n",
            "a.py": "from pkg.core import run\n\nrun()\n",
            "b.py": "from pkg import core\n\ncore.run()\n",
            "c.py": "import pkg.core\n\npkg.core.Tool()\n",
        },
    )
    graph = build_graph(tmp_path)
    assert ("pkg.core", "run") in graph.calls["a"]
    assert ("pkg.core", "run") in graph.calls["b"]
    assert ("pkg.core", "Tool") in graph.calls["c"]


def test_external_imports_ignored(tmp_path: Path) -> None:
    write_tree(tmp_path, {"app.py": "import os\nfrom json import dumps\n"})
    graph = build_graph(tmp_path)
    assert graph.imports["app"] == set()


def test_raw_dependents_survive_deletion(tmp_path: Path) -> None:
    # `gone` does not exist on disk, but app.py still imports it.
    write_tree(tmp_path, {"app.py": "from gone import thing\n"})
    graph = build_graph(tmp_path)
    assert "app" in graph.raw_dependents_of("gone")
