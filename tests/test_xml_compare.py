"""Modo COMPARE: relatório de diferenças entre dois XMLs, sem plano (XMLs sintéticos)."""

import csv
import json
from io import StringIO

from lxml import etree
from xml.sax.saxutils import escape

from panorama_cleanup.cli import main
from panorama_cleanup.xml_compare import VERDICT_CLEAN, VERDICT_DIFF, compare_trees


RO = ("<readonly><devices><entry name='localhost.localdomain'><device-group>"
      "<entry name='DG-A'><id>11</id></entry></device-group></entry></devices></readonly>")


def make(shared_objects="", dg_objects="", rules=None, extra="", readonly=RO):
    rules = rules if rules is not None else [
        ("R1", "11111111-1111-1111-1111-111111111111", "SRV", "svc-web"),
        ("R2", "22222222-2222-2222-2222-222222222222", "SRV", "svc-web"),
    ]
    rule_xml = "".join(
        f'<entry name="{n}" uuid="{u}"><source><member>{s}</member></source>'
        f"<service><member>{v}</member></service><action>allow</action></entry>"
        for n, u, s, v in rules)
    xml = f"""<config version="11.1.0">
  <mgt-config><devices><entry name="000000000001"/></devices></mgt-config>
  <devices><entry name="localhost.localdomain">
    <device-group>
      <entry name="DG-A">
        {dg_objects}
        <post-rulebase><security><rules>{rule_xml}</rules></security></post-rulebase>
      </entry>
    </device-group>
  </entry></devices>
  <shared>{shared_objects}</shared>
  {readonly}{extra}
</config>"""
    return etree.ElementTree(etree.fromstring(xml.encode()))


ADDR = '<address><entry name="{n}"><ip-netmask>{v}</ip-netmask>{d}</entry></address>'
SVC = ('<service><entry name="svc-web"><protocol><tcp><port>80</port></tcp></protocol>'
       "</entry></service>")


def addr(name, value, desc=""):
    d = f"<description>{escape(desc)}</description>" if desc else ""
    return ADDR.format(n=name, v=value, d=d)


BASE_DG = addr("SRV", "10.0.0.1") + SVC


def test_same_file_has_no_differences():
    c = compare_trees(make(dg_objects=BASE_DG), make(dg_objects=BASE_DG))
    assert c.verdict == VERDICT_CLEAN
    assert c.summary["field_changes"] == 0 and c.summary["rules_identical"] == 2
    assert not c.attention and c.summary["objects_added"] == c.summary["objects_removed"] == 0


def test_equivalent_rename_is_low_risk_with_equal_effective_value():
    # SRV local passa a SRV-1 em Shared, mesmo valor, e as regras trocam o nome
    mod = make(shared_objects=addr("SRV-1", "10.0.0.1"), dg_objects=SVC,
               rules=[("R1", "11111111-1111-1111-1111-111111111111", "SRV-1", "svc-web"),
                      ("R2", "22222222-2222-2222-2222-222222222222", "SRV-1", "svc-web")])
    c = compare_trees(make(dg_objects=BASE_DG), mod)
    assert c.summary["field_changes"] == 2
    assert {x["risk"] for x in c.rule_changes} == {"BAIXO"}
    assert all(x["effective"] == "igual em todos os contextos" for x in c.rule_changes)
    assert c.verdict == VERDICT_CLEAN
    removed = [o for o in c.object_changes if o["change"] == "Removido"]
    assert removed and "10.0.0.1" in removed[0]["value_original"]
    assert "SRV-1 (mesmo valor)" in removed[0]["visible_equivalent"]


def test_rename_that_changes_value_is_high_risk():
    mod = make(shared_objects=addr("SRV-1", "10.9.9.9"), dg_objects=SVC,
               rules=[("R1", "11111111-1111-1111-1111-111111111111", "SRV-1", "svc-web"),
                      ("R2", "22222222-2222-2222-2222-222222222222", "SRV", "svc-web")])
    c = compare_trees(make(dg_objects=BASE_DG), mod)
    changed = [x for x in c.rule_changes if x["field"] == "source"]
    assert any(x["risk"] == "ALTO" and x["effective"].startswith("DIFERE") for x in changed)
    assert c.verdict == VERDICT_DIFF and c.summary["field_changes_effective_differ"] >= 1


def test_non_reference_field_change_is_flagged():
    mod = make(dg_objects=BASE_DG)
    mod.getroot().find(".//entry[@name='R1']/action").text = "deny"
    c = compare_trees(make(dg_objects=BASE_DG), mod)
    ch = [x for x in c.rule_changes if x["field"] == "action"]
    assert ch and ch[0]["risk"] == "ALTO" and ch[0]["kind"] == "campo sem referência a objeto"
    assert c.verdict == VERDICT_DIFF


def test_added_removed_renamed_and_reordered_rules():
    r1 = ("R1", "11111111-1111-1111-1111-111111111111", "SRV", "svc-web")
    r2 = ("R2", "22222222-2222-2222-2222-222222222222", "SRV", "svc-web")
    r3 = ("R3", "33333333-3333-3333-3333-333333333333", "SRV", "svc-web")
    base = make(dg_objects=BASE_DG, rules=[r1, r2])
    removed = compare_trees(base, make(dg_objects=BASE_DG, rules=[r1]))
    assert [r["status"] for r in removed.rules] == ["idêntica", "REMOVIDA"]
    added = compare_trees(base, make(dg_objects=BASE_DG, rules=[r1, r2, r3]))
    assert "ADICIONADA" in [r["status"] for r in added.rules]
    renamed = compare_trees(base, make(dg_objects=BASE_DG, rules=[("R1x",) + r1[1:], r2]))
    assert "RENOMEADA" in [r["status"] for r in renamed.rules]
    reordered = compare_trees(base, make(dg_objects=BASE_DG, rules=[r2, r1]))
    assert "REORDENADA" in [r["status"] for r in reordered.rules]
    assert any(o["relative_order_preserved"] == "NÃO" for o in reordered.rule_order)
    assert all(c.verdict == VERDICT_DIFF for c in (removed, added, renamed, reordered))


def test_removed_object_still_referenced_is_new_unresolved_reference():
    # o objeto SRV some e a regra continua apontando para ele
    c = compare_trees(make(dg_objects=BASE_DG), make(dg_objects=SVC))
    assert c.summary["new_unresolved_references"] >= 1
    assert c.verdict == VERDICT_DIFF


def test_description_only_change_is_informative():
    c = compare_trees(make(dg_objects=addr("SRV", "10.0.0.1", "a") + SVC),
                      make(dg_objects=addr("SRV", "10.0.0.1", "b") + SVC))
    mod = [o for o in c.object_changes if o["change"] == "Modificado"]
    assert len(mod) == 1 and mod[0]["risk"] == "INFORMATIVO" and mod[0]["changed_attributes"] == "descrição"
    assert c.verdict == VERDICT_CLEAN


def test_section_outside_objects_and_policies_is_compared():
    c = compare_trees(make(dg_objects=BASE_DG, extra="<panorama><x>1</x></panorama>"),
                      make(dg_objects=BASE_DG, extra="<panorama><x>2</x></panorama>"))
    assert any(s["section"] == "/config/panorama" and s["status"] == "DIFERENTE" for s in c.sections)
    assert c.verdict == VERDICT_DIFF


def test_exported_ignores_readonly_and_predefined():
    orig = make(dg_objects=BASE_DG)
    mod = make(dg_objects=BASE_DG, readonly=RO.replace("<id>11</id>", "<id>12</id>"),
               extra="<predefined><application/></predefined>")
    assert compare_trees(orig, mod).verdict == VERDICT_DIFF
    c = compare_trees(orig, mod, exported=True)
    assert c.verdict == VERDICT_CLEAN
    assert any("predefined" in w for w in c.warnings)


def test_inputs_trees_are_not_modified():
    orig, mod = make(dg_objects=BASE_DG), make(dg_objects=BASE_DG, extra="<predefined/>")
    before = etree.tostring(orig)
    compare_trees(orig, mod, exported=True)
    assert etree.tostring(orig) == before


def _run(project, orig_tree, mod_tree, *flags):
    a, b = project.input_dir / "orig.xml", project.input_dir / "mod.xml"
    a.write_bytes(etree.tostring(orig_tree, xml_declaration=True, encoding="utf-8"))
    b.write_bytes(etree.tostring(mod_tree, xml_declaration=True, encoding="utf-8"))
    code = main(["--config", str(project.root / "config" / "settings.json") if hasattr(project, "root")
                 else str(project.input_dir.parent / "config" / "settings.json"),
                 "compare", "--input", str(a), "--modified", str(b), *flags])
    return code, a, b


def test_cli_writes_reports_and_never_touches_inputs(project):
    hostile = '<script>alert(1)</script> =CMD() \ufffd'
    orig = make(dg_objects=addr("SRV", "10.0.0.1", "x") + SVC)
    mod = make(dg_objects=addr("SRV", "10.0.0.1", hostile) + SVC)
    a, b = project.input_dir / "orig.xml", project.input_dir / "mod.xml"
    a.write_bytes(etree.tostring(orig))
    b.write_bytes(etree.tostring(mod))
    import hashlib
    sha = lambda p: hashlib.sha256(p.read_bytes()).hexdigest()
    before = (sha(a), sha(b))
    code = main(["--config", str(project.input_dir.parent / "config" / "settings.json"),
                 "compare", "--input", str(a), "--modified", str(b)])
    assert code == 0 and (sha(a), sha(b)) == before
    run = next(project.reports_dir.glob("*-compare-*"))
    names = {p.name for p in run.iterdir()}
    assert {"compare-report.html", "compare-report.xlsx", "compare-rules.csv",
            "compare-rule-field-changes.csv", "compare-object-changes.csv",
            "compare-group-effective-diffs.csv", "manifest.json"} <= names
    page = (run / "compare-report.html").read_text(encoding="utf-8")
    assert "<script>alert(1)</script>" not in page and "alert(1)" in page
    assert "<script>alert" not in page.replace("<script>\ndocument", "")
    manifest = json.loads((run / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["status"] == "ok" and manifest["modified"]["sha256"] == before[1]
    rows = list(csv.DictReader(StringIO((run / "compare-object-changes.csv").read_text(encoding="utf-8"))))
    assert rows[0]["description_modified"] == hostile.replace("&#xFFFD;", "�")
    from openpyxl import load_workbook
    ws = load_workbook(run / "compare-report.xlsx")["object-changes"]
    cell = next(c for row in ws.iter_rows(min_row=2) for c in row if str(c.value).startswith("<script>"))
    assert cell.data_type == "s"


def test_cli_rejects_same_file(project):
    a = project.input_dir / "orig.xml"
    a.write_bytes(etree.tostring(make(dg_objects=BASE_DG)))
    code = main(["--config", str(project.input_dir.parent / "config" / "settings.json"),
                 "compare", "--input", str(a), "--modified", str(a)])
    assert code == 1
