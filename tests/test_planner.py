import pytest

from panorama_cleanup.analysis import run_analysis
from panorama_cleanup.planner import (
    CONSOLIDATE, DELETE_UNUSED, KEEP, KEEP_LOCAL, MOVE, PENDING, RENAME_MOVE, PlanError,
    build_plan, check_tag_invariant, operation_sequence,
)
from panorama_cleanup.xml_reader import parse_backup
from tests.conftest import FIXTURES


def analyze(path):
    an = run_analysis(parse_backup(path))
    return an, build_plan(an)


def decision(an, plan, scope, name, object_type=None):
    for i, r in enumerate(an.inventory.records):
        if (r.scope, r.name) == (scope, name) and object_type in (None, r.object_type):
            return plan.decisions[i]
    raise KeyError((scope, name))


def panorama(dgs: dict, shared: str = "", parents: dict = None) -> str:
    parents = parents or {}
    groups = "".join(f'<entry name="{n}">{body}</entry>' for n, body in dgs.items())
    readonly = "".join(
        f'<entry name="{n}">' + (f"<parent-dg>{parents[n]}</parent-dg>" if n in parents else "")
        + "</entry>" for n in dgs)
    return (f'<config version="11.1.0"><devices><entry name="localhost.localdomain">'
            f"<device-group>{groups}</device-group></entry></devices><readonly><devices>"
            f'<entry name="localhost.localdomain"><device-group>{readonly}</device-group>'
            f"</entry></devices></readonly><shared>{shared}</shared></config>")


def addr(name, ip, tags=()):
    tag = "".join(f"<member>{t}</member>" for t in tags)
    return (f'<entry name="{name}"><ip-netmask>{ip}</ip-netmask>'
            + (f"<tag>{tag}</tag>" if tags else "") + "</entry>")


def rule(name, *destinations, rulebase="post-rulebase"):
    members = "".join(f"<member>{d}</member>" for d in destinations)
    return (f"<{rulebase}><security><rules><entry name=\"{name}\"><destination>{members}"
            f"</destination></entry></rules></security></{rulebase}>")


@pytest.fixture
def write(tmp_path):
    def _write(text):
        path = tmp_path / "p.xml"
        path.write_text(text, encoding="utf-8")
        return path
    return _write


@pytest.fixture(scope="module")
def deps():
    return analyze(FIXTURES / "panorama_deps.xml")


EXPECTED_DG = {
    ("DG-RJ", "RJ-TAG"): DELETE_UNUSED,
    ("DG-RJ", "SERVER"): CONSOLIDATE,
    ("DG-RJ", "SHADOW"): RENAME_MOVE,
    ("DG-RJ", "NAT-TARGET"): MOVE,
    ("DG-RJ", "DUPE"): MOVE,
    ("DG-RJ", "TAGDIFF"): DELETE_UNUSED,
    ("DG-RJ", "WEB-B"): DELETE_UNUSED,
    ("DG-RJ", "OVR-MEMBER"): KEEP_LOCAL,
    ("DG-RJ", "NEXTHOP-OBJ"): KEEP_LOCAL,
    ("DG-RJ", "GRP-X"): MOVE,
    ("DG-BH", "SERVER"): KEEP_LOCAL,
    ("DG-BH", "DUPE"): DELETE_UNUSED,
    ("DG-BH", "TAGDIFF"): MOVE,
    ("DG-BH", "BH-ONLY"): DELETE_UNUSED,
    ("DG-BH", "BH-OBJ"): MOVE,
    ("DG-BH", "GRP-X"): DELETE_UNUSED,
    ("DG-BH-FILIAL", "FIL-ONLY"): KEEP_LOCAL,
    ("DG-BH-FILIAL", "APPS-WEB"): MOVE,
    ("DG-BH-FILIAL", "FILTER-1"): MOVE,
}


def test_fixture_decisions(deps):
    an, plan = deps
    got = {(r.scope, r.name): plan.decisions[i].action
           for i, r in enumerate(an.inventory.records)
           if r.scope != "shared" and r.name not in ("AMBIG", "MIXED")}
    assert got == EXPECTED_DG
    assert decision(an, plan, "DG-RJ", "MIXED", "address-group").action == DELETE_UNUSED
    assert {decision(an, plan, "DG-RJ", "AMBIG", t).action
            for t in ("address", "address-group")} == {KEEP_LOCAL}


def test_fixture_is_transparent_in_every_phase(deps):
    _, plan = deps
    assert [(v[0], v[1]) for v in plan.verification] == [
        (p, what) for p in (1, 2, 3, 4) for what in ("referências de regra", "grupos remanescentes")]
    assert all(v[3] == 0 for v in plan.verification)


def test_rename_uses_dg_suffix_and_updates_rule(deps):
    an, plan = deps
    assert decision(an, plan, "DG-RJ", "SHADOW").new_name == "SHADOW_DG-RJ"
    (u,) = plan.rule_updates
    assert (u["source_name"], u["field"], u["old"], u["new"]) == (
        "RJ-R1", "destination", "SHADOW", "SHADOW_DG-RJ")


def test_override_used_by_shared_definition_stays_local(deps):
    an, plan = deps
    assert "rule SH-R1" in decision(an, plan, "DG-BH", "SERVER").reason
    assert "GRP-SHARED-OVR" in decision(an, plan, "DG-RJ", "OVR-MEMBER").reason


def test_newly_visible_name_is_demoted(deps):
    an, plan = deps
    d = decision(an, plan, "DG-BH-FILIAL", "FIL-ONLY")
    assert d.reason.startswith("transparência") and "DG-BH" in d.reason


def test_shared_objects_kept_or_deleted(deps):
    an, plan = deps
    actions = {plan.decisions[i].action for i, r in enumerate(an.inventory.records)
               if r.scope == "shared"}
    assert actions <= {KEEP, DELETE_UNUSED}
    assert decision(an, plan, "shared", "SHADOW").action == DELETE_UNUSED
    assert decision(an, plan, "shared", "ROLE-X").action == KEEP


def test_conflict_between_dgs_renames_each(write):
    path = write(panorama({
        "DG-A": "<address>" + addr("X", "1.1.1.1") + "</address>" + rule("RA", "X"),
        "DG-B": "<address>" + addr("X", "2.2.2.2") + "</address>" + rule("RB", "X"),
    }))
    an, plan = analyze(path)
    assert decision(an, plan, "DG-A", "X").new_name == "X_DG-A"
    assert decision(an, plan, "DG-B", "X").new_name == "X_DG-B"
    assert {(u["source_name"], u["new"]) for u in plan.rule_updates} == {
        ("RA", "X_DG-A"), ("RB", "X_DG-B")}
    assert all(v[3] == 0 for v in plan.verification)


def test_identical_copies_across_dgs_consolidate_into_one(write):
    path = write(panorama({
        "DG-A": "<address>" + addr("X", "1.1.1.1") + "</address>" + rule("RA", "X"),
        "DG-B": "<address>" + addr("X", "1.1.1.1/32") + "</address>" + rule("RB", "X"),
    }))
    an, plan = analyze(path)
    a, b = decision(an, plan, "DG-A", "X"), decision(an, plan, "DG-B", "X")
    assert (a.action, b.action) == (MOVE, CONSOLIDATE)
    assert an.inventory.records[b.target].scope == "DG-A"
    assert plan.rule_updates == []


def test_name_collision_is_pending(write):
    path = write(panorama({
        "DG-A": "<address>" + addr("X", "1.1.1.1") + addr("X_DG-A", "3.3.3.3") + "</address>"
                + rule("RA", "X", "X_DG-A"),
    }, shared="<address>" + addr("X", "2.2.2.2") + "</address>"))
    an, plan = analyze(path)
    d = decision(an, plan, "DG-A", "X")
    assert d.action == PENDING and "já existe" in d.reason


def test_name_too_long_is_pending(write):
    long_name = "N" * 60
    path = write(panorama({
        "DG-A": "<address>" + addr(long_name, "1.1.1.1") + "</address>" + rule("RA", long_name),
    }, shared="<address>" + addr(long_name, "2.2.2.2") + "</address>"))
    an, plan = analyze(path)
    d = decision(an, plan, "DG-A", long_name)
    assert d.action == PENDING and "excede 63" in d.reason


def test_local_group_member_updated_in_place(write):
    path = write(panorama({
        "DG-A": "<address>" + addr("X", "1.1.1.1") + "</address>"
                "<address-group><entry name=\"G\"><static><member>X</member></static></entry></address-group>",
    }, shared="<address>" + addr("X", "9.9.9.9") + addr("Y", "8.8.8.8") + "</address>"
              "<address-group><entry name=\"G\"><static><member>Y</member></static></entry></address-group>"
              + rule("RS", "G", rulebase="pre-rulebase")))
    an, plan = analyze(path)
    assert decision(an, plan, "DG-A", "G").action == KEEP_LOCAL
    assert decision(an, plan, "DG-A", "X").new_name == "X_DG-A"
    (u,) = plan.object_updates
    assert (u["field"], u["old"], u["new"]) == ("static", "X", "X_DG-A")
    ops = [op for _, op, _, _ in operation_sequence(an.inventory, plan)]
    assert "UPDATE_OBJECT_REFERENCE" in ops
    assert all(v[3] == 0 for v in plan.verification)


def test_moved_group_copy_gets_renamed_member(write):
    path = write(panorama({
        "DG-A": "<address>" + addr("X", "1.1.1.1") + "</address>"
                "<address-group><entry name=\"G\"><static><member>X</member></static></entry></address-group>"
                + rule("RA", "G"),
    }, shared="<address>" + addr("X", "9.9.9.9") + "</address>"))
    an, plan = analyze(path)
    assert decision(an, plan, "DG-A", "G").action == MOVE
    (u,) = plan.object_updates
    assert u["new"] == "X_DG-A" and u["source_action"] == MOVE


def test_unused_shared_that_becomes_used_is_not_deleted(write):
    path = write(panorama({
        "DG-A": "<address>" + addr("S", "1.1.1.1") + "</address>" + rule("RA", "S"),
    }, shared="<address>" + addr("S", "1.1.1.1") + "</address>"))
    an, plan = analyze(path)
    assert decision(an, plan, "DG-A", "S").action == CONSOLIDATE
    shared = decision(an, plan, "shared", "S")
    assert shared.action == KEEP and "exclusão cancelada" in shared.reason


def test_tagged_address_joining_shared_dynamic_group_is_demoted(write):
    path = write(panorama({
        "DG-A": "<address>" + addr("A1", "1.1.1.1", ("PROD",)) + "</address>" + rule("RA", "A1"),
        "DG-B": rule("RB", "DYN"),
    }, shared="<tag><entry name=\"PROD\"/></tag>"
              "<address-group><entry name=\"DYN\"><dynamic><filter>'PROD'</filter></dynamic></entry>"
              "</address-group>"))
    an, plan = analyze(path)
    d = decision(an, plan, "DG-A", "A1")
    assert d.action == KEEP_LOCAL and "DG-B" in d.reason


def test_move_requires_tag_in_shared(write):
    path = write(panorama({
        "DG-A": "<tag><entry name=\"LOCAL\"><color>color1</color></entry></tag>"
                "<address>" + addr("A1", "1.1.1.1", ("LOCAL",)) + "</address>" + rule("RA", "A1"),
        "DG-B": "<tag><entry name=\"LOCAL\"><color>color2</color></entry></tag>"
                "<address>" + addr("B1", "2.2.2.2", ("LOCAL",)) + "</address>" + rule("RB", "B1"),
    }))
    an, plan = analyze(path)
    assert decision(an, plan, "DG-A", "LOCAL", "tag").action == KEEP_LOCAL
    a1 = decision(an, plan, "DG-A", "A1")
    assert a1.action == KEEP_LOCAL and a1.reason.startswith("dependência")


def test_tags_never_renamed_or_changed(deps):
    an, plan = deps
    check_tag_invariant(an.inventory, plan)
    for i, d in plan.decisions.items():
        if an.inventory.records[i].object_type == "tag":
            assert d.action != RENAME_MOVE


def test_tag_invariant_rejects_tag_updates(deps):
    an, plan = deps
    plan.rule_updates.append({"field": "tag", "source_xpath": "/x"})
    try:
        with pytest.raises(PlanError, match="tag"):
            check_tag_invariant(an.inventory, plan)
    finally:
        plan.rule_updates.pop()


def test_operation_order(write):
    path = write(panorama({
        "DG-A": "<address>" + addr("L", "1.1.1.1") + "</address><address-group>"
                "<entry name=\"OUTER\"><static><member>INNER</member></static></entry>"
                "<entry name=\"INNER\"><static><member>L</member></static></entry></address-group>"
                + rule("RA", "OUTER"),
    }))
    an, plan = analyze(path)
    ops = [(phase, op, an.inventory.records[k].name)
           for phase, op, k, _ in operation_sequence(an.inventory, plan)]
    creates = [n for p, o, n in ops if o == "CREATE_SHARED"]
    deletes = [n for p, o, n in ops if o == "DELETE_LOCAL_COPY"]
    assert creates == ["L", "INNER", "OUTER"]
    assert deletes == ["OUTER", "INNER", "L"]
    assert [p for p, _, _ in ops] == sorted(p for p, _, _ in ops)


def test_planning_does_not_modify_xml():
    path = FIXTURES / "panorama_deps.xml"
    before = path.read_bytes()
    analyze(path)
    assert path.read_bytes() == before


def test_consolidation_compares_value_in_consumer_context(write):
    # Iguais no próprio escopo, diferentes nos firewalls do DG: o grupo de Shared
    # resolveria o membro M para a cópia local de M no DG-A.
    path = write(panorama({
        "DG-A": "<address>" + addr("N", "1.1.1.1") + addr("M", "2.2.2.2") + "</address>"
                "<address-group><entry name=\"G\"><static><member>N</member></static></entry>"
                "</address-group>" + rule("RA", "G", "M"),
    }, shared="<address>" + addr("M", "1.1.1.1") + "</address>"
              "<address-group><entry name=\"G\"><static><member>M</member></static></entry>"
              "</address-group>" + rule("RS", "G", rulebase="pre-rulebase")))
    an, plan = analyze(path)
    assert decision(an, plan, "DG-A", "G").action != CONSOLIDATE
    assert all(v[3] == 0 for v in plan.verification)
    assert plan.demotions == []
