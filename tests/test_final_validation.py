import copy

import pytest
from lxml import etree

from panorama_cleanup.analysis import run_analysis
from panorama_cleanup.cleanup import deletion_operations, review_candidates
from panorama_cleanup.final_validation import APPROVED, NOT_APPROVED, Authorized, validate_final
from panorama_cleanup.planner import build_plan
from panorama_cleanup.report_generator import (
    operation_rows, plan_object_rows, reference_update_rows,
)
from panorama_cleanup.shared_converter import apply_plan
from panorama_cleanup.xml_reader import parse_backup
from tests.conftest import FIXTURES

DG = "/config/devices/entry[@name='localhost.localdomain']/device-group/entry"


@pytest.fixture(scope="module")
def pipeline():
    original = parse_backup(FIXTURES / "panorama_deps.xml")
    an = run_analysis(original)
    plan = build_plan(an)
    ops = operation_rows(an, plan)
    copies = [u for u in reference_update_rows(an, plan) if u["applies_to"] == "cópia criada em Shared"]
    res = apply_plan(original, ops, copies)
    values = {r.xpath: r.value for r in an.inventory.records}
    cands = review_candidates(res.tree, run_analysis(res.tree), ops, values, keep_types=("tag",))
    final = apply_plan(res.tree, deletion_operations(cands), [], phases=(4,))
    auth = Authorized(
        decisions={d["xpath"]: d for d in plan_object_rows(an, plan)},
        rule_renames=res.rule_renames, local_renames=res.object_renames,
        copy_renames=res.copy_member_renames, deleted_unused=set(final.deleted_xpaths),
        kept_types=("tag",))
    return original, final.tree, auth


def failed(v):
    return {name for _, name, ok, _ in v.checks if not ok}


def test_untouched_final_is_approved(pipeline):
    original, final, auth = pipeline
    v = validate_final(original, final, auth)
    assert v.result == APPROVED, failed(v)
    assert v.summary["tags_preserved"] == 4


def tamper(final, fn):
    t = copy.deepcopy(final)
    fn(t)
    return t


def one(t, xpath):
    (el,) = t.xpath(xpath)
    return el


CASES = [
    ("mudar ação de regra",
     lambda t: one(t, f"{DG}[@name='DG-RJ']/post-rulebase/security/rules/entry[@name='RJ-R1']/action")
     .__setattr__("text", "deny"),
     "nenhuma alteração não autorizada em campos de regra"),
    ("trocar UUID",
     lambda t: one(t, "/config/shared/pre-rulebase/security/rules/entry[@name='SH-R1']")
     .set("uuid", "33333333-3333-3333-3333-333333333333"),
     "UUIDs idênticos"),
    ("reordenar regras",
     lambda t: (lambda r: (r.remove(r[0]), r.append(copy.deepcopy(r[0]))))(
         one(t, f"{DG}[@name='DG-RJ']/post-rulebase/security/rules")),
     "ordem das regras idêntica"),
    ("remover regra",
     lambda t: (lambda e: e.getparent().remove(e))(
         one(t, f"{DG}[@name='DG-RJ']/post-rulebase/security/rules/entry[@name='RJ-R2']")),
     "mesma quantidade de regras por rulebase"),
    ("trocar zona",
     lambda t: one(t, f"{DG}[@name='DG-RJ']/post-rulebase/security/rules/entry[@name='RJ-R1']/from/member")
     .__setattr__("text", "dmz"),
     "campos que não referenciam objetos (ação, zonas, flags, alvo, tags) intactos"),
    ("membro de regra não autorizado",
     lambda t: one(t, f"{DG}[@name='DG-RJ']/post-rulebase/security/rules/entry[@name='RJ-R2']/destination/member")
     .__setattr__("text", "NAT-TARGET"),
     "nenhuma alteração não autorizada em campos de regra"),
    ("alterar valor de objeto movido",
     lambda t: one(t, "/config/shared/address/entry[@name='NAT-TARGET']/ip-netmask").__setattr__("text", "9.9.9.9"),
     "cada objeto do original tem o destino previsto no plano"),
    ("alterar objeto mantido",
     lambda t: one(t, "/config/shared/address/entry[@name='DNS-SHARED']/ip-netmask").__setattr__("text", "9.9.9.9"),
     "valor efetivo de cada referência de regra idêntico em cada firewall"),
    ("objeto extra",
     lambda t: one(t, "/config/shared/address").append(
         etree.fromstring("<entry name='NOVO'><ip-netmask>1.1.1.1</ip-netmask></entry>")),
     "nenhum objeto no XML final sem origem autorizada"),
    ("excluir objeto usado",
     lambda t: (lambda e: e.getparent().remove(e))(one(t, "/config/shared/address/entry[@name='DNS-SHARED']")),
     "nenhuma referência nova sem resolução (quebrada)"),
    ("alterar cor de tag",
     lambda t: one(t, "/config/shared/tag/entry[@name='PROD']").append(etree.fromstring("<color>color5</color>")),
     "todas as tags do original presentes e idênticas (mesmo escopo)"),
    ("excluir tag",
     lambda t: (lambda e: e.getparent().remove(e))(one(t, "/config/shared/tag/entry[@name='UNUSED-TAG']")),
     "todas as tags do original presentes e idênticas (mesmo escopo)"),
    ("adicionar tag a objeto",
     lambda t: one(t, "/config/shared/address/entry[@name='NAT-TARGET']").append(
         etree.fromstring("<tag><member>PROD</member></tag>")),
     "tags aplicadas aos objetos inalteradas"),
    ("alterar template",
     lambda t: one(t, "//template/entry[@name='T-RJ']/config").append(etree.Element("x")),
     "configuração não relacionada (templates, Panorama, readonly, DGs) idêntica"),
    ("alterar readonly",
     lambda t: one(t, "/config/readonly/devices/entry/device-group/entry[@name='DG-RJ']/id").__setattr__("text", "99"),
     "configuração não relacionada (templates, Panorama, readonly, DGs) idêntica"),
    ("objeto de Shared referenciando objeto de DG",
     lambda t: one(t, "/config/shared/address-group/entry[@name='GRP-NESTED-INNER']/static/member")
     .__setattr__("text", "AMBIG"),
     "objetos em Shared referenciam apenas objetos de Shared"),
    ("versão alterada",
     lambda t: t.getroot().set("version", "11.2.0"),
     "raiz <config> e atributos de versão idênticos"),
]


@pytest.mark.parametrize("label,fn,check", CASES, ids=[c[0] for c in CASES])
def test_tampering_is_not_approved(pipeline, label, fn, check):
    original, final, auth = pipeline
    v = validate_final(original, tamper(final, fn), auth)
    assert v.result == NOT_APPROVED
    assert check in failed(v), failed(v)


def test_missing_authorization_is_not_approved(pipeline):
    original, final, auth = pipeline
    no_renames = Authorized(auth.decisions, {}, auth.local_renames, auth.copy_renames,
                            auth.deleted_unused, auth.kept_types)
    v = validate_final(original, final, no_renames)
    assert v.result == NOT_APPROVED
    assert "nenhuma alteração não autorizada em campos de regra" in failed(v)


def test_exported_mode_ignores_readonly_only(pipeline):
    original, final, auth = pipeline
    t = tamper(final, lambda x: one(x, "/config/readonly/devices/entry/device-group/entry[@name='DG-RJ']/id")
               .__setattr__("text", "99"))
    v = validate_final(original, t, auth, ignore_sections=("/config/readonly",))
    assert v.result == APPROVED, failed(v)
    t2 = tamper(t, lambda x: one(x, "//template/entry[@name='T-RJ']/config").append(etree.Element("x")))
    assert validate_final(original, t2, auth, ignore_sections=("/config/readonly",)).result == NOT_APPROVED
