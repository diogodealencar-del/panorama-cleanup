"""Portabilidade (Windows/Linux): arquivos de comando em LF, Etapa 7 sem bash, console."""

import csv
import io
import sys

import pytest

from panorama_cleanup import cli
from panorama_cleanup.cli import EXIT_OK, main
from tests.conftest import FIXTURES, PROJECT_ROOT

GUIDE = PROJECT_ROOT / "docs" / "referencias" / "pan-os-11.1" / "ngfw-cli.md"
needs_guide = pytest.mark.skipif(not GUIDE.is_file(), reason="guia PAN-OS 11.1 ausente em docs/referencias")


def _latest(work, kind):
    runs = sorted(p for p in (work / "output" / "reports").iterdir() if p.name.split("-", 1)[1].startswith(kind + "-"))
    return runs[-1]


def _pipeline(tmp_path):
    """Etapas 2 a 6 sobre a fixture, numa pasta de trabalho isolada."""
    work = tmp_path / "work"
    work.mkdir()
    backup = tmp_path / "backup.xml"
    backup.write_bytes((FIXTURES / "panorama_deps.xml").read_bytes())
    w = ["--workdir", str(work)]
    assert main(w + ["inventory", "--input", str(backup)]) == EXIT_OK
    assert main(w + ["analyze", "--input", str(backup), "--inventory", str(_latest(work, "inventory"))]) == EXIT_OK
    assert main(w + ["plan", "--input", str(backup), "--analysis", str(_latest(work, "analyze"))]) == EXIT_OK
    plan = _latest(work, "plan")
    assert main(w + ["apply", "--input", str(backup), "--plan", str(plan)]) == EXIT_OK
    convert = _latest(work, "apply")
    assert main(w + ["apply", "--stage", "cleanup", "--keep-type", "tag", "--input", str(backup),
                     "--plan", str(plan), "--from-run", str(convert)]) == EXIT_OK
    cleanup = _latest(work, "apply-cleanup")
    return work, w, backup, plan, convert, cleanup


def _commands(work, w, backup, plan, convert, cleanup):
    return main(w + ["plan", "--stage", "commands", "--input", str(backup), "--plan", str(plan),
                     "--convert-run", str(convert), "--cleanup-run", str(cleanup)])


@needs_guide
def test_command_files_use_lf_and_missing_bash_is_pending(tmp_path, monkeypatch):
    work, *rest = _pipeline(tmp_path)
    import shutil
    monkeypatch.setattr(shutil, "which", lambda name, *a, **k: None if name == "bash" else "/x")
    assert _commands(work, *rest) == EXIT_OK

    cli_dir = sorted((work / "cli").iterdir())[-1]
    for path in cli_dir.iterdir():
        if path.suffix in (".sh", ".cli", ".tsv", ".md"):
            assert b"\r" not in path.read_bytes(), path.name

    checks = list(csv.reader(open(_latest(work, "plan-commands") / "generation-checks.csv",
                                  encoding="utf-8", newline="")))
    bash_row = next(r for r in checks if r[0].startswith("run-xmlapi.sh: sintaxe bash"))
    assert bash_row[1] == "PENDENTE" and "Git Bash ou WSL" in bash_row[2]


def test_console_does_not_break_on_legacy_codepage(monkeypatch):
    stream = io.TextIOWrapper(io.BytesIO(), encoding="cp1252")
    monkeypatch.setattr(sys, "stdout", stream)
    cli._safe_console()
    print("Conferência → OK ✓ ação")
    stream.flush()
    assert b"Confer\xeancia" in stream.buffer.getvalue()


def test_command_writers_never_use_platform_newlines():
    """No macOS/Linux o teste acima passaria mesmo com write_text; esta guarda impede a regressão.

    Path.write_text/open sem newline= gravam CRLF no Windows. Em generate_cli, todo texto vai
    por _write_lf (LF) ou por open(..., newline="") (CSV/TSV, quebra controlada pelo código).
    """
    import re
    source = (PROJECT_ROOT / "panorama_cleanup" / "generate_cli.py").read_text(encoding="utf-8")
    assert ".write_text(" not in source
    for call in re.findall(r"open\([^)]*\"w\"[^)]*\)", source):
        assert "newline=" in call, call
