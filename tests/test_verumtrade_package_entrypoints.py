import importlib
import importlib.util
from pathlib import Path

import pytest


def test_verumtrade_package_exposes_public_graph_entrypoint():
    module = importlib.import_module("verumtrade.graph.verumtrade_graph")

    assert hasattr(module, "VerumtradeGraph")


def test_legacy_package_name_is_not_exposed():
    legacy_name = "trading" + "agents"

    assert importlib.util.find_spec(legacy_name) is None


def test_pyproject_uses_verumtrade_package_and_cli_names():
    tomllib = pytest.importorskip("tomllib")  # stdlib from Python 3.11
    pyproject = tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))

    assert pyproject["project"]["name"] == "verumtrade"
    assert pyproject["project"]["scripts"] == {"verumtrade": "cli.main:app"}
    assert "verumtrade*" in pyproject["tool"]["setuptools"]["packages"]["find"]["include"]
    assert "trading" + "agents*" not in pyproject["tool"]["setuptools"]["packages"]["find"]["include"]
