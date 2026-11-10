"""Caso Site_REC: grupo local sem uso (DELETE_UNUSED) com membro renomeado.

O service 389 local difere do de Shared e vira 389-tcp-389 em Shared. O grupo PT_TCP_AD,
sem uso, continua no DG até a fase 4 (que pode ser adiada): seu membro precisa ser
atualizado na fase 2. Antes da correção, o plano listava a atualização como "cópia criada
em Shared" sem operação correspondente; a Etapa 5 não a aplicava e passava em todas as
verificações; só a Etapa 8 percebia, abortando sem relatório.
"""

import csv
import json

import pytest

from panorama_cleanup.analysis import run_analysis
from panorama_cleanup.cli import EXIT_OK, main
from panorama_cleanup.planner import build_plan
from panorama_cleanup.report_generator import operation_rows, reference_update_rows
from panorama_cleanup.xml_reader import parse_backup
from tests.conftest import FIXTURES

XML = FIXTURES / "panorama_unused_group_ref.xml"


@pytest.fixture(scope="module")
def planned():
    an = run_analysis(parse_backup(XML))
    return an, build_plan(an)


def _latest(work, kind):
    runs = sorted(p for p in (work / "output" / "reports").iterdir()
                  if p.name.split("-", 1)[1].startswith(kind + "-"))
    return runs[-1]


def test_plan_has_phase2_update_for_unused_group(planned):
    an, plan = planned
    ops = operation_rows(an, plan)
    updates = [o for o in ops if o["operation"] == "UPDATE_OBJECT_REFERENCE"]
    assert len(updates) == 1 and updates[0]["phase"] == 2
    assert [o["name"] for o in ops if o["operation"] == "DELETE_UNUSED"] == ["PT_TCP_AD"]
    (row,) = [r for r in reference_update_rows(an, plan) if r["kind"] == "objeto"]
    assert (row["source_name"], row["old"], row["new"]) == ("PT_TCP_AD", "389", "389-tcp-389")
    assert row["applies_to"] == "objeto local"


def test_plan_verifies_remaining_groups(planned):
    _, plan = planned
    groups = [v for v in plan.verification if v[1] == "grupos remanescentes"]
    assert [v[0] for v in groups] == [1, 2, 3, 4]
    assert all(v[2] > 0 for v in groups[:3]) and all(v[3] == 0 for v in groups)


def test_full_chain_applies_update_and_is_approved(tmp_path):
    work = tmp_path / "w"
    work.mkdir()
    w = ["--workdir", str(work)]
    b = str(XML)
    assert main(w + ["inventory", "--input", b]) == EXIT_OK
    assert main(w + ["analyze", "--input", b, "--inventory", str(_latest(work, "inventory"))]) == EXIT_OK
    assert main(w + ["plan", "--input", b, "--analysis", str(_latest(work, "analyze"))]) == EXIT_OK
    plan = _latest(work, "plan")
    assert main(w + ["apply", "--input", b, "--plan", str(plan)]) == EXIT_OK
    convert = _latest(work, "apply")
    rows = list(csv.DictReader(open(convert / "reference-updates.csv", encoding="utf-8")))
    assert rows and all(r["status"] == "aplicado" for r in rows)
    checks = {r["check"]: r["result"] for r in csv.DictReader(open(convert / "integrity-checks.csv", encoding="utf-8"))}
    assert checks["todas as atualizações de referência do plano aplicadas"] == "OK"
    assert checks["valor efetivo dos grupos remanescentes idêntico em todo DG onde uma regra os usa"] == "OK"
    shared_xml = next((work / "output" / "xml" / convert.name).glob("*.shared.xml")).read_text(encoding="utf-8")
    assert "<member>389-tcp-389</member>" in shared_xml.split('name="PT_TCP_AD"')[1].split("</entry>")[0]

    assert main(w + ["apply", "--stage", "cleanup", "--keep-type", "tag", "--input", b,
                     "--plan", str(plan), "--from-run", str(convert)]) == EXIT_OK
    cleanup = _latest(work, "apply-cleanup")
    review = {r["name"]: r for r in csv.DictReader(open(cleanup / "unused-review.csv", encoding="utf-8"))}
    # Antes da correção da Etapa 6, o grupo era mantido com "valor difere do plano": o
    # membro já renomeado na fase 2 era comparado com o valor do original.
    assert review["PT_TCP_AD"]["status"] == "excluído", review["PT_TCP_AD"]
    final = next((work / "output" / "xml" / cleanup.name).glob("*.final.xml"))
    assert 'name="PT_TCP_AD"' not in final.read_text(encoding="utf-8")
    assert main(w + ["validate", "--input", b, "--modified", str(final), "--plan", str(plan),
                     "--convert-run", str(convert), "--cleanup-run", str(cleanup)]) == EXIT_OK
    manifest = json.loads((_latest(work, "validate") / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["status"] == "ok"


def test_missing_update_is_caught_at_stage5(planned):
    """Sem a operação de fase 2, a Etapa 5 agora reprova (antes passava em 11 de 11)."""
    from panorama_cleanup.shared_converter import apply_plan
    from panorama_cleanup.xml_validator import validate_transformation
    an, plan = planned
    original = parse_backup(XML)
    ops = [o for o in operation_rows(an, plan) if o["operation"] != "UPDATE_OBJECT_REFERENCE"]
    result = apply_plan(original, ops, [])
    checks, _, _ = validate_transformation(original, result.tree, result, ops)
    by_name = {c.name: c.passed for c in checks}
    assert by_name["valor efetivo dos grupos remanescentes idêntico em todo DG onde uma regra os usa"] is False
