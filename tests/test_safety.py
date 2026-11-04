import pytest

from panorama_cleanup.errors import SafetyViolation
from panorama_cleanup.run_context import sha256_file
from panorama_cleanup.safety import ensure_writable_target, verify_unchanged


def test_allows_target_in_writable_dir(project, backup_xml):
    target = project.reports_dir / "run" / "x.csv"
    assert ensure_writable_target(target, project, backup_xml) == target.resolve()


def test_refuses_input_file(project, backup_xml):
    with pytest.raises(SafetyViolation, match="arquivo de entrada"):
        ensure_writable_target(backup_xml, project, backup_xml)


def test_refuses_anything_in_input_dir(project):
    with pytest.raises(SafetyViolation, match="diretório de entrada"):
        ensure_writable_target(project.input_dir / "novo.xml", project)


def test_refuses_traversal_into_input(project):
    sneaky = project.reports_dir / ".." / ".." / "input" / "x.xml"
    with pytest.raises(SafetyViolation, match="diretório de entrada"):
        ensure_writable_target(sneaky, project)


def test_refuses_symlink_into_input(project, backup_xml):
    link = project.reports_dir / "link.xml"
    link.symlink_to(backup_xml)
    with pytest.raises(SafetyViolation):
        ensure_writable_target(link, project, backup_xml, allow_overwrite=True)


def test_refuses_outside_writable_dirs(project):
    with pytest.raises(SafetyViolation, match="fora dos diretórios"):
        ensure_writable_target(project.root / "config" / "x.json", project)


def test_refuses_overwrite(project):
    existing = project.rollback_dir / "rollback.xml"
    existing.write_text("x", encoding="utf-8")
    with pytest.raises(SafetyViolation, match="já existe"):
        ensure_writable_target(existing, project)


def test_verify_unchanged(backup_xml):
    digest = sha256_file(backup_xml)
    verify_unchanged(backup_xml, digest)
    backup_xml.write_bytes(backup_xml.read_bytes() + b" ")
    with pytest.raises(SafetyViolation, match="alterado"):
        verify_unchanged(backup_xml, digest)
