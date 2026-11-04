"""scripts/build_skill.py: skill do plugin e zip do claude.ai a partir de skill/."""

import hashlib
import json
import re
import stat
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

from tests.conftest import FIXTURES, PROJECT_ROOT

sys.path.insert(0, str(PROJECT_ROOT / "scripts"))
import build_skill  # noqa: E402


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    out = tmp_path_factory.mktemp("dist")
    return build_skill.build_zip(out)


@pytest.fixture(scope="module")
def extracted(built, tmp_path_factory):
    """Zip extraído numa pasta somente leitura, como no ambiente do chat."""
    root = tmp_path_factory.mktemp("skills")
    with zipfile.ZipFile(built) as z:
        z.extractall(root)
    skill = root / build_skill.TOP
    for p in sorted(skill.rglob("*"), reverse=True):
        p.chmod(p.stat().st_mode & ~(stat.S_IWUSR | stat.S_IWGRP | stat.S_IWOTH))
    skill.chmod(skill.stat().st_mode & ~stat.S_IWUSR)
    yield skill
    for p in [skill, *skill.rglob("*")]:
        p.chmod(p.stat().st_mode | stat.S_IWUSR)


def _run(skill, *args, cwd):
    return subprocess.run([sys.executable, str(skill / "scripts" / "run.py"), *args],
                          cwd=cwd, capture_output=True, text=True)


def test_plugin_skill_is_up_to_date():
    assert build_skill.main(["--check"]) == 0


def test_versions_agree():
    assert build_skill.version()


def test_both_variants_render_without_placeholders():
    for variant in build_skill.VARIANTS:
        text = build_skill.render(variant, "0.0.0")
        assert "{{" not in text
        front = re.match(r"^---\nname: ([a-z0-9-]+)\ndescription: (.+)\n---\n", text)
        assert front and front.group(1) == "panorama-cleanup"
        assert len(front.group(2)) <= 1024
        assert "Regras invioláveis" in text and "Uma etapa por vez" in text


def test_zip_contains_only_allowed_files(built):
    with zipfile.ZipFile(built) as z:
        names = z.namelist()
    assert all(n.startswith(build_skill.TOP + "/") for n in names)
    rel = [n[len(build_skill.TOP) + 1:] for n in names]
    assert "SKILL.md" in rel and "scripts/run.py" in rel and "MANIFEST.json" in rel
    assert not [n for n in rel if n.endswith((".xml", ".log", ".csv", ".xlsx", ".tsv"))]
    assert not [n for n in rel if re.match(r"(input|output|logs|cli|rollback)/", n)]
    assert all(build_skill.ALLOWED.match(n) for n in rel if n != "MANIFEST.json")


def test_zip_respects_claude_ai_file_limit(built):
    with zipfile.ZipFile(built) as z:
        assert len(z.namelist()) <= build_skill.MAX_FILES


def test_zip_has_no_nested_archives(built):
    with zipfile.ZipFile(built) as z:
        names = z.namelist()
        assert not [n for n in names if n.lower().endswith((".zip", ".whl", ".egg", ".pyz", ".jar"))]
        assert not [n for n in names if z.read(n)[:4] == b"PK\x03\x04"]


def test_defusedxml_loads_from_skill(extracted, tmp_path):
    result = _run(extracted, "--check", cwd=tmp_path)
    assert result.returncode == 0, result.stdout + result.stderr
    assert re.search(r"defusedxml: ok \S+ \[skill\]", result.stdout), result.stdout


def test_zip_is_deterministic(built, tmp_path):
    again = build_skill.build_zip(tmp_path)
    assert again.read_bytes() == built.read_bytes()


def test_manifest_hashes_match(built):
    with zipfile.ZipFile(built) as z:
        manifest = json.loads(z.read(f"{build_skill.TOP}/MANIFEST.json"))
        for name, digest in manifest["files"].items():
            assert hashlib.sha256(z.read(f"{build_skill.TOP}/{name}")).hexdigest() == digest
    assert manifest["version"] == build_skill.version()


def test_check_uses_package_from_skill(extracted, tmp_path):
    result = _run(extracted, "--check", cwd=tmp_path)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "AMBIENTE OK" in result.stdout
    assert re.search(r"panorama_cleanup\.cli: ok \S+ \[skill\]", result.stdout)


def test_workdir_is_required(extracted, tmp_path):
    backup = tmp_path / "backup.xml"
    backup.write_bytes((FIXTURES / "panorama_basic.xml").read_bytes())
    result = _run(extracted, "inventory", "--input", str(backup), cwd=tmp_path)
    assert result.returncode == 1 and "--workdir" in result.stderr


def test_inventory_runs_from_read_only_skill(extracted, tmp_path):
    backup = tmp_path / "uploads" / "backup.xml"
    backup.parent.mkdir()
    data = (FIXTURES / "panorama_basic.xml").read_bytes()
    backup.write_bytes(data)
    work = tmp_path / "outputs" / "panorama-backup"
    work.mkdir(parents=True)
    result = _run(extracted, "--workdir", str(work), "inventory", "--input", str(backup), cwd=tmp_path)
    assert result.returncode == 0, result.stderr
    assert backup.read_bytes() == data
    runs = list((work / "output" / "reports").iterdir())
    assert len(runs) == 1 and (runs[0] / "inventory.xlsx").is_file()
    assert not list(extracted.rglob("__pycache__"))
