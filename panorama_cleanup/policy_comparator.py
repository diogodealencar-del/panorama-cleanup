"""Comparação de políticas original x transformada (CLAUDE.md §3).

Cada regra de cada rulebase (Shared e Device Groups) é comparada na forma
canônica. A única diferença aceita é a troca de nome de membro registrada no
plano; ordem, UUID, ações e todos os demais campos devem ser idênticos.
"""

from __future__ import annotations

import copy

from lxml import etree

from panorama_cleanup.inventory import quote
from panorama_cleanup.xml_reader import SHARED, panorama_entry

IDENTICAL, UPDATED, DIFFERENT = "idêntica", "referências atualizadas", "DIFERENTE"


def canonical(el) -> bytes:
    """C14N sem espaços de formatação."""
    c = copy.deepcopy(el)
    for x in c.iter():
        if x.text is not None and not x.text.strip():
            x.text = None
        if x.tail is not None and not x.tail.strip():
            x.tail = None
    c.tail = None
    return etree.tostring(c, method="c14n")


def rulebases(tree) -> dict:
    """(escopo, rulebase/tipo) -> [(xpath, elemento da regra)] na ordem do XML."""
    root = tree.getroot()
    device = panorama_entry(root)
    scopes = [(SHARED, root.find("shared"), "/config/shared")]
    base = f"/config/devices/entry[@name={quote(device.get('name', ''))}]/device-group"
    scopes += [(e.get("name"), e, f"{base}/entry[@name={quote(e.get('name'))}]")
               for e in device.findall("device-group/entry")]
    out = {}
    for scope, element, prefix in scopes:
        if element is None:
            continue
        for rulebase in ("pre-rulebase", "post-rulebase"):
            rb = element.find(rulebase)
            if rb is None:
                continue
            for kind in rb:
                if not isinstance(kind.tag, str):
                    continue
                rules = [(f"{prefix}/{rulebase}/{kind.tag}/rules/entry[@name={quote(r.get('name', ''))}]", r)
                         for r in kind.findall("rules/entry")]
                out[(scope, f"{rulebase}/{kind.tag}")] = rules
    return out


def compare_policies(original, transformed, rule_renames: dict) -> list:
    """Linhas de policy-comparison: uma por regra, mais linhas de erro estrutural."""
    from panorama_cleanup.shared_converter import replace_reference

    before, after = rulebases(original), rulebases(transformed)
    renames_by_rule = {}
    for (xpath, field_path, old), new in rule_renames.items():
        renames_by_rule.setdefault(xpath, []).append((field_path, old, new))

    rows = []
    for key in sorted(set(before) | set(after), key=lambda k: (k[0] != SHARED, k)):
        scope, rulebase = key
        rb, ra = before.get(key), after.get(key)
        if rb is None or ra is None:
            rows.append(_row(scope, rulebase, "", "", 0, DIFFERENT, "rulebase ausente em um dos lados"))
            continue
        if [x for x, _ in rb] != [x for x, _ in ra]:
            rows.append(_row(scope, rulebase, "", "", 0, DIFFERENT,
                             f"ordem ou conjunto de regras diferente ({len(rb)} x {len(ra)})"))
            continue
        for position, ((xpath, rule_b), (_, rule_a)) in enumerate(zip(rb, ra), 1):
            expected = copy.deepcopy(rule_b)
            changes = renames_by_rule.get(xpath, [])
            for field_path, old, new in changes:
                replace_reference(expected, field_path, old, new)
            same_as_expected = canonical(expected) == canonical(rule_a)
            if not same_as_expected:
                status, detail = DIFFERENT, "conteúdo difere do esperado pelo plano"
            elif changes:
                status = UPDATED
                detail = "; ".join(f"{f}: {o} -> {n}" for f, o, n in changes)
            else:
                status, detail = IDENTICAL, ""
            rows.append(_row(scope, rulebase, rule_b.get("name", ""), rule_b.get("uuid", ""),
                             position, status, detail, uuid_after=rule_a.get("uuid", "")))
    return rows


def _row(scope, rulebase, name, uuid, position, status, detail, uuid_after=None) -> dict:
    return {"scope": scope, "rulebase": rulebase, "rule": name, "position": position,
            "uuid": uuid, "uuid_unchanged": "yes" if uuid_after in (None, uuid) else "no",
            "status": status, "detail": detail}


def rule_field_changes(original, transformed, rule_renames: dict) -> list:
    """Diferenças campo a campo de cada regra, classificadas como autorizadas ou não.

    Autorizada: o campo final é exatamente o original com as renomeações de membro
    registradas no plano para aquela regra e campo. Qualquer outra diferença, inclusive
    em atributos da regra (name, uuid), é NÃO AUTORIZADA.
    """
    from panorama_cleanup.shared_converter import replace_reference

    renames = {}
    for (xpath, field_path, old), new in rule_renames.items():
        renames.setdefault((xpath, field_path.split("/")[0]), []).append((field_path, old, new))

    before, after = rulebases(original), rulebases(transformed)
    rows = []
    for key in sorted(set(before) & set(after), key=lambda k: (k[0] != SHARED, k)):
        scope, rulebase = key
        after_rules = dict(after[key])
        for xpath, rule_b in before[key]:
            rule_a = after_rules.get(xpath)
            if rule_a is None:
                continue
            if dict(rule_b.attrib) != dict(rule_a.attrib):
                rows.append(_change(scope, rulebase, rule_b, "@atributos", "NÃO AUTORIZADA",
                                    str(dict(rule_b.attrib)), str(dict(rule_a.attrib))))
            fields_b = {c.tag: c for c in rule_b if isinstance(c.tag, str)}
            fields_a = {c.tag: c for c in rule_a if isinstance(c.tag, str)}
            for tag in sorted(set(fields_b) | set(fields_a)):
                fb, fa = fields_b.get(tag), fields_a.get(tag)
                if fb is not None and fa is not None and canonical(fb) == canonical(fa):
                    continue
                status = "NÃO AUTORIZADA"
                if fb is not None and fa is not None and (xpath, tag) in renames:
                    expected = copy.deepcopy(fb)
                    wrapper = etree.Element("rule")
                    wrapper.append(expected)
                    for field_path, old, new in renames[(xpath, tag)]:
                        replace_reference(wrapper, field_path, old, new)
                    if canonical(wrapper[0]) == canonical(fa):
                        status = "autorizada"
                rows.append(_change(scope, rulebase, rule_b, tag, status,
                                    _text(fb), _text(fa)))
    return rows


def _change(scope, rulebase, rule, field_name, status, before, after) -> dict:
    return {"scope": scope, "rulebase": rulebase, "rule": rule.get("name", ""),
            "uuid": rule.get("uuid", ""), "field": field_name, "status": status,
            "before": before, "after": after}


def _text(el) -> str:
    if el is None:
        return "(ausente)"
    members = [(m.text or "").strip() for m in el.iter("member")]
    if members:
        return "; ".join(members)
    return canonical(el).decode("utf-8", "replace")[:500]
