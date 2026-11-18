"""Comparação detalhada de dois XMLs de configuração do Panorama (modo COMPARE).

Somente leitura e sem plano: a entrada são apenas o XML original e o XML modificado
(gerado pela ferramenta ou exportado do Panorama). Nada é "autorizado" aqui; o relatório
descreve o que mudou e prova, para cada campo de regra que referencia objetos, se o valor
efetivo em cada firewall (contexto de Device Group) continua igual.

Limites: o modelo de resolução de nomes é o de effective_model.py. O resultado nunca é
APROVADO: sem o plano e sem o Panorama, a conclusão máxima é "sem diferenças efetivas nas
regras modeladas" e continua INCONCLUSIVA para importação.
"""

from __future__ import annotations

import copy
from collections import Counter, defaultdict
from dataclasses import dataclass, field

from panorama_cleanup.analysis import run_analysis
from panorama_cleanup.effective_model import (
    NAME_TYPES, State, compare_objects, objects_from_inventory, rule_refs_from_graph,
    used_contexts,
)
from panorama_cleanup.inventory import KNOWN_TYPES
from panorama_cleanup.policy_comparator import canonical, rulebases
from panorama_cleanup.xml_reader import SHARED, panorama_entry

LOW, MEDIUM, HIGH, INFO = "BAIXO", "MÉDIO", "ALTO", "INFORMATIVO"
NOT_VALIDATED = (
    "catálogo App-ID e atualização de conteúdo do destino",
    "usuários e grupos de diretório",
    "estado operacional dos firewalls",
    "referências e overrides definidos localmente nos firewalls",
    "aceitação do XML como candidate config (Validate Candidate Configuration)",
    "resultado de commit e push",
    "autorização individual de cada troca (não há plano nesta comparação)",
)
# Campos de regra que são tags: nunca podem mudar (CLAUDE.md §3).
TAG_FIELDS = ("tag", "group-tag")
# Contêineres de objetos e rulebases removidos antes da comparação de seções.
_SCOPE_STRIP = tuple(rel.split("/")[0] for rel, _ in KNOWN_TYPES) + (
    "profiles", "pre-rulebase", "post-rulebase")

VERDICT_DIFF = "DIFERENÇAS RELEVANTES ENCONTRADAS"
VERDICT_CLEAN = "SEM DIFERENÇAS EFETIVAS NAS REGRAS MODELADAS (INCONCLUSIVO PARA IMPORTAÇÃO)"


@dataclass
class Comparison:
    sections: list = field(default_factory=list)
    rulebases: list = field(default_factory=list)
    rule_order: list = field(default_factory=list)
    rules: list = field(default_factory=list)
    rule_changes: list = field(default_factory=list)
    object_types: list = field(default_factory=list)
    object_scopes: list = field(default_factory=list)
    object_changes: list = field(default_factory=list)
    classification: list = field(default_factory=list)
    group_diffs: list = field(default_factory=list)
    group_rules: list = field(default_factory=list)
    policy_types: list = field(default_factory=list)
    conflicts_by_type: list = field(default_factory=list)
    resolution_warnings: list = field(default_factory=list)
    new_unresolved: list = field(default_factory=list)
    summary: dict = field(default_factory=dict)
    attention: list = field(default_factory=list)
    verdict: str = VERDICT_CLEAN
    warnings: list = field(default_factory=list)
    rule_xpath_map: dict = field(default_factory=dict, repr=False)
    metadata: dict = field(default_factory=dict)


def compare_trees(original, modified, exported: bool = False) -> Comparison:
    """Compara duas árvores já lidas. exported: XML exportado do Panorama (ignora /config/readonly
    e /config/predefined ausente no original); as árvores recebidas não são alteradas."""
    from panorama_cleanup.final_validation import strip_exported_predefined

    cmp = Comparison()
    original, modified = copy.deepcopy(original), copy.deepcopy(modified)
    if exported:
        # /config/readonly fica nas árvores (a hierarquia dos DGs vem dela); só sai da
        # comparação de seções, pois os IDs são mantidos pelo próprio Panorama.
        if strip_exported_predefined(original, modified):
            cmp.warnings.append("/config/predefined do XML exportado ignorada (conteúdo embutido "
                                "do Panorama, ausente no original)")
    an_o, an_m = run_analysis(original), run_analysis(modified)
    state_o = State(objects_from_inventory(an_o.inventory), an_o.inventory.device_groups)
    state_m = State(objects_from_inventory(an_m.inventory), an_m.inventory.device_groups)
    md = an_o.inventory.metadata
    cmp.metadata = {"panos": getattr(md, "detail_version", None) or getattr(md, "config_version", "") or ""}
    cmp.warnings += [f"original: {w}" for w in an_o.warnings] + [f"modificado: {w}" for w in an_m.warnings]

    _sections(original, modified, cmp, ("/config/readonly",) if exported else ())
    _rules(original, modified, an_o, an_m, state_o, state_m, cmp)
    _objects(an_o, an_m, state_o, state_m, cmp)
    _groups(an_o, an_m, state_o, state_m, cmp)
    _unresolved(an_o, an_m, cmp)
    _summary(cmp)
    return cmp


# ----------------------------------------------------------------------------- seções

def _strip_scope(root) -> None:
    from panorama_cleanup.xml_reader import panorama_entry as pe
    scopes = [root.find("shared")] + list(pe(root).findall("device-group/entry"))
    for scope in scopes:
        if scope is None:
            continue
        for tag in _SCOPE_STRIP:
            for el in scope.findall(tag):
                scope.remove(el)
        for el in scope.findall("log-settings/profiles"):
            el.getparent().remove(el)


def _section_map(tree) -> dict:
    root = copy.deepcopy(tree.getroot())
    _strip_scope(root)
    out = {}
    for child in root:
        if not isinstance(child.tag, str):
            continue
        if child.tag == "devices":
            for dev in child.findall("entry"):
                base = f"/config/devices/{dev.get('name')}"
                for sub in dev:
                    if not isinstance(sub.tag, str):
                        continue
                    if sub.tag == "device-group":
                        for dg in sub.findall("entry"):
                            out[f"{base}/device-group/{dg.get('name')} (fora de objetos e políticas)"] = dg
                    else:
                        out[f"{base}/{sub.tag}"] = sub
        elif child.tag == "shared":
            out["/config/shared (fora de objetos e políticas)"] = child
        else:
            out[f"/config/{child.tag}"] = child
    return out


def _sections(original, modified, cmp, ignore=()) -> None:
    a, b = _section_map(original), _section_map(modified)
    for path in ignore:
        found = (a.pop(path, None), b.pop(path, None))
        if any(x is not None for x in found):
            cmp.sections.append({"section": path, "status": "ignorada",
                                 "detail": "IDs internos mantidos pelo Panorama (XML exportado)"})
    for path in sorted(set(a) | set(b)):
        if path not in a:
            status, detail = "AUSENTE NO ORIGINAL", ""
        elif path not in b:
            status, detail = "AUSENTE NO MODIFICADO", ""
        elif canonical(a[path]) == canonical(b[path]):
            status, detail = "idêntica", ""
        else:
            status, detail = "DIFERENTE", "conteúdo difere fora de objetos inventariados e rulebases"
        cmp.sections.append({"section": path, "status": status, "detail": detail})


# ----------------------------------------------------------------------------- regras

def _match(rb, ra) -> list:
    """Pareia regras de uma rulebase: primeiro por UUID, depois por nome. [(i, j, como)]."""
    by_uuid = {el.get("uuid"): j for j, (_, el) in enumerate(ra) if el.get("uuid")}
    by_name = {}
    for j, (_, el) in enumerate(ra):
        by_name.setdefault(el.get("name"), j)
    used, pairs, left = set(), [], []
    for i, (_, el) in enumerate(rb):
        j = by_uuid.get(el.get("uuid")) if el.get("uuid") else None
        if j is not None and j not in used:
            used.add(j)
            pairs.append((i, j, "uuid"))
        else:
            left.append(i)
    for i in left:
        j = by_name.get(rb[i][1].get("name"))
        if j is not None and j not in used:
            used.add(j)
            pairs.append((i, j, "nome"))
    return sorted(pairs)


def _xml(el, limit: int = 1500) -> str:
    if el is None:
        return "[AUSENTE]"
    text = canonical(el).decode("utf-8", "replace")
    return text if len(text) <= limit else text[:limit] + "…"


def _text(el) -> str:
    if el is None:
        return "(ausente)"
    members = [(m.text or "").strip() for m in el.iter("member")]
    if members:
        return "; ".join(members)
    return canonical(el).decode("utf-8", "replace")[:500]


def _refs_by_rule(graph) -> dict:
    out = defaultdict(list)
    for ref in rule_refs_from_graph(graph):
        out[(ref.source_xpath, ref.field.split("/")[0])].append(ref)
    return out


def _effective(state_o, state_m, refs_o, refs_m, scope) -> list:
    """Diferenças de valor efetivo por contexto de firewall: [(contexto, só antes, só depois)]."""
    diffs = []
    for context in state_o.firewall_contexts(scope):
        before, after = set(), set()
        for ref in refs_o:
            before |= state_o.expand(context, ref.value, ref.types)[0]
        for ref in refs_m:
            after |= state_m.expand(context, ref.value, ref.types)[0]
        if before != after:
            diffs.append((context, before - after, after - before))
    return diffs


def _fmt(values, limit: int = 4) -> str:
    items = sorted("|".join(str(x) for x in v) if isinstance(v, tuple) else str(v) for v in values)
    text = "; ".join(items[:limit])
    return text + (f"; (+{len(items) - limit})" if len(items) > limit else "")


def _rules(original, modified, an_o, an_m, state_o, state_m, cmp) -> None:
    rb_o, rb_m = rulebases(original), rulebases(modified)
    refs_o, refs_m = _refs_by_rule(an_o.graph), _refs_by_rule(an_m.graph)
    policy_types = {}
    for key in sorted(set(rb_o) | set(rb_m), key=lambda k: (k[0] != SHARED, k)):
        scope, rulebase = key
        lo, lm = rb_o.get(key, []), rb_m.get(key, [])
        pairs = _match(lo, lm)
        mod_positions = [j for _, j, _ in pairs]
        ordered = mod_positions == sorted(mod_positions)
        changed_xml = 0
        matched_o, matched_m = {i for i, _, _ in pairs}, {j for _, j, _ in pairs}
        peak = -1
        for i, j, how in pairs:
            (xp_o, el_o), (xp_m, el_m) = lo[i], lm[j]
            cmp.rule_xpath_map[xp_o] = xp_m
            changes = _rule_changes(scope, rulebase, el_o, el_m, xp_o, xp_m, refs_o, refs_m,
                                    state_o, state_m)
            cmp.rule_changes += changes
            status = "idêntica" if not changes else "campos alterados"
            if el_o.get("name") != el_m.get("name"):
                status = "RENOMEADA"
            if el_o.get("uuid") != el_m.get("uuid"):
                status = "UUID DIFERENTE"
            if j < peak:
                status = "REORDENADA"
            peak = max(peak, j)
            if changes or status != "idêntica":
                changed_xml += 1
            cmp.rules.append({
                "scope": scope, "rulebase": rulebase, "rule": el_o.get("name", ""),
                "rule_modified": el_m.get("name", ""), "uuid": el_o.get("uuid", ""),
                "position_original": i + 1, "position_modified": j + 1, "matched_by": how,
                "status": status,
                "changed_fields": ", ".join(sorted({c["field"] for c in changes})),
                "worst_risk": _worst([c["risk"] for c in changes]),
                "xpath_original": xp_o, "xpath_modified": xp_m})
        for i, (xp, el) in enumerate(lo):
            if i not in matched_o:
                changed_xml += 1
                cmp.rules.append(_orphan(scope, rulebase, el, i + 1, None, "REMOVIDA", xp, ""))
        for j, (xp, el) in enumerate(lm):
            if j not in matched_m:
                changed_xml += 1
                cmp.rules.append(_orphan(scope, rulebase, el, None, j + 1, "ADICIONADA", "", xp))
        kind = rulebase.split("/")[-1]
        pt = policy_types.setdefault(kind, [0, 0, 0])
        pt[0] += len(lo)
        pt[1] += len(lm)
        pt[2] += changed_xml
        cmp.rulebases.append({"scope": scope, "rulebase": rulebase, "original": len(lo),
                              "modified": len(lm), "xml_changed": changed_xml})
        cmp.rule_order.append({"scope": scope, "rulebase": rulebase, "original": len(lo),
                               "modified": len(lm),
                               "relative_order_preserved": "sim" if ordered else "NÃO"})
    _finish_policy_types(cmp, policy_types)


def _finish_policy_types(cmp, policy_types) -> None:
    for kind in ("security", "nat", "decryption", "pbf", "qos", "application-override",
                 "authentication", "dos"):
        policy_types.setdefault(kind, [0, 0, 0])
    for kind in sorted(policy_types):
        o, m, c = policy_types[kind]
        cmp.policy_types.append({"policy": kind, "original": o, "modified": m, "xml_changed": c})


def _orphan(scope, rulebase, el, pos_o, pos_m, status, xp_o, xp_m) -> dict:
    return {"scope": scope, "rulebase": rulebase, "rule": el.get("name", ""),
            "rule_modified": "" if pos_m is None else el.get("name", ""), "uuid": el.get("uuid", ""),
            "position_original": pos_o or "", "position_modified": pos_m or "", "matched_by": "",
            "status": status, "changed_fields": "", "worst_risk": HIGH,
            "xpath_original": xp_o, "xpath_modified": xp_m}


def _worst(risks: list) -> str:
    for r in (HIGH, MEDIUM, LOW, INFO):
        if r in risks:
            return r
    return ""


def _rule_changes(scope, rulebase, el_o, el_m, xp_o, xp_m, refs_o, refs_m, state_o, state_m) -> list:
    rows = []

    current = {}

    def row(field_name, kind, before, after, risk, effective, detail=""):
        return {"scope": scope, "rulebase": rulebase, "rule": el_o.get("name", ""),
                "rule_modified": el_m.get("name", ""), "uuid": el_o.get("uuid", ""),
                "field": field_name, "kind": kind, "before": before, "after": after,
                "before_xml": _xml(current.get("a")), "after_xml": _xml(current.get("b")),
                "risk": risk, "effective": effective, "detail": detail}

    if dict(el_o.attrib) != dict(el_m.attrib):
        rows.append(row("@atributos", "atributo da regra (nome/UUID)", str(dict(el_o.attrib)),
                        str(dict(el_m.attrib)), HIGH, "n/a",
                        "nome e UUID de regra não podem mudar"))
    fo = {c.tag: c for c in el_o if isinstance(c.tag, str)}
    fm = {c.tag: c for c in el_m if isinstance(c.tag, str)}
    for tag in sorted(set(fo) | set(fm)):
        a, b = fo.get(tag), fm.get(tag)
        if a is not None and b is not None and canonical(a) == canonical(b):
            continue
        current["a"], current["b"] = a, b
        before, after = _text(a), _text(b)
        ro, rm = refs_o.get((xp_o, tag), []), refs_m.get((xp_m, tag), [])
        if tag in TAG_FIELDS:
            rows.append(row(tag, "tag de regra", before, after, HIGH, "n/a",
                            "tags de regra não podem mudar"))
        elif not ro and not rm:
            rows.append(row(tag, "campo sem referência a objeto", before, after, HIGH, "n/a",
                            "campo que não referencia objetos mudou"))
        else:
            diffs = _effective(state_o, state_m, ro, rm, scope)
            if diffs:
                ctx, only_b, only_a = diffs[0]
                rows.append(row(tag, "referência", before, after, HIGH,
                                f"DIFERE em {len(diffs)} contexto(s)",
                                f"{ctx}: só antes [{_fmt(only_b)}]; só depois [{_fmt(only_a)}]"))
            else:
                rows.append(row(tag, "referência", before, after, LOW,
                                "igual em todos os contextos",
                                f"{len(state_o.firewall_contexts(scope))} contexto(s) comparado(s)"))
    return rows


# ----------------------------------------------------------------------------- objetos

def _record_key(rec) -> tuple:
    return (rec.scope, rec.object_type, rec.name)


def _attr_diff(a, b) -> list:
    out = []
    if a.value_sha256 != b.value_sha256:
        out.append("valor")
    if a.members != b.members:
        out.append("membros")
    if a.description != b.description:
        out.append("descrição")
    if a.tags != b.tags:
        out.append("tags")
    if a.disable_override != b.disable_override:
        out.append("disable-override")
    return out


def _objects(an_o, an_m, state_o, state_m, cmp) -> None:
    ro = {_record_key(r): (i, r) for i, r in enumerate(an_o.inventory.records)}
    rm = {_record_key(r): (i, r) for i, r in enumerate(an_m.inventory.records)}
    leaf_index = defaultdict(list)
    for obj in state_m.objects:
        if obj.leaf is not None:
            leaf_index[(obj.object_type, obj.leaf)].append(obj)
    by_type = defaultdict(Counter)
    scope_counts = defaultdict(Counter)
    for key in sorted(set(ro) | set(rm)):
        scope, typ, name = key
        in_o, in_m = key in ro, key in rm
        by_type[typ]["original"] += in_o
        by_type[typ]["modified"] += in_m
        scope_counts[(scope, typ)]["original"] += in_o
        scope_counts[(scope, typ)]["modified"] += in_m
        if in_o and in_m:
            diff = _attr_diff(ro[key][1], rm[key][1])
            if not diff:
                continue
            kind = "Modificado"
            by_type[typ]["modified_entries"] += 1
            scope_counts[(scope, typ)]["modified_entries"] += 1
            used_o = an_o.usage[ro[key][0]].status
            if "valor" in diff or "membros" in diff:
                risk = HIGH if used_o == "USED" else MEDIUM
            elif "tags" in diff:
                risk = MEDIUM
            else:
                risk = INFO
            cmp.object_changes.append(_obj_row(kind, risk, key, ro[key], rm[key], an_o, an_m,
                                               ", ".join(diff), ""))
        elif in_o:
            by_type[typ]["removed"] += 1
            scope_counts[(scope, typ)]["removed"] += 1
            used_o = an_o.usage[ro[key][0]].status
            equiv = _equivalent(state_o.objects[ro[key][0]], state_m, leaf_index)
            risk = MEDIUM if used_o == "USED" else LOW
            cmp.object_changes.append(_obj_row("Removido", risk, key, ro[key], None, an_o, an_m,
                                               "", equiv))
        else:
            by_type[typ]["added"] += 1
            scope_counts[(scope, typ)]["added"] += 1
            cmp.object_changes.append(_obj_row("Adicionado", LOW, key, None, rm[key], an_o, an_m,
                                               "", ""))
    for typ in sorted(by_type):
        c = by_type[typ]
        cmp.object_types.append({"object_type": typ, "original": c["original"],
                                 "modified": c["modified"], "added": c["added"],
                                 "removed": c["removed"], "modified_entries": c["modified_entries"]})
    for (scope, typ), c in sorted(scope_counts.items(), key=lambda kv: (kv[0][0] != SHARED, kv[0])):
        cmp.object_scopes.append({"scope": scope, "object_type": typ,
                                  "original": c["original"], "modified": c["modified"],
                                  "added": c["added"], "removed": c["removed"],
                                  "modified_entries": c["modified_entries"]})
    cmp.classification = _classification(an_o, an_m)
    cmp.conflicts_by_type = _conflicts_by_type(an_o, an_m)


def _obj_row(kind, risk, key, orig, mod, an_o, an_m, changed, equivalent) -> dict:
    scope, typ, name = key
    rec_o = orig[1] if orig else None
    rec_m = mod[1] if mod else None
    return {"change": kind, "risk": risk, "scope": scope, "object_type": typ, "name": name,
            "changed_attributes": changed,
            "value_original": rec_o.value if rec_o else "[AUSENTE]",
            "value_modified": rec_m.value if rec_m else "[AUSENTE]",
            "description_original": rec_o.description if rec_o else "",
            "description_modified": rec_m.description if rec_m else "",
            "usage_original": an_o.usage[orig[0]].status if orig else "",
            "usage_modified": an_m.usage[mod[0]].status if mod else "",
            "visible_equivalent": equivalent,
            "xml_original": _xml(an_o.inventory.elements[orig[0]], 2500) if orig else "[AUSENTE]",
            "xml_modified": _xml(an_m.inventory.elements[mod[0]], 2500) if mod else "[AUSENTE]",
            "line_original": rec_o.source_line if rec_o else "",
            "line_modified": rec_m.source_line if rec_m else ""}


def _equivalent(old, state_m, leaf_index) -> str:
    """Objeto visível no modificado (do escopo do removido para cima) que o substitui."""
    if old.scope != SHARED and old.scope not in state_m.parent:
        return ""
    hits = state_m.resolve(old.scope, old.name, NAME_TYPES.get(old.object_type, (old.object_type,)))
    if hits:
        hit = hits[0]
        same = hit.leaf == old.leaf if old.leaf is not None else hit.members == old.members
        return f"{hit.scope}/{hit.name} ({'mesmo nome e valor' if same else 'mesmo nome, VALOR DIFERENTE'})"
    if old.leaf is not None:
        chain = set(state_m.chain(old.scope))
        cands = [o for o in leaf_index.get((old.object_type, old.leaf), []) if o.scope in chain]
        if cands:
            return "; ".join(f"{o.scope}/{o.name} (mesmo valor)" for o in cands[:3])
    return ""


def _conflicts_by_type(an_o, an_m) -> list:
    def count(an):
        c = Counter()
        for rec, st in zip(an.inventory.records, an.duplicates.name_status):
            if st.status == "CONFLICT":
                c[rec.object_type] += 1
        return c
    co, cm = count(an_o), count(an_m)
    return [{"object_type": t, "original": co[t], "modified": cm[t]} for t in sorted(set(co) | set(cm))]


def _classification(an_o, an_m) -> list:
    rows = []
    for label, getter in (
        ("Entradas duplicadas de mesmo nome e valor", lambda a: sum(
            1 for s in a.duplicates.name_status if s.status == "DUPLICATE")),
        ("Entradas com nome em conflito", lambda a: sum(
            1 for s in a.duplicates.name_status if s.status == "CONFLICT")),
        ("Objetos em grupos de possível duplicidade funcional",
         lambda a: len(a.duplicates.possible_group_of)),
        ("Objetos sem utilização identificada", lambda a: sum(
            1 for u in a.usage if u.status == "UNUSED")),
        ("Objetos com utilização desconhecida", lambda a: sum(
            1 for u in a.usage if u.status == "UNKNOWN")),
    ):
        rows.append({"classification": label, "original": getter(an_o), "modified": getter(an_m)})
    return rows


# ----------------------------------------------------------------------------- grupos

def _fmt_value(v) -> str:
    return " | ".join(str(x) for x in v) if isinstance(v, tuple) else str(v)


def _lines(values) -> str:
    return "\n".join(sorted(_fmt_value(v) for v in values))


def _direct_rule_refs(graph) -> dict:
    """(índice do objeto, contexto) -> referências diretas de regras."""
    out = defaultdict(list)
    for ref in graph.references:
        if ref.source_kind == "rule" and ref.target >= 0:
            out[(ref.target, ref.context)].append(ref)
    return out


def _groups(an_o, an_m, state_o, state_m, cmp) -> None:
    mod_by_key = {(o.scope, o.object_type, o.name): o for o in state_m.objects}
    pairs = []
    for obj in state_o.objects:
        other = mod_by_key.get((obj.scope, obj.object_type, obj.name))
        if other is not None and obj.leaf is None:
            pairs.append((obj, other))
    reach_o, reach_m = used_contexts(an_o.graph), used_contexts(an_m.graph)
    direct_o, direct_m = _direct_rule_refs(an_o.graph), _direct_rule_refs(an_m.graph)
    field_values_o, field_values_m = _refs_by_rule(an_o.graph), _refs_by_rule(an_m.graph)
    for ctx, obj_o, vals_o, vals_m, _ in compare_objects(state_o, state_m, pairs):
        obj_m = mod_by_key[(obj_o.scope, obj_o.object_type, obj_o.name)]
        in_o, in_m = (obj_o.key, ctx) in reach_o, (obj_m.key, ctx) in reach_m
        risk = HIGH if in_m else (MEDIUM if in_o else INFO)
        d_o, d_m = direct_o.get((obj_o.key, ctx), []), direct_m.get((obj_m.key, ctx), [])
        if in_m:
            manual = "Conferir: o valor diferente é alcançado por regra no modificado."
        elif in_o:
            manual = "Conferir: regras alcançavam o grupo no original e deixaram de alcançá-lo."
        else:
            manual = "Sem uso por regra nos dois arquivos; conferir consumidores locais antes do push."
        cmp.group_diffs.append({
            "context": ctx, "scope": obj_o.scope, "object_type": obj_o.object_type,
            "name": obj_o.name, "values_original": len(vals_o), "only_original_count": len(vals_o - vals_m),
            "common_count": len(vals_o & vals_m), "only_modified_count": len(vals_m - vals_o),
            "values_modified": len(vals_m),
            "only_original": _fmt(vals_o - vals_m), "only_modified": _fmt(vals_m - vals_o),
            "usage_original": f"{len(d_o)} referência(s) direta(s) de regra" + ("; alcançado" if in_o else "; não alcançado"),
            "usage_modified": f"{len(d_m)} referência(s) direta(s) de regra" + ("; alcançado" if in_m else "; não alcançado"),
            "reached_by_rules_original": "sim" if in_o else "não",
            "reached_by_rules_modified": "sim" if in_m else "não", "risk": risk, "manual_check": manual,
            "full_original": _lines(vals_o), "full_common": _lines(vals_o & vals_m),
            "full_modified": _lines(vals_m)})
        for ref in d_o:
            xp_m = cmp.rule_xpath_map.get(ref.source_xpath, "")
            root = ref.field.split("/")[0]
            cmp.group_rules.append({
                "group": obj_o.name, "object_type": obj_o.object_type, "context": ctx,
                "rule_scope": ref.source_scope, "rulebase": ref.source_type, "rule": ref.source_name,
                "field": ref.field, "original_reference": ref.value,
                "original_field_references": "; ".join(sorted({r.value for r in field_values_o.get((ref.source_xpath, root), [])})),
                "modified_field_references": "; ".join(sorted({r.value for r in field_values_m.get((xp_m, root), [])})),
                "xpath": ref.source_xpath})
    cmp.group_diffs.sort(key=lambda r: (r["risk"] != HIGH, r["risk"] != MEDIUM, r["context"], r["name"]))


# ----------------------------------------------------------------------------- referências

def _unresolved(an_o, an_m, cmp) -> None:
    from panorama_cleanup.analysis import unresolved_hint

    def collect(an):
        out = {}
        for r in an.graph.references:
            if r.resolution != "unresolved":
                continue
            key = (r.source_kind, r.source_scope, r.source_type, r.context, r.field, r.value)
            out.setdefault(key, [0, unresolved_hint(r), r.source_name])[0] += 1
        return out
    co, cm = collect(an_o), collect(an_m)
    cmp.summary["unresolved_original"] = sum(v[0] for v in co.values())
    cmp.summary["unresolved_modified"] = sum(v[0] for v in cm.values())
    for k in sorted(set(co) | set(cm)):
        kind, scope, typ, ctx, fld, value = k
        state = ("preexistente" if k in co and k in cm else
                 "NOVA" if k in cm else "resolvida no modificado")
        info = cm.get(k) or co.get(k)
        cmp.resolution_warnings.append({
            "state": state, "scope": scope, "source_type": typ, "source_kind": kind, "context": ctx,
            "field": fld, "value": value, "diagnosis": info[1],
            "occurrences_original": co[k][0] if k in co else 0,
            "occurrences_modified": cm[k][0] if k in cm else 0,
            "risk": HIGH if state == "NOVA" else INFO})
        if state == "NOVA":
            cmp.new_unresolved.append({
                "source_kind": kind, "source_scope": scope, "source_type": typ,
                "source_name": info[2], "field": fld, "value": value})


# ----------------------------------------------------------------------------- resumo

def _summary(cmp) -> None:
    s = cmp.summary
    s["rules_original"] = sum(r["original"] for r in cmp.rulebases)
    s["rules_modified"] = sum(r["modified"] for r in cmp.rulebases)
    status = Counter(r["status"] for r in cmp.rules)
    s["rules_identical"] = status["idêntica"]
    s["rules_field_changed"] = status["campos alterados"]
    s["rules_structural"] = sum(v for k, v in status.items()
                                if k not in ("idêntica", "campos alterados"))
    s["field_changes"] = len(cmp.rule_changes)
    s["field_changes_by_field"] = dict(Counter(c["field"] for c in cmp.rule_changes))
    s["field_changes_effective_equal"] = sum(1 for c in cmp.rule_changes if c["risk"] == LOW)
    s["field_changes_effective_differ"] = sum(1 for c in cmp.rule_changes if c["risk"] == HIGH)
    s["objects_original"] = sum(r["original"] for r in cmp.object_types)
    s["objects_modified"] = sum(r["modified"] for r in cmp.object_types)
    kinds = Counter(c["change"] for c in cmp.object_changes)
    s["objects_added"], s["objects_removed"] = kinds["Adicionado"], kinds["Removido"]
    s["objects_modified_entries"] = kinds["Modificado"]
    s["group_context_diffs"] = len(cmp.group_diffs)
    s["group_context_diffs_reached_by_rules"] = sum(
        1 for g in cmp.group_diffs if g["reached_by_rules_modified"] == "sim")
    s["sections_different"] = sum(1 for x in cmp.sections if x["status"] not in ("idêntica", "ignorada"))
    s["new_unresolved_references"] = len(cmp.new_unresolved)

    a = cmp.attention
    if s["rules_structural"]:
        a.append(f"{s['rules_structural']} regra(s) adicionada(s), removida(s), renomeada(s), "
                 "reordenada(s) ou com UUID diferente")
    if s["field_changes_effective_differ"]:
        a.append(f"{s['field_changes_effective_differ']} alteração(ões) de campo de regra com "
                 "valor efetivo diferente ou fora das regras permitidas")
    if any(r["relative_order_preserved"] != "sim" for r in cmp.rule_order):
        a.append("ordem relativa das regras não preservada em alguma rulebase")
    if s["group_context_diffs_reached_by_rules"]:
        a.append(f"{s['group_context_diffs_reached_by_rules']} par(es) grupo-contexto com valor "
                 "diferente alcançado(s) por regra no modificado")
    if s["sections_different"]:
        a.append(f"{s['sections_different']} seção(ões) fora de objetos e rulebases diferente(s)")
    if s["new_unresolved_references"]:
        a.append(f"{s['new_unresolved_references']} referência(s) nova(s) sem resolução")
    if a:
        cmp.verdict = VERDICT_DIFF
