"""Architecture and layer boundary compliance tests using AST inspection."""

import ast
from pathlib import Path

SRC_ROOT = Path(__file__).resolve().parents[3] / "src" / "iwed_bot"


def _build_parent_map(tree: ast.AST) -> dict[ast.AST, ast.AST]:
    """Membangun pemetaan parent untuk setiap node AST."""
    parent_map: dict[ast.AST, ast.AST] = {}
    for node in ast.walk(tree):
        for child in ast.iter_child_nodes(node):
            parent_map[child] = node
    return parent_map


def _is_inside_type_checking(node: ast.AST, parent_map: dict[ast.AST, ast.AST]) -> bool:
    """Memeriksa apakah node berada di dalam blok `if TYPE_CHECKING:`."""
    curr: ast.AST | None = node
    while curr is not None:
        parent = parent_map.get(curr)
        if isinstance(parent, ast.If):
            test = parent.test
            if isinstance(test, ast.Name) and test.id == "TYPE_CHECKING":
                return True
            if isinstance(test, ast.Attribute) and test.attr == "TYPE_CHECKING":
                return True
        curr = parent
    return False


def _get_runtime_imports(file_path: Path) -> set[str]:
    """Mengekstrak seluruh modul yang diimpor pada runtime (di luar `if TYPE_CHECKING:`)."""
    with open(file_path, encoding="utf-8") as f:
        tree = ast.parse(f.read(), filename=str(file_path))

    parent_map = _build_parent_map(tree)
    imports = set()
    for node in ast.walk(tree):
        if _is_inside_type_checking(node, parent_map):
            continue
        if isinstance(node, ast.Import):
            for alias in node.names:
                imports.add(alias.name)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imports.add(node.module)
    return imports


def test_domain_ports_application_have_no_discord_or_wavelink_imports() -> None:
    """Memastikan layer domain, ports, application bebas dari runtime import discord & wavelink."""
    forbidden = {"discord", "wavelink"}
    layers = [SRC_ROOT / "domain", SRC_ROOT / "ports", SRC_ROOT / "application"]

    for layer in layers:
        for py_file in layer.glob("**/*.py"):
            runtime_imports = _get_runtime_imports(py_file)
            for imp in runtime_imports:
                top_pkg = imp.split(".")[0]
                assert top_pkg not in forbidden, (
                    f"File {py_file.name} mengimpor {imp} yang terlarang di layer {layer.name}!"
                )


def test_application_does_not_import_infrastructure() -> None:
    """Memastikan layer application tidak memiliki dependensi ke layer infrastructure."""
    app_dir = SRC_ROOT / "application"
    for py_file in app_dir.glob("**/*.py"):
        runtime_imports = _get_runtime_imports(py_file)
        for imp in runtime_imports:
            assert not imp.startswith("iwed_bot.infrastructure"), (
                f"Application file {py_file.name} mengimpor infrastructure ({imp})!"
            )


def test_production_code_does_not_use_wavelink_queue_or_player_queue() -> None:
    """Memastikan seluruh source code production tidak pakai wavelink.Queue / player.queue."""
    for py_file in SRC_ROOT.glob("**/*.py"):
        with open(py_file, encoding="utf-8") as f:
            content = f.read()

        assert "wavelink.Queue" not in content, (
            f"File {py_file.name} menggunakan wavelink.Queue yang terlarang!"
        )
        assert "player.queue" not in content, (
            f"File {py_file.name} menggunakan player.queue yang terlarang!"
        )


def test_production_code_does_not_use_blocking_sleep_or_requests() -> None:
    """Memastikan production code tidak menggunakan blocking sleep / HTTP requests."""
    for py_file in SRC_ROOT.glob("**/*.py"):
        runtime_imports = _get_runtime_imports(py_file)
        assert "requests" not in runtime_imports, (
            f"File {py_file.name} mengimpor blocking library requests!"
        )
        assert "time.sleep" not in runtime_imports, f"File {py_file.name} mengimpor time.sleep!"

        with open(py_file, encoding="utf-8") as f:
            content = f.read()
        assert "time.sleep(" not in content, (
            f"File {py_file.name} menggunakan blocking time.sleep()!"
        )
