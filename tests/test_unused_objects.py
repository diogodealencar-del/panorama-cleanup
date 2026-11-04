import pytest

from panorama_cleanup.analysis import run_analysis
from panorama_cleanup.xml_reader import parse_backup
from tests.conftest import FIXTURES

NONE, ONLY_UNUSED = "nenhuma referência", "referenciado apenas por objetos não utilizados"
POSSIBLE = "alcançável apenas por referência possível"

EXPECTED = {
    ("shared", "PROD"): ("USED", "referência indireta"),
    ("shared", "UNUSED-TAG"): ("UNUSED", NONE),
    ("shared", "ON-ORPHAN"): ("UNUSED", ONLY_UNUSED),
    ("shared", "SERVER"): ("USED", "referência direta"),
    ("shared", "SHADOW"): ("UNUSED", NONE),
    ("shared", "PARENT-OBJ"): ("USED", "referência direta"),
    ("shared", "DNS-SHARED"): ("USED", "referência direta"),
    ("shared", "ORPHAN"): ("UNUSED", NONE),
    ("shared", "IN-UNUSED-GRP"): ("UNUSED", ONLY_UNUSED),
    ("shared", "NESTED-LEAF"): ("USED", "referência indireta"),
    ("shared", "TAGGED-1"): ("USED", "referência indireta"),
    ("shared", "WEB-A"): ("UNUSED", NONE),
    ("shared", "MIXED"): ("UNUSED", NONE),
    ("shared", "OVR-MEMBER"): ("USED", "referência indireta"),
    ("shared", "GRP-UNUSED"): ("UNUSED", NONE),
    ("shared", "GRP-NESTED-INNER"): ("USED", "referência indireta"),
    ("shared", "GRP-NESTED-OUTER"): ("USED", "referência direta"),
    ("shared", "GRP-DYN"): ("USED", "referência direta"),
    ("shared", "GRP-SHARED-OVR"): ("USED", "referência direta"),
    ("shared", "TCP-443"): ("USED", "referência direta"),
    ("shared", "TCP-8443"): ("USED", "referência indireta"),
    ("shared", "UDP-UNUSED"): ("UNUSED", NONE),
    ("shared", "SG-WEB"): ("USED", "referência direta"),
    ("shared", "SCH-1"): ("USED", "referência direta"),
    ("shared", "EDL-1"): ("USED", "referência direta"),
    ("shared", "PG-1"): ("USED", "referência direta"),
    ("shared", "LFP-1"): ("USED", "referência direta"),
    ("shared", "AV-1"): ("USED", "referência indireta"),
    ("shared", "AS-DIRECT"): ("USED", "referência direta"),
    ("shared", "URL-1"): ("USED", "referência direta"),
    ("shared", "CUSTOM-CAT"): ("USED", "referência direta"),
    ("shared", "CUSTOM-CAT2"): ("UNKNOWN", POSSIBLE),
    ("shared", "ROLE-X"): ("UNKNOWN", "tipo de objeto não reconhecido"),
    ("DG-RJ", "RJ-TAG"): ("UNUSED", ONLY_UNUSED),
    ("DG-RJ", "SERVER"): ("USED", "referência direta"),
    ("DG-RJ", "SHADOW"): ("USED", "referência direta"),
    ("DG-RJ", "NAT-TARGET"): ("USED", "referência direta"),
    ("DG-RJ", "DUPE"): ("USED", "referência direta"),
    ("DG-RJ", "TAGDIFF"): ("UNUSED", NONE),
    ("DG-RJ", "WEB-B"): ("UNUSED", NONE),
    ("DG-RJ", "OVR-MEMBER"): ("USED", "referência indireta"),
    ("DG-RJ", "NEXTHOP-OBJ"): ("UNKNOWN", POSSIBLE),
    ("DG-RJ", "MIXED"): ("UNUSED", NONE),
    ("DG-RJ", "GRP-X"): ("USED", "referência direta"),
    ("DG-BH", "SERVER"): ("USED", "referência direta"),
    ("DG-BH", "DUPE"): ("UNUSED", NONE),
    ("DG-BH", "TAGDIFF"): ("USED", "referência direta"),
    ("DG-BH", "BH-ONLY"): ("UNUSED", NONE),
    ("DG-BH", "BH-OBJ"): ("USED", "referência direta"),
    ("DG-BH", "GRP-X"): ("UNUSED", NONE),
    ("DG-BH-FILIAL", "FIL-ONLY"): ("USED", "referência direta"),
    ("DG-BH-FILIAL", "APPS-WEB"): ("USED", "referência direta"),
    ("DG-BH-FILIAL", "FILTER-1"): ("USED", "referência direta"),
}


@pytest.fixture(scope="module")
def an():
    return run_analysis(parse_backup(FIXTURES / "panorama_deps.xml"))


def usage_of(an):
    out = {}
    for i, r in enumerate(an.inventory.records):
        if r.name == "AMBIG":
            continue
        out[(r.scope, r.name)] = (an.usage[i].status, an.usage[i].reason)
    return out


def test_every_object_classified_as_expected(an):
    assert usage_of(an) == EXPECTED


def test_ambiguous_objects_unknown(an):
    ambig = [an.usage[i] for i, r in enumerate(an.inventory.records) if r.name == "AMBIG"]
    assert [u.status for u in ambig] == ["UNKNOWN", "UNKNOWN"]


def test_member_of_reported_before_deletion(an):
    idx = next(i for i, r in enumerate(an.inventory.records) if r.name == "IN-UNUSED-GRP")
    assert an.usage[idx].member_of == ("shared:address-group:GRP-UNUSED",)
    assert an.usage[idx].inbound_object_refs == 1


def test_evidence_chain_for_indirect(an):
    idx = next(i for i, r in enumerate(an.inventory.records) if r.name == "NESTED-LEAF")
    evidence = an.usage[idx].evidence
    assert evidence.startswith("rule DG-RJ:post-rulebase/security:RJ-R1 [destination]")
    assert evidence.endswith("shared:address-group:GRP-NESTED-INNER")


def test_used_via_disabled_rule_still_used(an):
    idx = next(i for i, r in enumerate(an.inventory.records)
               if (r.scope, r.name) == ("DG-RJ", "DUPE"))
    assert an.usage[idx].status == "USED" and an.usage[idx].direct_rule_refs == 1


def test_unknown_never_unused(an):
    for i, r in enumerate(an.inventory.records):
        if not r.recognized:
            assert an.usage[i].status == "UNKNOWN"
