from collections import Counter

import pytest

from panorama_cleanup.inventory import build_inventory
from panorama_cleanup.xml_reader import parse_backup
from tests.conftest import FIXTURES

EXPECTED = {
    ("shared", "log-settings/profiles"): 1,
    ("shared", "tag"): 2,
    ("shared", "address"): 4,
    ("shared", "address-group"): 2,
    ("shared", "service"): 2,
    ("shared", "service-group"): 1,
    ("shared", "application"): 1,
    ("shared", "schedule"): 1,
    ("shared", "external-list"): 1,
    ("shared", "profile-group"): 1,
    ("shared", "profiles/virus"): 1,
    ("shared", "profiles/spyware"): 2,
    ("shared", "admin-role"): 1,
    ("DG-RJ", "address"): 2,
    ("DG-RJ", "address-group"): 1,
    ("DG-RJ", "service"): 1,
    ("DG-BH", "tag"): 1,
    ("DG-BH", "address"): 1,
    ("DG-BH-FILIAL", "address"): 1,
    ("DG-BH-FILIAL", "application-group"): 1,
    ("DG-BH-FILIAL", "application-filter"): 1,
}


@pytest.fixture(scope="module")
def inv():
    return build_inventory(parse_backup(FIXTURES / "panorama_basic.xml"))


def find(inv, scope, object_type, name):
    matches = [r for r in inv.records
               if (r.scope, r.object_type, r.name) == (scope, object_type, name)]
    assert len(matches) == 1
    return matches[0]


def test_exact_counts(inv):
    assert dict(inv.counts()) == EXPECTED
    assert len(inv.records) == sum(EXPECTED.values()) == 29


def test_reconciliation_matches(inv):
    assert inv.reconciliation
    assert all(xml_count == count for _, _, xml_count, count in inv.reconciliation)


def test_rules_and_non_object_sections_not_inventoried(inv):
    names = {r.name for r in inv.records}
    assert not {"R1", "SHARED-R1", "000000000001", "S-RJ"} & names


def test_readonly_entries_not_inventoried(inv):
    assert all("readonly" not in r.xpath for r in inv.records)
    assert Counter(r.name for r in inv.records)["GRP-ALL"] == 1


def test_same_name_different_scope_kept_separately(inv):
    rj = find(inv, "DG-RJ", "address", "SERVER")
    bh = find(inv, "DG-BH", "address", "SERVER")
    shared = find(inv, "shared", "address", "SERVER")
    assert rj.value == "ip-netmask=192.168.10.10"
    assert bh.value == "ip-netmask=192.168.20.10"
    assert rj.value_sha256 == shared.value_sha256 != bh.value_sha256


def test_value_excludes_metadata(inv):
    r = find(inv, "shared", "address", "NET-RANGE")
    assert r.value == "ip-range=10.1.1.1-10.1.1.20"
    assert r.description == "faixa"
    assert r.tags == ("PROD",)
    bh = find(inv, "DG-BH", "address", "SERVER")
    rj = find(inv, "DG-RJ", "address", "SERVER")
    assert bh.tags == ("BH",)
    assert rj.tags == ()


def test_group_members(inv):
    assert find(inv, "shared", "address-group", "GRP-ALL").members == ("SERVER", "SITE")
    assert find(inv, "DG-RJ", "address-group", "GRP-RJ").members == ("SERVER", "DNS")
    assert find(inv, "shared", "service-group", "SG-WEB").members == ("TCP-443",)
    assert find(inv, "DG-BH-FILIAL", "application-group", "APPS-WEB").members == ("web-browsing", "ssl")
    dyn = find(inv, "shared", "address-group", "GRP-DYN")
    assert dyn.members == () and dyn.value == "dynamic/filter='PROD'"


def test_service_value(inv):
    assert find(inv, "shared", "service", "TCP-443").value == (
        "protocol/tcp/port=443 | protocol/tcp/override/no"
    )


def test_nested_entries_keep_their_names(inv):
    value = find(inv, "shared", "log-settings/profiles", "LFP-DEFAULT").value
    assert "match-list/entry[@name='Traffic']/log-type=traffic" in value
    assert "match-list/entry[@name='Threat']/log-type=threat" in value


def test_unrecognized_container_inventoried_and_warned(inv):
    role = find(inv, "shared", "admin-role", "ROLE-X")
    assert role.recognized is False
    assert any("admin-role" in w for w in inv.warnings)
    assert len(inv.warnings) == 1


def test_xpath_resolves_to_the_same_element(inv):
    tree = parse_backup(FIXTURES / "panorama_basic.xml")
    for r in inv.records:
        found = tree.xpath(r.xpath)
        assert len(found) == 1, r.xpath
        assert found[0].get("name") == r.name
        assert found[0].sourceline == r.source_line


def test_names_with_special_characters(inv):
    assert find(inv, "DG-BH-FILIAL", "address", "WEB FILIAL/01").value == "fqdn=web.filial.example"
    assert find(inv, "shared", "address", "O'BRIEN").xpath.endswith(
        """entry[@name="O'BRIEN"]""")


def test_duplicate_name_in_same_scope_warned(tmp_path):
    path = tmp_path / "dup.xml"
    path.write_text("""<config version="11.1.0">
      <devices><entry name="localhost.localdomain"/></devices>
      <shared><address>
        <entry name="A"><ip-netmask>1.1.1.1</ip-netmask></entry>
        <entry name="A"><ip-netmask>2.2.2.2</ip-netmask></entry>
      </address></shared></config>""", encoding="utf-8")
    inv = build_inventory(parse_backup(path))
    assert len(inv.records) == 2
    assert any("repetido 2x" in w for w in inv.warnings)
