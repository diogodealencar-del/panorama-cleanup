import copy

import pytest

from panorama_cleanup.analysis import run_analysis
from panorama_cleanup.planner import build_plan
from panorama_cleanup.report_generator import operation_rows, reference_update_rows
from panorama_cleanup.shared_converter import ApplyError, apply_plan, serialize
from panorama_cleanup.xml_reader import parse_backup
from panorama_cleanup.xml_validator import validate_transformation
from tests.conftest import FIXTURES

PATH = FIXTURES / "panorama_deps.xml"
DG = "/config/devices/entry[@name='localhost.localdomain']/device-group/entry"


@pytest.fixture
def applied():
    tree = parse_backup(PATH)
    an = run_analysis(tree)
    plan = build_plan(an)
    ops = operation_rows(an, plan)
    copies = [u for u in reference_update_rows(an, plan) if u["applies_to"] == "cópia criada em Shared"]
    return tree, ops, copies, apply_plan(tree, ops, copies)


def failed(checks):
    return [c.name for c in checks if not c.passed]


def test_all_integrity_checks_pass(applied):
    tree, ops, _, result = applied
    checks, rows, _ = validate_transformation(tree, result.tree, result, ops)
    assert failed(checks) == []
    assert {r["status"] for r in rows} == {"idêntica", "referências atualizadas"}


def test_original_tree_not_modified(applied):
    tree, _, _, _ = applied
    assert serialize(tree, PATH.read_bytes()) == PATH.read_bytes()


def test_roundtrip_is_byte_identical():
    tree = parse_backup(PATH)
    assert serialize(tree, PATH.read_bytes()) == PATH.read_bytes()


def test_expected_objects_moved_and_removed(applied):
    _, _, _, result = applied
    t = result.tree
    assert t.xpath("/config/shared/address/entry[@name='SHADOW-10.0.0.99']/ip-netmask/text()") == ["10.0.0.99"]
    assert t.xpath("/config/shared/address/entry[@name='NAT-TARGET']")
    assert not t.xpath(f"{DG}[@name='DG-RJ']/address/entry[@name='SHADOW']")
    assert not t.xpath(f"{DG}[@name='DG-RJ']/address/entry[@name='SERVER']")
    assert t.xpath(f"{DG}[@name='DG-BH']/address/entry[@name='SERVER']")  # mantido local
    assert t.xpath("/config/shared/application-group/entry[@name='APPS-WEB']")


def test_rule_member_renamed_in_place(applied):
    _, _, _, result = applied
    members = result.tree.xpath(f"{DG}[@name='DG-RJ']/post-rulebase/security/rules/"
                                "entry[@name='RJ-R1']/destination/member/text()")
    assert members[0] == "SHADOW-10.0.0.99"
    assert "SHADOW" not in members


def test_unused_objects_not_deleted_in_stage5(applied):
    _, _, _, result = applied
    assert result.tree.xpath("/config/shared/address/entry[@name='ORPHAN']")
    assert result.tree.xpath(f"{DG}[@name='DG-RJ']/address/entry[@name='WEB-B']")


def test_phase_4_operations_ignored(applied):
    _, ops, _, result = applied
    assert any(int(o["phase"]) == 4 for o in ops)
    assert all(c.phase in (1, 2, 3) for c in result.changes)


def test_created_entry_reindented(applied):
    _, _, _, result = applied
    data = serialize(result.tree, PATH.read_bytes()).decode()
    assert '\n      <entry name="NAT-TARGET"><ip-netmask>10.3.3.3</ip-netmask></entry>' in data


@pytest.mark.parametrize("mutate,check", [
    (lambda t: t.xpath(f"{DG}[@name='DG-RJ']/post-rulebase/security/rules/entry[@name='RJ-R1']/action")[0]
     .__setattr__("text", "deny"), "políticas: ordem, UUID e conteúdo preservados"),
    (lambda t: t.xpath("/config/shared/address/entry[@name='NAT-TARGET']/ip-netmask")[0]
     .__setattr__("text", "10.3.3.4"), "cópias em Shared idênticas aos originais (valor, tags, descrição)"),
    (lambda t: t.xpath("/config/shared/address/entry[@name='ORPHAN']/ip-netmask")[0]
     .__setattr__("text", "10.9.9.8"), "configuração fora do plano idêntica (C14N)"),
    (lambda t: t.xpath(f"{DG}[@name='DG-BH']/address/entry[@name='SERVER']/ip-netmask")[0]
     .__setattr__("text", "1.2.3.4"), "valor efetivo idêntico em todos os firewalls"),
])
def test_tampering_is_detected(applied, mutate, check):
    tree, ops, _, result = applied
    mutate(result.tree)
    checks, _, _ = validate_transformation(tree, result.tree, result, ops)
    assert check in failed(checks)


def test_rule_order_change_detected(applied):
    tree, ops, _, result = applied
    rules = result.tree.xpath(f"{DG}[@name='DG-RJ']/post-rulebase/security/rules")[0]
    first = rules[0]
    rules.remove(first)
    rules.append(first)
    checks, rows, _ = validate_transformation(tree, result.tree, result, ops)
    assert "políticas: ordem, UUID e conteúdo preservados" in failed(checks)


def test_precondition_failure_stops(applied):
    tree, ops, copies, _ = applied
    broken = copy.deepcopy(ops)
    for o in broken:
        if o["operation"] == "UPDATE_RULE_REFERENCE":
            o["name"] = "NAO-EXISTE"
    with pytest.raises(ApplyError, match="não encontrado"):
        apply_plan(tree, broken, copies)


def test_existing_name_in_shared_stops(applied):
    tree, ops, copies, _ = applied
    t = copy.deepcopy(tree)
    shared = t.xpath("/config/shared/address")[0]
    clash = copy.deepcopy(shared[0])
    clash.set("name", "SHADOW-10.0.0.99")
    shared.append(clash)
    with pytest.raises(ApplyError, match="já existe"):
        apply_plan(t, ops, copies)
