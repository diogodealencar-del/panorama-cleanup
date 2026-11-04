"""--workdir / PANORAMA_CLEANUP_HOME: saídas de cada Panorama isoladas na própria pasta."""

from panorama_cleanup.cli import EXIT_ERROR, EXIT_OK, WORKDIR_ENV, main
from tests.conftest import FIXTURES, PROJECT_ROOT, tree_snapshot

PROJECT_DIRS = ("output", "cli", "rollback", "logs")


def _snapshot_project():
    return {d: tree_snapshot(PROJECT_ROOT / d) for d in PROJECT_DIRS}


def _backup(tmp_path):
    src = tmp_path / "cliente-backup.xml"
    src.write_bytes((FIXTURES / "panorama_basic.xml").read_bytes())
    return src


def test_inventory_writes_only_inside_workdir(tmp_path):
    workdir = tmp_path / "cliente-a"
    workdir.mkdir()
    before = _snapshot_project()
    assert main(["--workdir", str(workdir), "inventory", "--input", str(_backup(tmp_path))]) == EXIT_OK
    assert _snapshot_project() == before
    runs = list((workdir / "output" / "reports").iterdir())
    assert len(runs) == 1 and (runs[0] / "inventory.csv").is_file()
    assert list((workdir / "logs").iterdir())
    for d in ("input", "cli", "rollback", "output/xml"):
        assert (workdir / d).is_dir()


def test_workdir_from_environment(tmp_path, monkeypatch):
    workdir = tmp_path / "cliente-b"
    workdir.mkdir()
    monkeypatch.setenv(WORKDIR_ENV, str(workdir))
    assert main(["inventory", "--input", str(_backup(tmp_path))]) == EXIT_OK
    assert len(list((workdir / "output" / "reports").iterdir())) == 1


def test_missing_workdir_is_error(tmp_path, capsys):
    code = main(["--workdir", str(tmp_path / "nao-existe"), "inventory",
                 "--input", str(_backup(tmp_path))])
    assert code == EXIT_ERROR
    assert not (tmp_path / "nao-existe").exists()
    assert "--workdir" in capsys.readouterr().err


def test_original_inside_workdir_input_is_untouched(tmp_path):
    workdir = tmp_path / "cliente-c"
    (workdir / "input").mkdir(parents=True)
    backup = workdir / "input" / "backup.xml"
    data = (FIXTURES / "panorama_basic.xml").read_bytes()
    backup.write_bytes(data)
    assert main(["--workdir", str(workdir), "inventory", "--input", str(backup)]) == EXIT_OK
    assert backup.read_bytes() == data
    assert sorted(p.name for p in (workdir / "input").iterdir()) == ["backup.xml"]
