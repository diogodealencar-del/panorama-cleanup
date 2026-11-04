import pytest

from panorama_cleanup.analysis import run_analysis
from panorama_cleanup.duplicate_analyzer import normalize_address
from panorama_cleanup.xml_reader import parse_backup
from tests.conftest import FIXTURES


@pytest.fixture(scope="module")
def an():
    return run_analysis(parse_backup(FIXTURES / "panorama_deps.xml"))


def status(an, scope, name, object_type=None):
    for i, r in enumerate(an.inventory.records):
        if (r.scope, r.name) == (scope, name) and object_type in (None, r.object_type):
            return an.duplicates.name_status[i]
    raise KeyError((scope, name))


def test_case_b_same_name_different_value_is_conflict(an):
    clusters = {s: status(an, s, "SERVER").cluster for s in ("shared", "DG-RJ", "DG-BH")}
    assert {status(an, s, "SERVER").status for s in clusters} == {"CONFLICT"}
    assert clusters["shared"] == clusters["DG-RJ"] != clusters["DG-BH"]
    assert status(an, "DG-BH", "SERVER").clusters == 2


def test_case_a_same_name_same_value_is_duplicate_with_normalization(an):
    rj, bh = status(an, "DG-RJ", "DUPE"), status(an, "DG-BH", "DUPE")
    assert rj.status == bh.status == "DUPLICATE"
    assert rj.literal_equal is False  # 10.1.1.1 vs 10.1.1.1/32


def test_duplicate_with_different_tags_flagged(an):
    rj = status(an, "DG-RJ", "TAGDIFF")
    assert rj.status == "DUPLICATE" and rj.tags_equal is False


def test_groups_with_same_member_names_but_different_values_conflict(an):
    assert status(an, "DG-RJ", "GRP-X").status == "CONFLICT"
    assert status(an, "DG-RJ", "GRP-X").cluster != status(an, "DG-BH", "GRP-X").cluster


def test_same_name_different_types_conflict(an):
    assert status(an, "shared", "MIXED", "address").status == "CONFLICT"
    assert status(an, "DG-RJ", "MIXED", "address-group").status == "CONFLICT"


def test_unique_names(an):
    assert status(an, "shared", "ORPHAN").status == "UNIQUE"


def test_case_c_different_names_same_value(an):
    groups = {gid: sorted((an.inventory.records[i].scope, an.inventory.records[i].name)
                          for i in idx)
              for gid, _, idx in an.duplicates.possible_groups}
    assert sorted(groups.values()) == [
        [("DG-RJ", "AMBIG"), ("DG-RJ", "MIXED")],
        [("DG-RJ", "WEB-B"), ("shared", "WEB-A")],
    ]


def test_case_c_not_marked_as_name_duplicate(an):
    assert status(an, "shared", "WEB-A").status == "UNIQUE"


class _Rec:
    def __init__(self, value):
        self.value = value


@pytest.mark.parametrize("a,b,equal", [
    ("ip-netmask=10.0.0.1", "ip-netmask=10.0.0.1/32", True),
    ("ip-netmask=2001:db8::1", "ip-netmask=2001:db8::1/128", True),
    ("ip-netmask=10.0.0.1/24", "ip-netmask=10.0.0.0/24", False),
    ("fqdn=WWW.Example.com", "fqdn=www.example.com", True),
    ("ip-range=10.0.0.1-10.0.0.5", "ip-range=10.0.0.1-10.0.0.5", True),
    ("ip-netmask=10.0.0.1", "ip-range=10.0.0.1-10.0.0.1", False),
])
def test_address_normalization_is_conservative(a, b, equal):
    assert (normalize_address(_Rec(a)) == normalize_address(_Rec(b))) is equal


def index_of(an, scope, name):
    return next(i for i, r in enumerate(an.inventory.records) if (r.scope, r.name) == (scope, name))


def test_shared_group_value_varies_where_member_is_overridden(an):
    variants = an.duplicates.context_variants
    assert variants[index_of(an, "shared", "GRP-SHARED-OVR")] == ("DG-RJ",)
    assert index_of(an, "shared", "GRP-NESTED-OUTER") not in variants
    assert len(variants) == 1


def test_dynamic_group_varies_when_dg_has_tagged_address(tmp_path):
    from panorama_cleanup.analysis import run_analysis

    path = tmp_path / "v.xml"
    path.write_text("""<config version="11.1.0"><devices><entry name="localhost.localdomain">
      <device-group><entry name="DG-A"><address>
        <entry name="LOCAL-PROD"><ip-netmask>9.9.9.9</ip-netmask><tag><member>PROD</member></tag></entry>
      </address></entry></device-group></entry></devices>
      <readonly><devices><entry name="localhost.localdomain"><device-group><entry name="DG-A"/>
      </device-group></entry></devices></readonly>
      <shared><tag><entry name="PROD"/></tag>
      <address><entry name="S-PROD"><ip-netmask>1.1.1.1</ip-netmask><tag><member>PROD</member></tag></entry></address>
      <address-group><entry name="DYN"><dynamic><filter>'PROD'</filter></dynamic></entry></address-group>
      </shared></config>""", encoding="utf-8")
    an = run_analysis(parse_backup(path))
    assert an.duplicates.context_variants[index_of(an, "shared", "DYN")] == ("DG-A",)
