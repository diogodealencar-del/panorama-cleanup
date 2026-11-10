import json

import pytest

from panorama_cleanup.cli import EXIT_ERROR, EXIT_OK, main
from panorama_cleanup.run_context import sha256_file
from panorama_cleanup.xml_reader import parse_backup
from tests.conftest import FIXTURES, tree_snapshot

APPLY_REPORTS = {
    "applied-changes.csv", "created-objects.csv", "renamed-objects.csv", "reference-updates.csv",
    "deleted-objects.csv", "policy-comparison.csv", "integrity-checks.csv", "apply.html",
    "manifest.json",
}


@pytest.fixture
def setup(project):
    backup = project.input_dir / "deps.xml"
    backup.write_bytes((FIXTURES / "panorama_deps.xml").read_bytes())
    config = str(project.root / "config" / "settings.json")
    seen = set()

    def newest():
        (d,) = set(project.reports_dir.iterdir()) - seen
        seen.add(d)
        return d

    assert main(["--config", config, "inventory", "--input", str(backup)]) == EXIT_OK
    inventory = newest()
    assert main(["--config", config, "analyze", "--input", str(backup), "--inventory", str(inventory)]) == EXIT_OK
    analysis = newest()
    assert main(["--config", config, "plan", "--input", str(backup), "--analysis", str(analysis)]) == EXIT_OK
    plan = newest()
    return project, backup, config, plan, newest


def run_apply(config, backup, plan):
    return main(["--config", config, "apply", "--input", str(backup), "--plan", str(plan)])


def test_apply_writes_new_xml_and_keeps_original(setup):
    project, backup, config, plan, newest = setup
    digest = sha256_file(backup)
    before = tree_snapshot(project.input_dir)
    assert run_apply(config, backup, plan) == EXIT_OK
    out = newest()
    assert {p.name for p in out.iterdir()} == APPLY_REPORTS
    assert tree_snapshot(project.input_dir) == before and sha256_file(backup) == digest
    m = json.loads((out / "manifest.json").read_text())
    assert m["status"] == "ok" and m["phase_4_executed"] is False
    assert m["checks_passed"] == m["checks_total"]
    xml = m["output_xml"]["path"]
    assert xml.startswith(str(project.xml_output_dir)) and sha256_file(xml) == m["output_xml"]["sha256"]
    tree = parse_backup(xml)
    assert tree.xpath("/config/shared/address/entry[@name='SHADOW-10.0.0.99']")


def test_apply_refuses_tampered_plan(setup):
    _, backup, config, plan, _ = setup
    path = plan / "plan.json"
    data = json.loads(path.read_text())
    data["operations"][0]["new_name"] = "OUTRO"
    path.write_text(json.dumps(data))
    assert run_apply(config, backup, plan) == EXIT_ERROR


def test_apply_refuses_other_backup(setup):
    project, backup, config, plan, _ = setup
    other = project.input_dir / "other.xml"
    other.write_bytes(backup.read_bytes().replace(b"10.0.0.99", b"10.0.0.98"))
    assert run_apply(config, other, plan) == EXIT_ERROR


def test_apply_never_writes_into_input(setup):
    project, backup, config, plan, _ = setup
    run_apply(config, backup, plan)
    assert sorted(p.name for p in project.input_dir.iterdir()) == ["deps.xml"]


CLEANUP_REPORTS = {
    "unused-review.csv", "deleted-objects.csv", "kept-unused-objects.csv", "applied-changes.csv",
    "policy-comparison.csv", "integrity-checks.csv", "cleanup.html", "manifest.json",
}


def run_cleanup(config, backup, plan, convert_run):
    return main(["--config", config, "apply", "--stage", "cleanup", "--input", str(backup),
                 "--plan", str(plan), "--from-run", str(convert_run)])


def test_cleanup_generates_final_xml(setup):
    project, backup, config, plan, newest = setup
    assert run_apply(config, backup, plan) == EXIT_OK
    convert_run = newest()
    converted = json.loads((convert_run / "manifest.json").read_text())["output_xml"]
    assert run_cleanup(config, backup, plan, convert_run) == EXIT_OK
    out = newest()
    assert {p.name for p in out.iterdir()} == CLEANUP_REPORTS
    m = json.loads((out / "manifest.json").read_text())
    assert m["status"] == "ok" and m["deleted"] == 18 and m["kept"] == 0
    assert m["checks_passed"] == m["checks_total"]
    assert sha256_file(converted["path"]) == converted["sha256"]
    final = parse_backup(m["output_xml"]["path"])
    assert not final.xpath("/config/shared/address/entry[@name='ORPHAN']")


def test_cleanup_requires_from_run(setup):
    _, backup, config, plan, _ = setup
    assert main(["--config", config, "apply", "--stage", "cleanup", "--input", str(backup),
                 "--plan", str(plan)]) == EXIT_ERROR


def test_cleanup_refuses_tampered_converted_xml(setup):
    _, backup, config, plan, newest = setup
    assert run_apply(config, backup, plan) == EXIT_OK
    convert_run = newest()
    path = json.loads((convert_run / "manifest.json").read_text())["output_xml"]["path"]
    with open(path, "ab") as fh:
        fh.write(b"\n")
    assert run_cleanup(config, backup, plan, convert_run) == EXIT_ERROR


def test_cleanup_refuses_plan_run_as_from_run(setup):
    _, backup, config, plan, _ = setup
    assert run_cleanup(config, backup, plan, plan) == EXIT_ERROR


def run_validate(config, backup, modified, plan, convert_run, cleanup_run):
    return main(["--config", config, "validate", "--input", str(backup), "--modified", str(modified),
                 "--plan", str(plan), "--convert-run", str(convert_run),
                 "--cleanup-run", str(cleanup_run)])


@pytest.fixture
def full_chain(setup):
    project, backup, config, plan, newest = setup
    assert run_apply(config, backup, plan) == EXIT_OK
    convert_run = newest()
    assert main(["--config", config, "apply", "--stage", "cleanup", "--keep-type", "tag",
                 "--input", str(backup), "--plan", str(plan), "--from-run", str(convert_run)]) == EXIT_OK
    cleanup_run = newest()
    final = json.loads((cleanup_run / "manifest.json").read_text())["output_xml"]["path"]
    return project, backup, config, plan, convert_run, cleanup_run, final, newest


def test_validate_approves_final(full_chain):
    project, backup, config, plan, convert_run, cleanup_run, final, newest = full_chain
    assert run_validate(config, backup, final, plan, convert_run, cleanup_run) == EXIT_OK
    out = newest()
    m = json.loads((out / "manifest.json").read_text())
    assert m["result"] == "APROVADO" and m["checks_passed"] == m["checks_total"]
    assert (out / "validation-report.html").is_file()


def test_validate_refuses_other_xml(full_chain, tmp_path):
    project, backup, config, plan, convert_run, cleanup_run, final, _ = full_chain
    other = project.reports_dir / "x.xml"
    other.write_bytes(open(final, "rb").read().replace(b"10.0.0.53", b"10.0.0.54"))
    assert run_validate(config, backup, other, plan, convert_run, cleanup_run) == EXIT_ERROR
