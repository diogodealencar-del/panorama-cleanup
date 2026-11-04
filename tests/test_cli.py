import subprocess
import sys

import pytest

from panorama_cleanup.cli import EXIT_ERROR, EXIT_NOT_IMPLEMENTED, IMPLEMENTED, main
from panorama_cleanup.run_context import MODES
from tests.conftest import PROJECT_ROOT, tree_snapshot


def test_all_modes_implemented():
    assert set(IMPLEMENTED) == set(MODES)


def test_missing_input_is_error(project, capsys):
    code = main(["--config", str(project.root / "config" / "settings.json"),
                 "inventory", "--input", str(project.input_dir / "nao-existe.xml")])
    assert code == EXIT_ERROR


def test_invalid_config_is_error(tmp_path, backup_xml):
    bad = tmp_path / "bad.json"
    bad.write_text("{", encoding="utf-8")
    assert main(["--config", str(bad), "inventory", "--input", str(backup_xml)]) == EXIT_ERROR


def test_mode_is_required():
    with pytest.raises(SystemExit) as exc:
        main([])
    assert exc.value.code != 0


def test_input_is_required():
    with pytest.raises(SystemExit):
        main(["inventory"])


def test_runs_as_module():
    result = subprocess.run(
        [sys.executable, "-m", "panorama_cleanup", "--help"],
        cwd=PROJECT_ROOT, capture_output=True, text=True,
    )
    assert result.returncode == 0
    for mode in MODES:
        assert mode in result.stdout


def test_run_id_collision_reported_without_traceback(project, monkeypatch, capsys):
    from datetime import datetime, timezone

    from panorama_cleanup import cli
    from panorama_cleanup.run_context import create_run_context
    from tests.conftest import FIXTURES

    backup_xml = project.input_dir / "basic.xml"
    backup_xml.write_bytes((FIXTURES / "panorama_basic.xml").read_bytes())

    fixed = datetime(2026, 11, 3, 12, 0, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(cli, "create_run_context",
                        lambda mode, path, label="": create_run_context(mode, path, fixed, label))
    args = ["--config", str(project.root / "config" / "settings.json"), "inventory",
            "--input", str(backup_xml)]
    assert main(args) == 0
    assert main(args) == EXIT_ERROR
    assert "já existe" in capsys.readouterr().err
