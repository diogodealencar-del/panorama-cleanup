import hashlib
import json
import re
from datetime import datetime, timedelta, timezone

import pytest

from panorama_cleanup.errors import SafetyViolation
from panorama_cleanup.run_context import (
    create_run_context, new_run_id, sha256_file, write_manifest,
)
from tests.conftest import tree_snapshot

NOW = datetime(2026, 11, 3, 16, 30, 0, tzinfo=timezone.utc)


def test_sha256_matches_hashlib(backup_xml):
    assert sha256_file(backup_xml) == hashlib.sha256(backup_xml.read_bytes()).hexdigest()


def test_run_id_format_and_determinism():
    run_id = new_run_id("inventory", "ab" * 32, NOW)
    assert run_id == "20261103T163000Z-inventory-abababab"
    assert new_run_id("inventory", "ab" * 32, NOW) == run_id


def test_run_id_normalizes_to_utc():
    brt = timezone(timedelta(hours=-3))
    assert new_run_id("plan", "0" * 64, NOW.astimezone(brt)).startswith("20261103T163000Z")


def test_run_id_rejects_unknown_mode():
    with pytest.raises(ValueError):
        new_run_id("cleanup", "0" * 64, NOW)


def test_manifest_traces_input(project, backup_xml):
    ctx = create_run_context("inventory", backup_xml, NOW)
    before = tree_snapshot(project.input_dir)
    path = write_manifest(ctx, project)

    assert path.parent == project.reports_dir / ctx.run_id
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["input"]["sha256"] == sha256_file(backup_xml)
    assert data["input"]["size_bytes"] == backup_xml.stat().st_size
    assert re.fullmatch(r"\d{8}T\d{6}Z-inventory-[0-9a-f]{8}", data["run_id"])
    assert tree_snapshot(project.input_dir) == before


def test_manifest_is_never_overwritten(project, backup_xml):
    ctx = create_run_context("inventory", backup_xml, NOW)
    write_manifest(ctx, project)
    with pytest.raises(SafetyViolation, match="já existe"):
        write_manifest(ctx, project)


def test_run_id_with_label():
    assert new_run_id("apply", "ab" * 32, NOW, "cleanup") == "20261103T163000Z-apply-cleanup-abababab"
