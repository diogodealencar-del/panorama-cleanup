import json
from pathlib import Path

import pytest

from panorama_cleanup.config import load_settings

PROJECT_ROOT = Path(__file__).resolve().parent.parent
FIXTURES = PROJECT_ROOT / "tests" / "fixtures" / "xml"

SETTINGS = {
    "paths": {
        "input": "input",
        "reports": "output/reports",
        "xml_output": "output/xml",
        "cli": "cli",
        "rollback": "rollback",
        "logs": "logs",
    },
    "logging": {"level": "INFO"},
}


@pytest.fixture
def project(tmp_path):
    """Projeto isolado em tmp_path, para nenhum teste tocar nos diretórios reais."""
    (tmp_path / "config").mkdir()
    for rel in SETTINGS["paths"].values():
        (tmp_path / rel).mkdir(parents=True)
    settings_file = tmp_path / "config" / "settings.json"
    settings_file.write_text(json.dumps(SETTINGS), encoding="utf-8")
    return load_settings(settings_file)


@pytest.fixture
def backup_xml(project):
    path = project.input_dir / "backup.xml"
    path.write_bytes(b'<config version="11.1.0"><shared/></config>\n')
    return path


def tree_snapshot(root: Path) -> dict:
    return {p: p.read_bytes() for p in sorted(root.rglob("*")) if p.is_file()}
