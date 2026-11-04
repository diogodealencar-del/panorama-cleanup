import csv
import json

import pytest
from openpyxl import load_workbook

from panorama_cleanup.cli import EXIT_OK, main
from panorama_cleanup.run_context import sha256_file
from tests.conftest import FIXTURES, tree_snapshot

EXPECTED_REPORTS = {
    "inventory.csv", "inventory.xlsx", "inventory.html", "device-groups.csv",
    "inventory-summary.csv", "inventory-warnings.csv", "manifest.json",
}


@pytest.fixture
def run(project, tmp_path):
    backup = project.input_dir / "panorama_basic.xml"
    backup.write_bytes((FIXTURES / "panorama_basic.xml").read_bytes())
    before_input = tree_snapshot(project.input_dir)
    code = main(["--config", str(project.root / "config" / "settings.json"),
                 "inventory", "--input", str(backup)])
    (run_dir,) = list(project.reports_dir.iterdir())
    return code, run_dir, backup, before_input


def test_inventory_mode_succeeds(run):
    code, run_dir, _, _ = run
    assert code == EXIT_OK
    assert {p.name for p in run_dir.iterdir()} == EXPECTED_REPORTS


def test_input_untouched_and_no_xml_generated(project, run):
    _, _, _, before = run
    assert tree_snapshot(project.input_dir) == before
    assert not project.xml_output_dir.exists() or not any(project.xml_output_dir.iterdir())
    assert not list(project.reports_dir.rglob("*.xml"))


def test_manifest_traces_input_and_outputs(run):
    _, run_dir, backup, _ = run
    m = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
    assert m["status"] == "ok"
    assert m["input"]["sha256"] == sha256_file(backup)
    assert m["input_unchanged"] is True
    assert m["panos_version"] == "11.1.0"
    assert m["objects"] == 29
    for name, digest in m["outputs"].items():
        assert sha256_file(run_dir / name) == digest


def test_csv_rows(run):
    _, run_dir, _, _ = run
    with open(run_dir / "inventory.csv", encoding="utf-8", newline="") as fh:
        rows = list(csv.DictReader(fh))
    assert len(rows) == 29
    dns = next(r for r in rows if r["name"] == "DNS")
    assert dns["description"] == '=HYPERLINK("http://exemplo")'
    with open(run_dir / "device-groups.csv", encoding="utf-8", newline="") as fh:
        groups = {r["name"]: r for r in csv.DictReader(fh)}
    assert groups["DG-BH-FILIAL"]["path"] == "shared > DG-BH > DG-BH-FILIAL"


def test_xlsx_sheets_and_no_formulas(run):
    _, run_dir, _, _ = run
    wb = load_workbook(run_dir / "inventory.xlsx")
    assert wb.sheetnames == ["Execução", "Resumo", "Device Groups", "Inventário", "Avisos"]
    ws = wb["Inventário"]
    assert ws.max_row == 30
    for row in ws.iter_rows():
        for cell in row:
            assert cell.data_type != "f", cell.coordinate


def test_html_escapes_content(run):
    _, run_dir, _, _ = run
    text = (run_dir / "inventory.html").read_text(encoding="utf-8")
    assert "Hierarquia dos Device Groups" in text
    assert "shared &gt; DG-BH &gt; DG-BH-FILIAL" in text
    assert "<script" not in text
