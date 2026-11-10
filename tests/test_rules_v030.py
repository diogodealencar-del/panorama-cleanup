"""Regras de negócio da 0.3.0 (pedido do usuário):

1. objetos sem uso são excluídos;
2. mesmo nome com valor diferente: NOME-IP / NOME-FQDN / NOME-PROTOCOLO-PORTA;
3. local e Shared com mesmo nome e valor: o local sai; a descrição de Shared prevalece e,
   se só o local tiver descrição, ela é copiada para o objeto de Shared;
4. todo o restante vai para Shared, inclusive membros presos a grupos de Shared que
   mudam de valor por Device Group (fase 0: cópia local do grupo);
e, acima de tudo: nenhum IP, porta ou application muda em nenhuma regra.
"""

import copy

from lxml import etree

from panorama_cleanup.localization import plan_with_localization
from panorama_cleanup.naming import conflict_name, valid_name
from panorama_cleanup.planner import (
    CONSOLIDATE, COPY_UPDATE_ACTIONS, KEEP, PENDING, RENAME_MOVE,
)
from panorama_cleanup.report_generator import operation_rows
from panorama_cleanup.rule_equivalence import compare_rules
from panorama_cleanup.shared_converter import apply_plan
from panorama_cleanup.xml_reader import parse_backup
from tests.test_planner import addr, panorama, rule, write  # noqa: F401  (fixture)


def _plan(path):
    original = parse_backup(path)
    lp = plan_with_localization(original)
    return original, lp


def _decision(lp, scope, name, object_type=None):
    for i, r in enumerate(lp.analysis.inventory.records):
        if (r.scope, r.name) == (scope, name) and object_type in (None, r.object_type):
            return lp.plan.decisions[i]
    raise KeyError((scope, name))


def _apply(original, lp, phases=(0, 1, 2, 3)):
    inv = lp.analysis.inventory
    copies = [dict(source_xpath=inv.records[u["source"]].xpath, field=u["field"], old=u["old"],
                   new=u["new"])
              for u in lp.plan.object_updates if lp.plan.decisions[u["source"]].action in COPY_UPDATE_ACTIONS]
    ops = operation_rows(lp.analysis, lp.plan)
    tree = original if 0 in phases else lp.tree
    return apply_plan(tree, ops, copies, phases=phases), ops


def _svc(name, port, proto="tcp"):
    return (f'<entry name="{name}"><protocol><{proto}><port>{port}</port></{proto}>'
            "</protocol></entry>")


# --- regra 2: nome ------------------------------------------------------------------

def test_conflict_name_by_value():
    el = etree.fromstring
    assert conflict_name("SRV", "address", el("<e><ip-netmask>10.1.1.1</ip-netmask></e>"), "DG")[0] == "SRV-10.1.1.1"
    assert conflict_name("SRV", "address", el("<e><ip-netmask>10.1.1.1/32</ip-netmask></e>"), "DG")[0] == "SRV-10.1.1.1"
    assert conflict_name("NET", "address", el("<e><ip-netmask>10.0.0.0/24</ip-netmask></e>"), "DG")[0] == "NET-10.0.0.0-24"
    assert conflict_name("R", "address", el("<e><ip-range>10.0.0.1-10.0.0.9</ip-range></e>"), "DG")[0] == "R-10.0.0.1-10.0.0.9"
    assert conflict_name("W", "address", el("<e><fqdn>portal.empresa.com.br</fqdn></e>"), "DG")[0] == "W-portal.empresa.com.br"
    assert conflict_name("AV", "service", el(_svc("AV", "8764")), "DG")[0] == "AV-tcp-8764"
    assert conflict_name("M", "service", el(_svc("M", "80,443")), "DG")[0] == "M-tcp-80_443"
    assert conflict_name("G", "address-group", el("<e/>"), "DG-A")[0] == "G_DG-A"
    assert not valid_name("X-2001:db8::1")


def test_ipv6_conflict_is_pending_not_truncated(write):
    path = write(panorama({
        "DG-A": "<address>" + addr("V6", "2001:db8::1") + "</address>" + rule("RA", "V6"),
    }, shared="<address>" + addr("V6", "2001:db8::2") + "</address>"))
    _, lp = _plan(path)
    d = _decision(lp, "DG-A", "V6")
    assert d.action == PENDING and "inválido" in d.reason


def test_same_new_name_in_two_dgs_consolidates_with_rename(write):
    path = write(panorama({
        "DG-A": "<address>" + addr("SRV", "10.1.1.1") + "</address>" + rule("RA", "SRV"),
        "DG-B": "<address>" + addr("SRV", "10.1.1.1") + "</address>" + rule("RB", "SRV"),
        "DG-C": rule("RC", "SRV"),
    }, shared="<address>" + addr("SRV", "10.9.9.9") + "</address>"))
    original, lp = _plan(path)
    a, b = _decision(lp, "DG-A", "SRV"), _decision(lp, "DG-B", "SRV")
    assert (a.action, a.new_name) == (RENAME_MOVE, "SRV-10.1.1.1")
    assert (b.action, b.new_name) == (CONSOLIDATE, "SRV-10.1.1.1")
    assert {(u["source_name"], u["new"]) for u in lp.plan.rule_updates} == {
        ("RA", "SRV-10.1.1.1"), ("RB", "SRV-10.1.1.1")}
    result, _ = _apply(original, lp)
    assert compare_rules(original, result.tree).ok
    assert len(result.tree.xpath("/config/shared/address/entry[@name='SRV-10.1.1.1']")) == 1


def test_existing_shared_object_with_same_new_name_and_value_is_reused(write):
    path = write(panorama({
        "DG-A": "<address>" + addr("SRV", "10.1.1.1") + "</address>" + rule("RA", "SRV"),
    }, shared="<address>" + addr("SRV", "10.9.9.9") + addr("SRV-10.1.1.1", "10.1.1.1") + "</address>"
              + rule("RS", "SRV-10.1.1.1", rulebase="pre-rulebase")))
    original, lp = _plan(path)
    d = _decision(lp, "DG-A", "SRV")
    assert (d.action, d.new_name) == (CONSOLIDATE, "SRV-10.1.1.1")
    result, _ = _apply(original, lp)
    assert compare_rules(original, result.tree).ok


# --- regra 3: descrição ------------------------------------------------------------

def _addr_desc(name, ip, desc=None):
    d = f"<description>{desc}</description>" if desc is not None else ""
    return f'<entry name="{name}"><ip-netmask>{ip}</ip-netmask>{d}</entry>'


def test_description_copied_only_when_shared_has_none(write):
    path = write(panorama({
        "DG-A": "<address>" + _addr_desc("A", "10.1.1.1", "local A") + _addr_desc("B", "10.1.1.2", "local B")
                + "</address>" + rule("RA", "A", "B"),
    }, shared="<address>" + _addr_desc("A", "10.1.1.1") + _addr_desc("B", "10.1.1.2", "shared B")
              + "</address>" + rule("RS", "A", "B", rulebase="pre-rulebase")))
    original, lp = _plan(path)
    assert _decision(lp, "DG-A", "A").action == CONSOLIDATE
    assert [(x["description"]) for x in lp.plan.descriptions] == ["local A"]
    result, _ = _apply(original, lp)
    t = result.tree
    assert t.xpath("/config/shared/address/entry[@name='A']/description/text()") == ["local A"]
    assert t.xpath("/config/shared/address/entry[@name='B']/description/text()") == ["shared B"]
    assert not t.xpath("//device-group//address/entry")
    assert compare_rules(original, t).ok


def test_unused_local_duplicate_of_kept_shared_object_is_consolidated(write):
    path = write(panorama({
        "DG-A": "<address>" + _addr_desc("A", "10.1.1.1", "só local") + "</address>",
    }, shared="<address>" + _addr_desc("A", "10.1.1.1") + "</address>"
              + rule("RS", "A", rulebase="pre-rulebase")))
    original, lp = _plan(path)
    assert _decision(lp, "shared", "A").action == KEEP
    assert _decision(lp, "DG-A", "A").action == CONSOLIDATE
    result, _ = _apply(original, lp)
    assert result.tree.xpath("/config/shared/address/entry[@name='A']/description/text()") == ["só local"]


# --- fase 0: membro preso a grupo de Shared -------------------------------------------

def _pinned(write):
    # Grupo G de Shared contém X; o DG-A tem X local com outro IP, então nos firewalls do
    # DG-A o G vale 10.1.1.1. DG-B usa o G de Shared (10.9.9.9).
    return write(panorama({
        "DG-A": "<address>" + addr("X", "10.1.1.1") + "</address>" + rule("RA", "G"),
        "DG-B": rule("RB", "G"),
    }, shared="<address>" + addr("X", "10.9.9.9") + "</address>"
              "<address-group><entry name=\"G\"><static><member>X</member></static></entry></address-group>"))


def test_phase0_frees_member_pinned_by_shared_group(write):
    original, lp = _plan(_pinned(write))
    assert lp.pairs == [("address-group", "G", "DG-A")]
    assert (_decision(lp, "DG-A", "X").action, _decision(lp, "DG-A", "X").new_name) == (RENAME_MOVE, "X-10.1.1.1")
    g = _decision(lp, "DG-A", "G")
    assert (g.action, g.new_name) == (RENAME_MOVE, "G_DG-A")
    assert all(v[3] == 0 for v in lp.plan.verification)

    result, ops = _apply(original, lp)
    t = result.tree
    assert not t.xpath("//device-group/entry/address/entry|//device-group/entry/address-group/entry")
    assert t.xpath("/config/shared/address-group/entry[@name='G_DG-A']/static/member/text()") == ["X-10.1.1.1"]
    assert t.xpath("/config/shared/address-group/entry[@name='G']/static/member/text()") == ["X"]
    assert compare_rules(original, t).ok
    assert [o["operation"] for o in ops if o["phase"] == 0] == ["CREATE_LOCAL_COPY"]


def test_phase0_on_original_equals_phases_1_3_on_base(write):
    from panorama_cleanup.policy_comparator import canonical
    original, lp = _plan(_pinned(write))
    full, _ = _apply(original, lp, phases=(0, 1, 2, 3))
    base, _ = _apply(original, lp, phases=(1, 2, 3))
    assert canonical(full.tree.getroot()) == canonical(base.tree.getroot())


def test_phase0_not_used_when_group_is_used_by_shared_rule(write):
    # A regra de Shared usa G em todos os DGs: não há como dar outro nome ao G só no DG-A
    # sem mexer na regra. O X local fica, e nada muda nas regras.
    path = write(panorama({
        "DG-A": "<address>" + addr("X", "10.1.1.1") + "</address>",
        "DG-B": "",
    }, shared="<address>" + addr("X", "10.9.9.9") + "</address>"
              "<address-group><entry name=\"G\"><static><member>X</member></static></entry></address-group>"
              + rule("RS", "G", rulebase="pre-rulebase")))
    original, lp = _plan(path)
    assert lp.pairs == []
    assert _decision(lp, "DG-A", "X").action not in (RENAME_MOVE, CONSOLIDATE)
    result, _ = _apply(original, lp)
    assert compare_rules(original, result.tree).ok


# --- verificador independente -----------------------------------------------------------

def test_rule_equivalence_detects_ip_port_and_app_changes(write):
    path = write(panorama({
        "DG-A": "<address>" + addr("X", "10.1.1.1") + "</address>"
                "<service>" + _svc("S", "8080") + "</service>"
                "<post-rulebase><security><rules><entry name=\"R\"><destination><member>X</member>"
                "</destination><service><member>S</member></service><application><member>ssh</member>"
                "</application></entry></rules></security></post-rulebase>",
    }))
    original = parse_backup(path)
    assert compare_rules(original, copy.deepcopy(original)).ok
    for xpath, value in (("//address/entry[@name='X']/ip-netmask", "10.1.1.2"),
                         ("//service/entry[@name='S']//port", "8081"),
                         ("//rules/entry[@name='R']/application/member", "web-browsing")):
        changed = copy.deepcopy(original)
        changed.xpath(xpath)[0].text = value
        r = compare_rules(original, changed)
        assert not r.ok and len(r.differences) == 1
