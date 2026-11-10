import json

import pytest

from panorama_cleanup.cli import EXIT_ERROR, EXIT_OK, main
from tests.conftest import FIXTURES, tree_snapshot

PLAN_REPORTS = {
    "plan-objects.csv", "plan-operations.csv", "renamed-objects.csv", "reference-updates.csv",
    "deleted-objects.csv", "kept-local-objects.csv", "plan-verification.csv",
    "plan-demotions.csv", "plan.json", "plan.xlsx", "plan.html", "manifest.json",
}


@pytest.fixture
def setup(project):
    backup = project.input_dir / "deps.xml"
    backup.write_bytes((FIXTURES / "panorama_deps.xml").read_bytes())
    config = str(project.root / "config" / "settings.json")
    assert main(["--config", config, "inventory", "--input", str(backup)]) == EXIT_OK
    (inventory,) = list(project.reports_dir.iterdir())
    assert main(["--config", config, "analyze", "--input", str(backup),
                 "--inventory", str(inventory)]) == EXIT_OK
    (analysis,) = [p for p in project.reports_dir.iterdir() if p != inventory]
    return project, backup, config, analysis


def run_plan(config, backup, analysis):
    return main(["--config", config, "plan", "--input", str(backup), "--analysis", str(analysis)])


def test_plan_generates_reports_without_touching_input(setup):
    project, backup, config, analysis = setup
    before = tree_snapshot(project.input_dir)
    existing = set(project.reports_dir.iterdir())
    assert run_plan(config, backup, analysis) == EXIT_OK
    (out,) = set(project.reports_dir.iterdir()) - existing
    assert {p.name for p in out.iterdir()} == PLAN_REPORTS
    assert tree_snapshot(project.input_dir) == before
    assert not list(project.root.rglob("output/xml/*"))
    data = json.loads((out / "plan.json").read_text())
    assert data["summary"]["verification_differences"] == 0
    assert data["summary"]["rename_move"] == 3  # SHADOW, OVR-MEMBER e a cópia da fase 0
    m = json.loads((out / "manifest.json").read_text())
    assert m["status"] == "ok" and m["analysis_run"] == analysis.name


def test_plan_refuses_other_file(setup):
    project, backup, config, analysis = setup
    other = project.input_dir / "other.xml"
    other.write_bytes(backup.read_bytes() + b"\n")
    assert run_plan(config, other, analysis) == EXIT_ERROR


def test_plan_refuses_tampered_analysis(setup):
    _, backup, config, analysis = setup
    path = analysis / "object-classification.csv"
    path.write_text(path.read_text().replace("UNUSED", "USED", 1))
    assert run_plan(config, backup, analysis) == EXIT_ERROR


def test_plan_requires_analysis_argument(setup):
    _, backup, config, _ = setup
    assert main(["--config", config, "plan", "--input", str(backup)]) == EXIT_ERROR
