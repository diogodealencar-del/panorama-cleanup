import pytest

from panorama_cleanup.analysis import run_analysis
from panorama_cleanup.dependency_analyzer import (
    Resolver, analyze_dependencies, check_precedence_setting, parse_tag_filter,
)
from panorama_cleanup.errors import AnalysisError
from panorama_cleanup.inventory import build_inventory
from panorama_cleanup.xml_reader import parse_backup
from tests.conftest import FIXTURES


@pytest.fixture(scope="module")
def an():
    return run_analysis(parse_backup(FIXTURES / "panorama_deps.xml"))


def refs(an, **filters):
    out = []
    for r in an.graph.references:
        target = an.inventory.records[r.target] if r.target >= 0 else None
        values = {**r.__dict__, "target_scope": target and target.scope,
                  "target_name": target and target.name,
                  "target_type": target and target.object_type}
        if all(values[k] == v for k, v in filters.items()):
            out.append(r)
    return out


def test_shared_rule_resolves_in_every_descendant_context(an):
    found = refs(an, source_name="SH-R1", value="SERVER")
    by_context = {r.context: an.inventory.records[r.target].scope for r in found}
    assert by_context == {"shared": "shared", "DG-RJ": "DG-RJ", "DG-BH": "DG-BH",
                          "DG-BH-FILIAL": "DG-BH"}
    assert {r.confidence for r in found} == {"confirmed"}


def test_dg_object_shadows_shared(an):
    (ref,) = refs(an, source_name="RJ-R1", value="SHADOW")
    assert an.inventory.records[ref.target].scope == "DG-RJ"


def test_inherited_from_parent_dg(an):
    (ref,) = refs(an, source_name="FIL-AO", value="BH-OBJ")
    assert (an.inventory.records[ref.target].scope, ref.confidence) == ("DG-BH", "confirmed")


def test_name_only_in_descendant(an):
    found = {r.context: r for r in refs(an, source_name="BH-R1", value="FIL-ONLY")}
    assert found["DG-BH"].resolution == "unresolved"
    assert found["DG-BH-FILIAL"].confidence == "confirmed"


def test_sibling_dg_not_visible(an):
    (ref,) = refs(an, source_name="RJ-R1", value="BH-ONLY")
    assert ref.target == -1 and ref.resolution == "unresolved"


def test_ambiguous_name_is_possible_for_both_types(an):
    found = refs(an, source_name="RJ-R1", value="AMBIG")
    assert {an.inventory.records[r.target].object_type for r in found} == {"address", "address-group"}
    assert {(r.confidence, r.resolution) for r in found} == {("possible", "ambiguous")}


def test_literal_ip_and_predefined_app(an):
    (literal,) = refs(an, source_name="RJ-R1", value="10.20.30.40")
    assert literal.resolution == "literal"
    (app,) = refs(an, source_name="RJ-R1", value="ssl")
    assert app.resolution == "unresolved"


def test_keywords_and_non_reference_fields_ignored(an):
    values = {r.value for r in refs(an, source_name="RJ-R1")}
    assert not {"any", "application-default", "trust", "untrust", "allow"} & values
    assert not refs(an, source_name="RJ-NAT1", value="ethernet1/1")
    assert not refs(an, source_name="RJ-NAT1", value="8080")


def test_nat_translated_address(an):
    (ref,) = refs(an, source_name="RJ-NAT1", value="NAT-TARGET")
    assert ref.field == "destination-translation/translated-address"
    assert ref.confidence == "confirmed"


def test_unmapped_field_is_only_possible(an):
    (ref,) = refs(an, source_name="RJ-PBF1", value="NEXTHOP-OBJ")
    assert (ref.confidence, ref.resolution) == ("possible", "unmapped-field")
    assert any("nexthop/ip-address" in w for w in an.warnings)


def test_profile_and_settings_fields(an):
    targets = {(r.field, an.inventory.records[r.target].name)
               for r in refs(an, source_kind="rule", confidence="confirmed")}
    assert {
        ("profile-setting/group", "PG-1"), ("log-setting", "LFP-1"), ("schedule", "SCH-1"),
        ("profile-setting/profiles/spyware", "AS-DIRECT"), ("category", "CUSTOM-CAT"),
        ("profile-setting/profiles/url-filtering", "URL-1"), ("destination", "EDL-1"),
    } <= targets


def test_disabled_rule_flagged(an):
    (ref,) = refs(an, source_name="RJ-R2", value="DUPE")
    assert ref.rule_disabled is True


def test_shared_group_member_override_is_effective(an):
    # Só a cópia do DG chega aos firewalls do DG (KB kA10g000000ClvoCAC).
    found = refs(an, source_name="GRP-SHARED-OVR", value="OVR-MEMBER")
    by_context = {r.context: (an.inventory.records[r.target].scope, r.confidence, r.resolution)
                  for r in found}
    assert by_context["shared"] == ("shared", "confirmed", "resolved")
    assert by_context["DG-RJ"] == ("DG-RJ", "confirmed", "override")
    assert by_context["DG-BH"] == ("shared", "confirmed", "resolved")


def test_dynamic_group_membership_and_tag(an):
    members = refs(an, source_name="GRP-DYN", resolution="dynamic-membership",
                   confidence="confirmed")
    assert {an.inventory.records[r.target].name for r in members} == {"TAGGED-1"}
    tags = refs(an, source_name="GRP-DYN", field="dynamic/filter", target_type="tag")
    assert {an.inventory.records[r.target].name for r in tags} == {"PROD"}


def test_profile_fallback_is_possible(an):
    found = refs(an, source_name="URL-1", value="CUSTOM-CAT2")
    assert found and {r.confidence for r in found} == {"possible"}


def test_template_text_not_matching_objects_produces_nothing(an):
    assert not refs(an, source_kind="template")


def test_template_text_matching_object_is_possible(tmp_path):
    path = tmp_path / "t.xml"
    path.write_text("""<config version="11.1.0"><devices><entry name="localhost.localdomain">
      <template><entry name="T1"><config><x><peer>GW-OBJ</peer></x></config></entry></template>
      </entry></devices><shared><address><entry name="GW-OBJ"><ip-netmask>1.2.3.4</ip-netmask></entry>
      </address></shared></config>""", encoding="utf-8")
    an = run_analysis(parse_backup(path))
    (ref,) = [r for r in an.graph.references if r.source_kind == "template"]
    assert (ref.confidence, ref.resolution) == ("possible", "template-match")
    assert an.usage[0].status == "UNKNOWN"


@pytest.mark.parametrize("expression,tags,expected", [
    ("'a'", {"a"}, True),
    ("'a' and 'b'", {"a"}, False),
    ("'a' or 'b'", {"b"}, True),
    ("'a' and not 'b'", {"a", "b"}, False),
    ("('a' or 'b') and 'c'", {"b", "c"}, True),
    ("'tag com espaço' or x", {"x"}, True),
    ('"dq"', {"dq"}, True),
])
def test_tag_filter(expression, tags, expected):
    predicate, _ = parse_tag_filter(expression)
    assert predicate(tags) is expected


@pytest.mark.parametrize("expression", ["", "'a' and", "('a'", "'a' 'b'", "and 'a'"])
def test_tag_filter_invalid(expression):
    with pytest.raises(ValueError):
        parse_tag_filter(expression)


def test_invalid_dynamic_filter_degrades_to_possible(tmp_path):
    path = tmp_path / "d.xml"
    path.write_text("""<config version="11.1.0"><devices><entry name="localhost.localdomain"/></devices>
      <shared><tag><entry name="T"/></tag>
      <address><entry name="A"><ip-netmask>1.1.1.1</ip-netmask><tag><member>T</member></tag></entry></address>
      <address-group><entry name="G"><dynamic><filter>'T' and</filter></dynamic></entry></address-group>
      <pre-rulebase><security><rules><entry name="R"><destination><member>G</member></destination></entry>
      </rules></security></pre-rulebase></shared></config>""", encoding="utf-8")
    an = run_analysis(parse_backup(path))
    status = {r.name: an.usage[i].status for i, r in enumerate(an.inventory.records)}
    assert status == {"G": "USED", "A": "UNKNOWN", "T": "UNKNOWN"}
    assert any("não interpretado" in w for w in an.warnings)


def test_unknown_hierarchy(tmp_path):
    path = tmp_path / "h.xml"
    path.write_text("""<config version="11.1.0"><devices><entry name="localhost.localdomain">
      <device-group><entry name="DG-X">
        <address><entry name="LOCAL"><ip-netmask>1.1.1.1</ip-netmask></entry></address>
        <post-rulebase><security><rules><entry name="R"><destination><member>S</member></destination>
        </entry></rules></security></post-rulebase>
      </entry></device-group></entry></devices>
      <shared><address><entry name="S"><ip-netmask>2.2.2.2</ip-netmask></entry></address></shared>
      </config>""", encoding="utf-8")
    an = run_analysis(parse_backup(path))
    status = {(r.scope, r.name): an.usage[i] for i, r in enumerate(an.inventory.records)}
    assert status[("shared", "S")].status == "UNKNOWN"
    assert status[("DG-X", "LOCAL")].status == "UNKNOWN"
    assert status[("DG-X", "LOCAL")].reason == "hierarquia do Device Group desconhecida"


def test_precedence_setting_stops_analysis(tmp_path):
    path = tmp_path / "p.xml"
    path.write_text("""<config version="11.1.0"><devices><entry name="localhost.localdomain">
      <deviceconfig><setting><management><shared-objects-take-precedence>yes</shared-objects-take-precedence>
      </management></setting></deviceconfig></entry></devices><shared/></config>""", encoding="utf-8")
    tree = parse_backup(path)
    with pytest.raises(AnalysisError, match="precedência"):
        analyze_dependencies(tree, build_inventory(tree))


def test_precedence_explicit_no_is_default(tmp_path):
    path = tmp_path / "p.xml"
    path.write_text("""<config version="11.1.0"><devices><entry name="localhost.localdomain">
      <deviceconfig><x-precedence>no</x-precedence></deviceconfig></entry></devices><shared/></config>""",
                    encoding="utf-8")
    check_precedence_setting(parse_backup(path))


def test_resolver_chain_and_descendants(an):
    resolver = Resolver(an.inventory)
    assert resolver.chain("DG-BH-FILIAL") == (["DG-BH-FILIAL", "DG-BH", "shared"], True)
    assert resolver.descendants("DG-BH") == ["DG-BH-FILIAL"]
    assert sorted(resolver.descendants("shared")) == ["DG-BH", "DG-BH-FILIAL", "DG-RJ"]


def test_analysis_does_not_modify_xml():
    path = FIXTURES / "panorama_deps.xml"
    before = path.read_bytes()
    run_analysis(parse_backup(path))
    assert path.read_bytes() == before
