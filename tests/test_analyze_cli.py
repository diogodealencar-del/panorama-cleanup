import csv
import json

import pytest

from panorama_cleanup.cli import EXIT_ERROR, EXIT_OK, main
from tests.conftest import FIXTURES, tree_snapshot

ANALYSIS_REPORTS = {
    "object-classification.csv", "used-objects.csv", "unused-objects.csv",
    "unknown-objects.csv", "context-dependent-objects.csv", "duplicate-objects.csv", "conflicting-objects.csv",
    "possible-duplicates.csv", "dependencies.csv", "unresolved-references.csv",
    "analysis-warnings.csv", "analysis.xlsx", "analysis.html", "manifest.json",
}


@pytest.fixture
def setup(project):
    backup = project.input_dir / "deps.xml"
    backup.write_bytes((FIXTURES / "panorama_deps.xml").read_bytes())
    config = str(project.root / "config" / "settings.json")
    assert main(["--config", config, "inventory", "--input", str(backup)]) == EXIT_OK
    (inventory_dir,) = list(project.reports_dir.iterdir())
    return project, backup, config, inventory_dir


def analyze(config, backup, inventory_dir):
    return main(["--config", config, "analyze", "--input", str(backup),
                 "--inventory", str(inventory_dir)])


def analysis_dir(project, inventory_dir):
    (d,) = [p for p in project.reports_dir.iterdir() if p != inventory_dir]
    return d


def test_analyze_generates_reports_without_touching_input(setup):
    project, backup, config, inventory_dir = setup
    before = tree_snapshot(project.input_dir)
    assert analyze(config, backup, inventory_dir) == EXIT_OK
    out = analysis_dir(project, inventory_dir)
    assert {p.name for p in out.iterdir()} == ANALYSIS_REPORTS
    assert tree_snapshot(project.input_dir) == before
    assert not list(project.root.rglob("output/xml/*"))


def test_manifest_links_inventory_run(setup):
    project, backup, config, inventory_dir = setup
    analyze(config, backup, inventory_dir)
    m = json.loads((analysis_dir(project, inventory_dir) / "manifest.json").read_text())
    assert m["status"] == "ok"
    assert m["inventory_run"] == inventory_dir.name
    assert m["summary"]["objects"] == 55
    assert m["summary"]["unknown"] == 5
    assert m["summary"]["context_dependent"] == 1


def test_unused_report_contents(setup):
    project, backup, config, inventory_dir = setup
    analyze(config, backup, inventory_dir)
    with open(analysis_dir(project, inventory_dir) / "unused-objects.csv", newline="") as fh:
        rows = list(csv.DictReader(fh))
    assert {r["usage"] for r in rows} == {"UNUSED"}
    orphan = next(r for r in rows if r["name"] == "IN-UNUSED-GRP")
    assert orphan["member_of"] == "shared:address-group:GRP-UNUSED"


def test_dependencies_aggregate_contexts(setup):
    project, backup, config, inventory_dir = setup
    analyze(config, backup, inventory_dir)
    with open(analysis_dir(project, inventory_dir) / "dependencies.csv", newline="") as fh:
        rows = list(csv.DictReader(fh))
    dg_bh = next(r for r in rows if r["source_name"] == "SH-R1" and r["target_scope"] == "DG-BH")
    assert dg_bh["contexts"] == "DG-BH; DG-BH-FILIAL"


def test_inventory_from_other_file_refused(setup, project):
    _, backup, config, inventory_dir = setup
    other = project.input_dir / "other.xml"
    other.write_bytes(backup.read_bytes() + b"\n")
    assert analyze(config, other, inventory_dir) == EXIT_ERROR


def test_tampered_inventory_refused(setup):
    _, backup, config, inventory_dir = setup
    csv_path = inventory_dir / "inventory.csv"
    csv_path.write_text(csv_path.read_text() + "x\n")
    assert analyze(config, backup, inventory_dir) == EXIT_ERROR


def test_inventory_argument_required(setup):
    _, backup, config, _ = setup
    with pytest.raises(SystemExit):
        main(["--config", config, "analyze", "--input", str(backup)])
