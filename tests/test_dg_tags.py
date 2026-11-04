"""Tags de DG nunca são movidas nem consolidadas (decisão padrão D17).

Caso real: o plano movia 18 tags de DG para Shared; a Etapa 5 aceitava, mas a Etapa 8
reprovava ("tag ausente do escopo original" e "tag nova em Shared"). Agora o plano as
mantém no DG, e objetos que dependem delas ficam no DG pela verificação de dependências.
"""

import csv
import json

import pytest

from panorama_cleanup.analysis import run_analysis
from panorama_cleanup.cli import EXIT_OK, main
from panorama_cleanup.planner import (
    CONSOLIDATE, MOVE, RENAME_MOVE, PlanError, build_plan, check_tag_invariant,
)
from panorama_cleanup.xml_reader import parse_backup
from tests.conftest import FIXTURES

XML = FIXTURES / "panorama_dg_tags.xml"


@pytest.fixture(scope="module")
def planned():
    an = run_analysis(parse_backup(XML))
    return an, build_plan(an)


def _decision(an, plan, scope, otype, name):
    (k,) = [i for i, r in enumerate(an.inventory.records)
            if (r.scope, r.object_type, r.name) == (scope, otype, name)]
    return plan.decisions[k]


def test_dg_tags_stay_and_dependents_stay(planned):
    an, plan = planned
    assert _decision(an, plan, "DG-A", "tag", "AMCOM").action == "KEEP_LOCAL"
    assert _decision(an, plan, "DG-B", "tag", "AMCOM").action == "KEEP_LOCAL"
    srv = _decision(an, plan, "DG-A", "address", "SRV-A")
    assert srv.action == "KEEP_LOCAL" and "tag 'AMCOM'" in srv.reason
    assert _decision(an, plan, "DG-A", "address", "WEB").action == MOVE
    assert _decision(an, plan, "DG-B", "address", "WEB").action == CONSOLIDATE


def test_invariant_refuses_any_tag_move(planned):
    from dataclasses import replace
    an, plan = planned
    (k,) = [i for i, r in enumerate(an.inventory.records) if r.object_type == "tag" and r.scope == "DG-A"]
    for action in (MOVE, CONSOLIDATE, RENAME_MOVE):
        bad = replace(plan, decisions={**plan.decisions, k: replace(plan.decisions[k], action=action)})
        with pytest.raises(PlanError):
            check_tag_invariant(an.inventory, bad)


def _latest(work, kind):
    return sorted(p for p in (work / "output" / "reports").iterdir()
                  if p.name.split("-", 1)[1].startswith(kind + "-"))[-1]


def test_full_chain_is_approved_with_tags_untouched(tmp_path):
    work = tmp_path / "w"
    work.mkdir()
    w, b = ["--workdir", str(work)], str(XML)
    assert main(w + ["inventory", "--input", b]) == EXIT_OK
    assert main(w + ["analyze", "--input", b, "--inventory", str(_latest(work, "inventory"))]) == EXIT_OK
    assert main(w + ["plan", "--input", b, "--analysis", str(_latest(work, "analyze"))]) == EXIT_OK
    plan = _latest(work, "plan")
    assert main(w + ["apply", "--input", b, "--plan", str(plan)]) == EXIT_OK
    convert = _latest(work, "apply")
    assert main(w + ["apply", "--stage", "cleanup", "--keep-type", "tag", "--input", b,
                     "--plan", str(plan), "--from-run", str(convert)]) == EXIT_OK
    cleanup = _latest(work, "apply-cleanup")
    final = next((work / "output" / "xml" / cleanup.name).glob("*.final.xml"))
    assert main(w + ["validate", "--input", b, "--modified", str(final), "--plan", str(plan),
                     "--convert-run", str(convert), "--cleanup-run", str(cleanup)]) == EXIT_OK
    validate = _latest(work, "validate")
    assert json.loads((validate / "manifest.json").read_text(encoding="utf-8"))["status"] == "ok"
    checks = list(csv.DictReader(open(validate / "validation-checks.csv", encoding="utf-8")))
    assert checks and all(c["result"] == "OK" for c in checks if "tag" in c["check"].lower())
