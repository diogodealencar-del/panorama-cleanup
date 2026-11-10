"""Etapa 8 (modo VALIDATE): comparação integral do XML original com o XML final.

Parte apenas dos dois XMLs e das alterações autorizadas registradas nas etapas
anteriores (decisões do plano, renomeações aplicadas, exclusões confirmadas). Toda
diferença fora dessas alterações é inconsistência e o resultado é NÃO APROVADO.
"""

from __future__ import annotations

import copy
from collections import Counter
from dataclasses import dataclass, field

from panorama_cleanup.analysis import run_analysis
from panorama_cleanup.effective_model import (
    State, compare, objects_from_inventory, rule_refs_from_graph,
)
from panorama_cleanup.inventory import KNOWN_TYPES, quote
from panorama_cleanup.policy_comparator import (
    DIFFERENT, canonical, compare_policies, rule_field_changes, rulebases,
)
from panorama_cleanup.xml_reader import SHARED, panorama_entry

APPROVED, NOT_APPROVED = "APROVADO", "NÃO APROVADO"
RULEBASE_SECTIONS = frozenset({"pre-rulebase", "post-rulebase"})
OBJECT_SECTIONS = frozenset({t.split("/")[0] for t, _ in KNOWN_TYPES}) | {"profiles", "admin-role"}


@dataclass
class Authorized:
    """Alterações autorizadas, lidas dos relatórios das etapas 4 a 6."""
    decisions: dict                  # xpath original -> linha de decisão do plano
    rule_renames: dict               # (xpath regra, campo, antigo) -> novo
    local_renames: list              # (xpath objeto local, campo, antigo, novo)
    copy_renames: dict               # xpath original -> [(campo, antigo, novo)]
    deleted_unused: set              # xpaths excluídos na Etapa 6
    kept_types: tuple = ()
    descriptions: dict = field(default_factory=dict)  # xpath final -> descrição herdada (regra 3)


@dataclass
class Validation:
    checks: list = field(default_factory=list)          # (área, verificação, ok, detalhe)
    sections: list = field(default_factory=list)
    policies: list = field(default_factory=list)
    policy_changes: list = field(default_factory=list)
    objects: list = field(default_factory=list)
    references: list = field(default_factory=list)
    tags: list = field(default_factory=list)
    remaining: list = field(default_factory=list)
    summary: dict = field(default_factory=dict)

    @property
    def result(self) -> str:
        return APPROVED if all(ok for _, _, ok, _ in self.checks) else NOT_APPROVED

    def add(self, area, name, ok, detail=""):
        self.checks.append((area, name, bool(ok), detail))


def validate_final(original, final, auth: Authorized, ignore_sections=()) -> Validation:
    """ignore_sections: seções fora da comparação (ex.: /config/readonly em XML exportado do
    Panorama, cujos IDs internos o próprio Panorama mantém; D23)."""
    v = Validation()
    _structure(original, final, v, ignore_sections)
    _policies(original, final, auth, v)
    an_o, an_f = run_analysis(original), run_analysis(final)
    _objects(original, final, an_o, an_f, auth, v)
    _references(an_o, an_f, auth, v)
    _tags(an_o, an_f, auth, v)
    _centralization(an_o, an_f, auth, v)
    return v


# --- estrutura ---------------------------------------------------------------------

def _structure(original, final, v, ignore_sections=()):
    ro, rf = original.getroot(), final.getroot()
    v.add("estrutura", "raiz <config> e atributos de versão idênticos",
          ro.tag == rf.tag == "config" and dict(ro.attrib) == dict(rf.attrib), str(dict(rf.attrib)))
    top_o = [c.tag for c in ro if isinstance(c.tag, str)]
    top_f = [c.tag for c in rf if isinstance(c.tag, str)]
    v.add("estrutura", "seções de primeiro nível e ordem idênticas", top_o == top_f, " ".join(top_f))

    pairs = []
    for child in ro:
        if isinstance(child.tag, str) and child.tag not in ("shared", "devices"):
            pairs.append((f"/config/{child.tag}", child, rf.find(child.tag)))
    do, df = panorama_entry(ro), panorama_entry(rf)
    base = f"/config/devices/entry[@name={quote(do.get('name', ''))}]"
    for child in do:
        if isinstance(child.tag, str) and child.tag != "device-group":
            pairs.append((f"{base}/{child.tag}", child, df.find(child.tag)))
    dgs_o = [e.get("name") for e in do.findall("device-group/entry")]
    dgs_f = [e.get("name") for e in df.findall("device-group/entry")]
    v.add("estrutura", "Device Groups: mesmo conjunto e ordem", dgs_o == dgs_f, " ".join(dgs_f))
    scopes = [("/config/shared", ro.find("shared"), rf.find("shared"))]
    for name in dgs_o:
        q = quote(name)
        scopes.append((f"{base}/device-group/entry[@name={q}]",
                       do.find(f"device-group/entry[@name='{name}']"),
                       df.find(f"device-group/entry[@name='{name}']")))
    for prefix, so, sf in scopes:
        if so is None or sf is None:
            continue
        for child in so:
            if not isinstance(child.tag, str) or child.tag in RULEBASE_SECTIONS:
                continue
            if child.tag in OBJECT_SECTIONS:
                continue
            if child.tag == "log-settings":
                pairs.append(_without(prefix, child, sf.find(child.tag), "profiles"))
            else:
                pairs.append((f"{prefix}/{child.tag}", child, sf.find(child.tag)))
        # Contêineres de objeto criados pela conversão são validados objeto a objeto.
        extra = [c.tag for c in sf if isinstance(c.tag, str) and so.find(c.tag) is None
                 and c.tag not in OBJECT_SECTIONS]
        if extra:
            v.sections.append({"section": prefix, "status": "DIFERENTE",
                               "detail": f"seções novas: {extra}"})
    if ignore_sections:
        v.add("estrutura", "seções excluídas da comparação (gerenciadas pelo Panorama)", True,
              ", ".join(ignore_sections))
    for path, a, b in pairs:
        if path in ignore_sections:
            v.sections.append({"section": path, "status": "ignorada", "detail": "IDs internos do Panorama"})
            continue
        same = b is not None and canonical(a) == canonical(b)
        v.sections.append({"section": path, "status": "idêntica" if same else "DIFERENTE",
                           "detail": "" if same else "conteúdo difere do original"})
    different = [s for s in v.sections if s["status"] not in ("idêntica", "ignorada")]
    v.add("estrutura", "configuração não relacionada (templates, Panorama, readonly, DGs) idêntica",
          not different, f"{len(v.sections)} seções comparadas; diferentes: "
          f"{[s['section'] for s in different][:5]}")


def _without(prefix, a, b, tag):
    a2, b2 = copy.deepcopy(a), copy.deepcopy(b) if b is not None else None
    for el in (a2, b2):
        if el is not None and el.find(tag) is not None:
            el.remove(el.find(tag))
    return (f"{prefix}/{a.tag} (exceto {tag})", a2, b2)


# --- políticas ----------------------------------------------------------------------

def _policies(original, final, auth, v):
    v.policies = compare_policies(original, final, auth.rule_renames)
    v.policy_changes = rule_field_changes(original, final, auth.rule_renames)
    rb_o, rb_f = rulebases(original), rulebases(final)
    v.add("políticas", "mesmas rulebases em Shared e em cada Device Group", set(rb_o) == set(rb_f),
          f"{len(rb_o)} rulebases")
    counts = Counter(k[1].split("/")[1] for k in rb_o for _ in rb_o[k])
    v.add("políticas", "mesma quantidade de regras por rulebase",
          all(len(rb_o[k]) == len(rb_f.get(k, ())) for k in rb_o),
          ", ".join(f"{t}={n}" for t, n in sorted(counts.items())))
    order = all([x for x, _ in rb_o[k]] == [x for x, _ in rb_f.get(k, ())] for k in rb_o)
    v.add("políticas", "ordem das regras idêntica", order, "")
    uuids = all([r.get("uuid") for _, r in rb_o[k]] == [r.get("uuid") for _, r in rb_f.get(k, ())]
                for k in rb_o)
    v.add("políticas", "UUIDs idênticos", uuids, "")
    unauthorized = [c for c in v.policy_changes if c["status"] != "autorizada"]
    v.add("políticas", "nenhuma alteração não autorizada em campos de regra", not unauthorized,
          f"{len(v.policy_changes)} campos alterados; não autorizados: {len(unauthorized)}")
    fields = Counter(c["field"] for c in v.policy_changes)
    # Campos que nunca referenciam objetos: qualquer diferença aqui é proibida (CLAUDE.md §3).
    # Campos que referenciam objetos só podem mudar por renomeação autorizada (verificado acima).
    protected = {"action", "from", "to", "disabled", "negate-source", "negate-destination",
                 "description", "option", "target", "rule-type", "log-start", "log-end",
                 "to-interface", "tag", "group-tag", "@atributos"}
    touched_protected = sorted(set(fields) & protected)
    v.add("políticas", "campos que não referenciam objetos (ação, zonas, flags, alvo, tags) intactos",
          not touched_protected, f"campos alterados: {dict(fields)}")
    diff_rules = [r for r in v.policies if r["status"] == DIFFERENT]
    v.add("políticas", "comparação regra a regra (conteúdo esperado pelo plano)", not diff_rules,
          f"{len(v.policies)} regras; divergentes: {len(diff_rules)}")


# --- objetos -----------------------------------------------------------------------

def _with_description(el, text):
    """Descrição herdada da cópia consolidada (operação SET_DESCRIPTION do plano)."""
    if text is None:
        return
    child = el.find("description")
    if child is None:
        child = el.makeelement("description", {})
        el.append(child)
    child.text = text


def _objects(original, final, an_o, an_f, auth, v):
    from panorama_cleanup.shared_converter import replace_reference

    final_by_xpath = {r.xpath: i for i, r in enumerate(an_f.inventory.records)}
    accounted, problems = set(), []
    local_by_xpath = {}
    for xpath, field_path, old, new in auth.local_renames:
        local_by_xpath.setdefault(xpath, []).append((field_path, old, new))

    for i, rec in enumerate(an_o.inventory.records):
        d = auth.decisions.get(rec.xpath)
        action = d["action"] if d else "?"
        el_o = an_o.inventory.elements[i]
        status, detail = "OK", ""
        if action in ("MOVE", "RENAME_MOVE"):
            new_xpath = f"/config/shared/{rec.object_type}/entry[@name={quote(d['new_name'])}]"
            j = final_by_xpath.get(new_xpath)
            expected = copy.deepcopy(el_o)
            expected.set("name", d["new_name"])
            for f, old, new in auth.copy_renames.get(rec.xpath, ()):
                replace_reference(expected, f, old, new)
            _with_description(expected, auth.descriptions.get(new_xpath))
            if rec.xpath in final_by_xpath:
                status, detail = "ERRO", "original ainda presente no Device Group"
            elif j is None:
                status, detail = "ERRO", f"cópia ausente em {new_xpath}"
            elif canonical(expected) != canonical(an_f.inventory.elements[j]):
                status, detail = "ERRO", "cópia em Shared difere do original"
            else:
                accounted.add(new_xpath)
                detail = f"em Shared como {d['new_name']}"
        elif action == "CONSOLIDATE":
            target = _target_xpath(d, auth)
            if rec.xpath in final_by_xpath:
                status, detail = "ERRO", "cópia consolidada ainda presente"
            elif target not in final_by_xpath:
                status, detail = "ERRO", f"objeto que a substitui não existe: {target}"
            else:
                detail = f"substituído por {target}"
        elif action == "DELETE_UNUSED" and rec.xpath in auth.deleted_unused:
            if rec.xpath in final_by_xpath:
                status, detail = "ERRO", "exclusão não aplicada"
            elif an_o.usage[i].status != "UNUSED":
                status, detail = "ERRO", f"excluído, mas era {an_o.usage[i].status} no original"
            else:
                detail = "excluído (não utilizado)"
        elif action == "?":
            status, detail = "ERRO", "objeto sem decisão no plano"
        else:
            j = final_by_xpath.get(rec.xpath)
            expected = copy.deepcopy(el_o)
            for f, old, new in local_by_xpath.get(rec.xpath, ()):
                replace_reference(expected, f, old, new)
            _with_description(expected, auth.descriptions.get(rec.xpath))
            if j is None:
                status, detail = "ERRO", "objeto mantido ausente no XML final"
            elif canonical(expected) != canonical(an_f.inventory.elements[j]):
                status, detail = "ERRO", "conteúdo alterado sem autorização"
            else:
                accounted.add(rec.xpath)
                detail = ("mantido com referência atualizada" if rec.xpath in local_by_xpath
                          else "mantido sem alteração")
        if status != "OK":
            problems.append(rec.xpath)
        v.objects.append({"scope": rec.scope, "object_type": rec.object_type, "name": rec.name,
                          "plan_action": action, "status": status, "detail": detail,
                          "xpath": rec.xpath})
    extra = [r.xpath for r in an_f.inventory.records if r.xpath not in accounted]
    for x in extra:
        v.objects.append({"scope": "", "object_type": "", "name": "", "plan_action": "",
                          "status": "ERRO", "detail": "objeto no XML final sem origem no plano",
                          "xpath": x})
    v.add("objetos", "cada objeto do original tem o destino previsto no plano", not problems,
          f"{len(an_o.inventory.records)} objetos; com erro: {len(problems)}")
    v.add("objetos", "nenhum objeto no XML final sem origem autorizada", not extra,
          f"{len(an_f.inventory.records)} objetos no final; sem origem: {len(extra)}")


def _target_xpath(d, auth) -> str:
    """Objeto que substitui a cópia: o de Shared, ou a cópia de outro DG movida com o mesmo nome."""
    return f"/config/shared/{d['object_type']}/entry[@name={quote(d['target_name'])}]"


# --- referências --------------------------------------------------------------------

def _references(an_o, an_f, auth, v):
    # Referências do original expressas como devem aparecer no final: objetos movidos
    # mudam de XPath e membros podem ter sido renomeados por autorização.
    moved = {}
    for xpath, d in auth.decisions.items():
        if d["action"] in ("MOVE", "RENAME_MOVE"):
            moved[xpath] = f"/config/shared/{d['object_type']}/entry[@name={quote(d['new_name'])}]"
    member_renames = {(x, f, o): n for x, f, o, n in auth.local_renames}
    for x, items in auth.copy_renames.items():
        for f, o, n in items:
            member_renames[(x, f, o)] = n

    def unresolved_before():
        out = Counter()
        for r in an_o.graph.references:
            if r.target >= 0 or r.resolution == "literal":
                continue
            if r.source_kind == "rule":
                value = auth.rule_renames.get((r.source_xpath, r.field, r.value), r.value)
                xpath = r.source_xpath
            else:
                value = member_renames.get((r.source_xpath, r.field, r.value), r.value)
                xpath = moved.get(r.source_xpath, r.source_xpath)
            out[(r.source_kind, xpath, r.field, value, r.resolution)] += 1
        return out

    def unresolved_after():
        out = Counter()
        for r in an_f.graph.references:
            if r.target < 0 and r.resolution != "literal":
                out[(r.source_kind, r.source_xpath, r.field, r.value, r.resolution)] += 1
        return out

    before, after = unresolved_before(), unresolved_after()
    new = [k for k in after if k not in before]
    for k in new:
        v.references.append({"source_kind": k[0], "source_xpath": k[1], "field": k[2],
                             "value": k[3], "resolution": k[4], "status": "NOVA (quebrada)"})
    v.add("referências", "nenhuma referência nova sem resolução (quebrada)", not new,
          f"não resolvidas antes: {sum(before.values())} (predefinidos/externos); "
          f"novas: {len(new)}")
    ambiguous = [r for r in an_f.graph.references if r.resolution == "ambiguous"]
    ambiguous_before = [r for r in an_o.graph.references if r.resolution == "ambiguous"]
    v.add("referências", "nenhuma referência ambígua nova", len(ambiguous) <= len(ambiguous_before),
          f"antes {len(ambiguous_before)}, depois {len(ambiguous)}")

    so = State(objects_from_inventory(an_o.inventory), an_o.inventory.device_groups)
    sf = State(objects_from_inventory(an_f.inventory), an_f.inventory.device_groups)
    refs = rule_refs_from_graph(an_o.graph)
    diffs = compare(so, sf, refs, auth.rule_renames)
    for context, ref, vb, va, _ in diffs[:500]:
        v.references.append({"source_kind": "rule", "source_xpath": ref.source_xpath,
                             "field": ref.field, "value": ref.value, "resolution": context,
                             "status": "VALOR EFETIVO DIFERENTE"})
    checked = sum(len(so.firewall_contexts(r.source_scope)) for r in refs)
    v.add("referências", "valor efetivo de cada referência de regra idêntico em cada firewall",
          not diffs, f"{checked} verificações; diferenças: {len(diffs)}")
    known = {r.name for r in an_o.inventory.records}
    broken_o = {(o.scope, o.name, f, n) for o, f, n in so.unresolved_object_refs(known)}
    broken_f = [(o.scope, o.name, f, n) for o, f, n in sf.unresolved_object_refs(known)]
    new_broken = [b for b in broken_f if b not in broken_o]
    v.add("referências", "membros e tags de todos os objetos resolvem no próprio escopo",
          not new_broken, f"novas quebras: {new_broken[:5]}")
    shared_bad = [(o.name, f, n) for o in sf.objects if o.scope == SHARED
                  for f, n, t in list(o.members) + [("tag", x, ("tag",)) for x in o.tags]
                  if n in known and not sf.resolve(SHARED, n, t)]
    v.add("referências", "objetos em Shared referenciam apenas objetos de Shared", not shared_bad,
          f"violações: {shared_bad[:5]}")


# --- tags -----------------------------------------------------------------------------

def _tags(an_o, an_f, auth, v):
    final = {r.xpath: an_f.inventory.elements[i] for i, r in enumerate(an_f.inventory.records)
             if r.object_type == "tag"}
    missing, changed = [], []
    for i, r in enumerate(an_o.inventory.records):
        if r.object_type != "tag":
            continue
        el = final.get(r.xpath)
        status = "idêntica"
        if el is None:
            status = "AUSENTE"
            missing.append(r.xpath)
        elif canonical(el) != canonical(an_o.inventory.elements[i]):
            status = "ALTERADA"
            changed.append(r.xpath)
        v.tags.append({"scope": r.scope, "name": r.name, "value": r.value, "status": status,
                       "xpath": r.xpath})
    v.add("tags", "todas as tags do original presentes e idênticas (mesmo escopo)",
          not missing and not changed,
          f"{len(v.tags)} tags; ausentes {len(missing)}; alteradas {len(changed)}")
    new_tags = [r.xpath for r in an_f.inventory.records if r.object_type == "tag"
                and r.xpath not in {t["xpath"] for t in v.tags}]
    v.add("tags", "nenhuma tag nova criada", not new_tags, f"{new_tags[:5]}")

    origin = {}
    for r in an_o.inventory.records:
        d = auth.decisions.get(r.xpath)
        if d and d["action"] in ("MOVE", "RENAME_MOVE"):
            origin[f"/config/shared/{r.object_type}/entry[@name={quote(d['new_name'])}]"] = r
    by_xpath = {r.xpath: r for r in an_o.inventory.records}
    tag_diff = [r.xpath for r in an_f.inventory.records
                if (origin.get(r.xpath) or by_xpath.get(r.xpath)) is not None
                and (origin.get(r.xpath) or by_xpath[r.xpath]).tags != r.tags]
    v.add("tags", "tags aplicadas aos objetos inalteradas", not tag_diff, f"{tag_diff[:5]}")
    rule_tag = [c for c in v.policy_changes if c["field"] in ("tag", "group-tag")]
    v.add("tags", "campos tag/group-tag das regras inalterados", not rule_tag, "")


# --- centralização ---------------------------------------------------------------------

def _centralization(an_o, an_f, auth, v):
    kept_unused_reason = "mantido na Etapa 6 (decisão do usuário ou reanálise USED)"
    undocumented = []
    for r in an_f.inventory.records:
        if r.scope == SHARED:
            continue
        d = auth.decisions.get(r.xpath)
        if d is None:
            reason = ""
        elif d["action"] in ("KEEP_LOCAL", "PENDING_DECISION"):
            reason = d["reason"]
        elif d["action"] == "DELETE_UNUSED" and r.xpath not in auth.deleted_unused:
            reason = kept_unused_reason
        else:
            reason = ""
        if not reason:
            undocumented.append(r.xpath)
        v.remaining.append({"scope": r.scope, "object_type": r.object_type, "name": r.name,
                            "reason": reason or "SEM MOTIVO DOCUMENTADO", "xpath": r.xpath})
    v.add("centralização", "todo objeto que permanece em Device Group tem motivo documentado",
          not undocumented, f"{len(v.remaining)} objetos em DGs; sem motivo: {len(undocumented)}")
    eligible_left = [x for x in v.remaining if x["reason"] == ""]
    v.summary = {
        "objects_original": len(an_o.inventory.records),
        "objects_final": len(an_f.inventory.records),
        "shared_original": sum(r.scope == SHARED for r in an_o.inventory.records),
        "shared_final": sum(r.scope == SHARED for r in an_f.inventory.records),
        "dg_original": sum(r.scope != SHARED for r in an_o.inventory.records),
        "dg_final": len(v.remaining),
        "rules": len(v.policies),
        "rules_with_reference_updates": sum(r["status"] != "idêntica" for r in v.policies),
        "rule_field_changes": len(v.policy_changes),
        "tags_preserved": sum(t["status"] == "idêntica" for t in v.tags),
    }
    v.add("centralização", "objetos elegíveis comprovadamente transparentes estão em Shared",
          not eligible_left, f"Shared {v.summary['shared_original']} -> {v.summary['shared_final']}; "
          f"DGs {v.summary['dg_original']} -> {v.summary['dg_final']}")
