import pytest

from panorama_cleanup.analysis import run_analysis
from panorama_cleanup.cleanup import deletion_operations, review_candidates
from panorama_cleanup.planner import build_plan
from panorama_cleanup.report_generator import operation_rows, reference_update_rows
from panorama_cleanup.shared_converter import apply_plan
from panorama_cleanup.xml_reader import parse_backup
from panorama_cleanup.xml_validator import validate_end_to_end, validate_transformation
from tests.conftest import FIXTURES

SHARED_UNUSED = {"GRP-UNUSED", "UDP-UNUSED", "WEB-A", "SHADOW", "ORPHAN", "MIXED",
                 "IN-UNUSED-GRP", "UNUSED-TAG", "ON-ORPHAN"}
DG_UNUSED = {("DG-RJ", "MIXED"), ("DG-BH", "GRP-X"), ("DG-RJ", "WEB-B"), ("DG-RJ", "TAGDIFF"),
             ("DG-BH", "DUPE"), ("DG-BH", "BH-ONLY"), ("DG-RJ", "RJ-TAG")}


def converted(path):
    tree = parse_backup(path)
    an = run_analysis(tree)
    plan = build_plan(an)
    ops = operation_rows(an, plan)
    copies = [u for u in reference_update_rows(an, plan) if u["applies_to"] == "cópia criada em Shared"]
    res = apply_plan(tree, ops, copies)
    values = {r.xpath: r.value for r in an.inventory.records}
    return tree, ops, res, values


@pytest.fixture
def stage():
    return converted(FIXTURES / "panorama_deps.xml")


def review(tree, ops, values):
    return review_candidates(tree, run_analysis(tree), ops, values)


def test_all_planned_unused_confirmed(stage):
    _, ops, res, values = stage
    cands = review(res.tree, ops, values)
    assert {c.name for c in cands if c.scope == "shared" and c.delete} == SHARED_UNUSED
    assert {(c.scope, c.name) for c in cands if c.scope != "shared" and c.delete} == DG_UNUSED
    assert all(c.usage == "UNUSED" for c in cands)


def test_chain_of_unused_removed_together(stage):
    _, ops, res, values = stage
    cands = {c.name: c for c in review(res.tree, ops, values) if c.scope == "shared"}
    assert cands["IN-UNUSED-GRP"].reason == "referenciado apenas por objetos também removidos"
    assert cands["ON-ORPHAN"].delete


def test_final_integrity(stage):
    original, ops, res, values = stage
    confirmed = deletion_operations(review(res.tree, ops, values))
    final = apply_plan(res.tree, confirmed, [], phases=(4,))
    checks, rows, _ = validate_transformation(res.tree, final.tree, final, confirmed, phases=(4,))
    checks += validate_end_to_end(original, final.tree, res.rule_renames, final.deleted_xpaths)
    assert [c.name for c in checks if not c.passed] == []
    assert {r["status"] for r in rows} == {"idêntica"}
    assert final.tree.xpath("/config/shared/address/entry[@name='DNS-SHARED']")
    assert not final.tree.xpath("/config/shared/address/entry[@name='ORPHAN']")


def test_object_used_after_conversion_is_kept(stage):
    _, ops, res, values = stage
    t = res.tree
    rule = t.xpath("/config/shared/pre-rulebase/security/rules/entry[@name='SH-R1']/destination")[0]
    member = rule.makeelement("member")
    member.text = "ORPHAN"
    rule.append(member)
    cands = {c.name: c for c in review(t, ops, values) if c.scope == "shared"}
    assert not cands["ORPHAN"].delete and cands["ORPHAN"].usage == "USED"
    assert not cands["ON-ORPHAN"].delete


def test_group_kept_keeps_its_member(stage):
    _, ops, res, values = stage
    t = res.tree
    rule = t.xpath("/config/shared/pre-rulebase/security/rules/entry[@name='SH-R1']/destination")[0]
    member = rule.makeelement("member")
    member.text = "GRP-UNUSED"
    rule.append(member)
    cands = {c.name: c for c in review(t, ops, values) if c.scope == "shared"}
    assert not cands["GRP-UNUSED"].delete
    assert not cands["IN-UNUSED-GRP"].delete


def test_text_outside_analyzed_areas_blocks(stage):
    _, ops, res, values = stage
    t = res.tree
    template = t.xpath("//template/entry[@name='T-RJ']/config")[0]
    el = template.makeelement("peer-address")
    el.text = "WEB-A"
    template.append(el)
    cands = {c.name: c for c in review(t, ops, values) if c.scope == "shared"}
    assert not cands["WEB-A"].delete


def test_changed_value_blocks(stage):
    _, ops, res, values = stage
    res.tree.xpath("/config/shared/address/entry[@name='ORPHAN']/ip-netmask")[0].text = "1.1.1.1"
    cands = {c.name: c for c in review(res.tree, ops, values) if c.scope == "shared"}
    assert not cands["ORPHAN"].delete and cands["ORPHAN"].reason == "valor difere do plano"


def test_missing_object_blocks(stage):
    _, ops, res, values = stage
    el = res.tree.xpath("/config/shared/address/entry[@name='ORPHAN']")[0]
    el.getparent().remove(el)
    cands = {c.name: c for c in review(res.tree, ops, values) if c.scope == "shared"}
    assert cands["ORPHAN"].reason == "objeto não encontrado no XML convertido"


def test_keep_type_preserves_tags(stage):
    _, ops, res, values = stage
    cands = review_candidates(res.tree, run_analysis(res.tree), ops, values, keep_types=("tag",))
    tags = [c for c in cands if c.object_type == "tag"]
    assert tags and not any(c.delete for c in tags)
    assert all("decisão do usuário" in c.reason for c in tags)
    others = {c.name for c in cands if c.delete}
    assert "ORPHAN" in others  # objeto com tag pode ser removido; a tag fica


def test_keep_type_final_integrity(stage):
    original, ops, res, values = stage
    confirmed = deletion_operations(review_candidates(res.tree, run_analysis(res.tree), ops, values,
                                                      keep_types=("tag",)))
    final = apply_plan(res.tree, confirmed, [], phases=(4,))
    checks, _, _ = validate_transformation(res.tree, final.tree, final, confirmed, phases=(4,))
    checks += validate_end_to_end(original, final.tree, res.rule_renames, final.deleted_xpaths)
    assert [c.name for c in checks if not c.passed] == []
    assert len(final.tree.xpath("//tag/entry")) == len(original.xpath("//tag/entry"))
