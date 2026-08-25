"""Architectural integrity tests to verify domain layer purity."""

import ast
from pathlib import Path


def test_domain_layer_has_zero_external_dependencies() -> None:
    domain_dir = (
        Path(__file__).resolve().parent.parent.parent.parent / "src" / "nadira_bot" / "domain"
    )
    assert domain_dir.exists()
    assert domain_dir.is_dir()

    forbidden_modules = {
        "discord",
        "wavelink",
        "redis",
        "aiohttp",
        "requests",
        "pydantic",
        "pydantic_settings",
        "docker",
    }

    for py_file in domain_dir.glob("*.py"):
        tree = ast.parse(py_file.read_text(encoding="utf-8"), filename=str(py_file))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    root_mod = alias.name.split(".")[0]
                    assert root_mod not in forbidden_modules, (
                        f"Domain file {py_file.name} imports forbidden module '{root_mod}'"
                    )
            elif isinstance(node, ast.ImportFrom) and node.module:
                root_mod = node.module.split(".")[0]
                assert root_mod not in forbidden_modules, (
                    f"Domain file {py_file.name} imports from forbidden module '{root_mod}'"
                )
