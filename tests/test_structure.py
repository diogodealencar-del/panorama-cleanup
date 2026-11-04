import importlib

import pytest

from tests.conftest import PROJECT_ROOT

REQUIRED_DIRS = [
    "cli", "config", "docs", "input", "logs", "output/reports",
    "rollback", "scripts", "tests/fixtures/xml", "panorama_cleanup",
]
REQUIRED_FILES = [
    "CLAUDE.md", "README.md", "requirements.txt", "pyproject.toml",
    ".gitignore", "config/settings.json", "scripts/check_env.py",
    "docs/arquitetura.md", "docs/modelo-etapa-09.md", "docs/procedimento-uso-da-ferramenta.md",
]
MODULES = [
    "inventory", "dependency_analyzer", "duplicate_analyzer", "unused_objects",
    "shared_converter", "xml_validator", "policy_comparator", "generate_cli",
    "report_generator", "cli", "config", "safety", "run_context",
    "logging_setup", "environment", "errors", "xml_reader",
]


@pytest.mark.parametrize("rel", REQUIRED_DIRS)
def test_required_dir_exists(rel):
    assert (PROJECT_ROOT / rel).is_dir()


@pytest.mark.parametrize("rel", REQUIRED_FILES)
def test_required_file_exists(rel):
    assert (PROJECT_ROOT / rel).is_file()


@pytest.mark.parametrize("name", MODULES)
def test_module_imports(name):
    importlib.import_module(f"panorama_cleanup.{name}")


def test_gitignore_excludes_original_backups():
    text = (PROJECT_ROOT / ".gitignore").read_text(encoding="utf-8")
    assert "input/*" in text
