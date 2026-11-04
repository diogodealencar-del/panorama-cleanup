import pytest

from panorama_cleanup.errors import XmlReadError
from panorama_cleanup.xml_reader import (
    SHARED, UNKNOWN, parse_backup, read_device_groups, read_metadata,
)
from tests.conftest import FIXTURES


def write(tmp_path, text, name="x.xml"):
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return path


def config(device_groups="", readonly="", extra_devices=""):
    return f"""<?xml version="1.0"?>
<config version="10.2.0">
  <devices>
    <entry name="localhost.localdomain"><device-group>{device_groups}</device-group></entry>
    {extra_devices}
  </devices>
  <readonly><devices><entry name="localhost.localdomain"><device-group>{readonly}</device-group></entry></devices></readonly>
  <shared/>
</config>"""


def test_metadata():
    m = read_metadata(parse_backup(FIXTURES / "panorama_basic.xml"))
    assert (m.config_version, m.detail_version, m.urldb) == ("11.1.0", "11.1.10", "paloaltonetworks")
    assert m.panorama_entry == "localhost.localdomain"
    assert m.managed_devices == ("000000000001", "000000000002", "000000000003")
    assert m.templates == ("T-RJ", "T-BH")
    assert m.template_stacks == ("S-RJ",)


def test_hierarchy():
    groups, warnings = read_device_groups(parse_backup(FIXTURES / "panorama_basic.xml"))
    by_name = {g.name: g for g in groups}
    assert warnings == []
    assert [g.name for g in groups] == ["DG-RJ", "DG-BH", "DG-BH-FILIAL"]
    assert by_name["DG-RJ"].parent == SHARED and by_name["DG-RJ"].depth == 1
    assert by_name["DG-BH-FILIAL"].parent == "DG-BH"
    assert by_name["DG-BH-FILIAL"].path == (SHARED, "DG-BH", "DG-BH-FILIAL")
    assert by_name["DG-BH-FILIAL"].depth == 2
    assert by_name["DG-RJ"].devices == ("000000000001",)
    assert by_name["DG-RJ"].reference_templates == ("S-RJ",)
    assert by_name["DG-BH"].readonly_id == "12"


def test_dg_missing_from_readonly_is_unknown(tmp_path):
    path = write(tmp_path, config('<entry name="A"/>', ""))
    groups, warnings = read_device_groups(parse_backup(path))
    assert groups[0].parent == UNKNOWN
    assert groups[0].path[0] == UNKNOWN
    assert any("sem entrada em readonly" in w for w in warnings)


def test_nonexistent_parent_is_unknown(tmp_path):
    path = write(tmp_path, config(
        '<entry name="A"/>', '<entry name="A"><parent-dg>FANTASMA</parent-dg></entry>'))
    groups, warnings = read_device_groups(parse_backup(path))
    assert groups[0].parent == UNKNOWN
    assert any("inexistente" in w for w in warnings)


def test_cycle_is_unknown(tmp_path):
    path = write(tmp_path, config(
        '<entry name="A"/><entry name="B"/>',
        '<entry name="A"><parent-dg>B</parent-dg></entry>'
        '<entry name="B"><parent-dg>A</parent-dg></entry>'))
    groups, warnings = read_device_groups(parse_backup(path))
    assert all(g.parent == UNKNOWN for g in groups)
    assert any("ciclo" in w for w in warnings)


def test_readonly_only_dg_is_reported(tmp_path):
    path = write(tmp_path, config("", '<entry name="ORFAO"/>'))
    groups, warnings = read_device_groups(parse_backup(path))
    assert groups == []
    assert any("ORFAO" in w for w in warnings)


def test_multiple_panorama_entries_refused(tmp_path):
    path = write(tmp_path, config(extra_devices='<entry name="outro"/>'))
    with pytest.raises(XmlReadError, match="exatamente um"):
        read_device_groups(parse_backup(path))


def test_missing_version_refused(tmp_path):
    path = write(tmp_path, '<config><devices><entry name="x"/></devices></config>')
    with pytest.raises(XmlReadError, match="version"):
        read_metadata(parse_backup(path))


@pytest.mark.parametrize("payload", [
    '<!DOCTYPE config [<!ENTITY x "boom">]><config version="1">&x;</config>',
    '<!DOCTYPE config SYSTEM "file:///etc/passwd"><config version="1"/>',
    '<!DOCTYPE config [<!ENTITY a "a"><!ENTITY b "&a;&a;&a;">]><config version="1">&b;</config>',
])
def test_unsafe_xml_refused(tmp_path, payload):
    with pytest.raises(XmlReadError, match="insegura"):
        parse_backup(write(tmp_path, payload))


def test_malformed_xml_refused(tmp_path):
    with pytest.raises(XmlReadError, match="malformado"):
        parse_backup(write(tmp_path, "<config version='1'><shared></config>"))


def test_wrong_root_refused(tmp_path):
    with pytest.raises(XmlReadError, match="Raiz inesperada"):
        parse_backup(write(tmp_path, "<response/>"))


def test_parse_does_not_modify_file():
    path = FIXTURES / "panorama_basic.xml"
    before = path.read_bytes()
    parse_backup(path)
    assert path.read_bytes() == before
