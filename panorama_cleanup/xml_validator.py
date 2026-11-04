"""Validação estrutural e semântica do XML transformado (CLAUDE.md §8 e §18).

Cada verificação devolve um Check. A transformação só é aceita se todas passarem.
"""

from __future__ import annotations

import copy
from collections import Counter
from dataclasses import dataclass

from panorama_cleanup.analysis import run_analysis
from panorama_cleanup.effective_model import (
    State, compare, objects_from_inventory, rule_refs_from_graph,
)
from panorama_cleanup.planner import TAG_FIELDS
from panorama_cleanup.policy_comparator import DIFFERENT, canonical, compare_policies


@dataclass(frozen=True)
class Check:
    name: str
    passed: bool
    detail: str


def validate_transformation(original, transformed, result, operations, phases=(1, 2, 3)) -> tuple:
    """Retorna (checks, linhas de comparação de políticas)."""
    from panorama_cleanup.shared_converter import replace_reference

    checks = []
    add = lambda name, ok, detail: checks.append(Check(name, bool(ok), detail))  # noqa: E731

    ro, rt = original.getroot(), transformed.getroot()
    add("raiz e versão preservadas", ro.tag == rt.tag and dict(ro.attrib) == dict(rt.attrib),
        f"{dict(rt.attrib)}")

    expected_ops = Counter(o["operation"] for o in operations if int(o["phase"]) in phases)
    applied = Counter(c.operation for c in result.changes)
    label = "-".join(str(p) for p in (phases[0], phases[-1])) if len(phases) > 1 else str(phases[0])
    add(f"todas as operações da(s) fase(s) {label} aplicadas", expected_ops == applied,
        ", ".join(f"{k}={v}" for k, v in sorted(applied.items())))

    # 1. Fora das operações planejadas, nada mudou.
    expected = copy.deepcopy(original)
    for xpath in result.deleted_xpaths:
        (el,) = expected.xpath(xpath)
        el.getparent().remove(el)
    for (xpath, field_path, old), new in result.rule_renames.items():
        (el,) = expected.xpath(xpath)
        replace_reference(el, field_path, old, new)
    for xpath, field_path, old, new in result.object_renames:
        (el,) = expected.xpath(xpath)
        replace_reference(el, field_path, old, new)
    actual = copy.deepcopy(transformed)
    for xpath, _ in result.created_xpaths:
        (el,) = actual.xpath(xpath)
        container = el.getparent()
        container.remove(el)
        # Contêiner criado pela conversão (não existia em Shared) sai junto.
        while (container is not None and not len(container)
               and not original.xpath(actual.getpath(container))):
            parent = container.getparent()
            parent.remove(container)
            container = parent
    add("configuração fora do plano idêntica (C14N)",
        canonical(expected.getroot()) == canonical(actual.getroot()),
        "comparado sem espaços de formatação")

    # 2. Cópias criadas = objeto original, exceto nome e membros planejados.
    bad = []
    for created_xpath, source_xpath in result.created_xpaths:
        (src,) = original.xpath(source_xpath)
        (new,) = transformed.xpath(created_xpath)
        expected_entry = copy.deepcopy(src)
        expected_entry.set("name", new.get("name"))
        for field_path, old, new_name in result.copy_member_renames.get(source_xpath, ()):
            replace_reference(expected_entry, field_path, old, new_name)
        if canonical(expected_entry) != canonical(new):
            bad.append(created_xpath)
    add("cópias em Shared idênticas aos originais (valor, tags, descrição)", not bad,
        f"{len(result.created_xpaths)} conferidas; divergentes: {bad[:5]}")

    # 3. Políticas.
    policy_rows = compare_policies(original, transformed, result.rule_renames)
    different = [r for r in policy_rows if r["status"] == DIFFERENT]
    updated = sum(1 for r in policy_rows if r["status"] != "idêntica")
    add("políticas: ordem, UUID e conteúdo preservados", not different,
        f"{len(policy_rows)} regras; {updated} com referência atualizada; {len(different)} divergentes")
    add("nenhuma alteração em campos de tag de regras ou objetos",
        all(f not in TAG_FIELDS for (_, f, _) in result.rule_renames)
        and all(f not in TAG_FIELDS for (_, f, _, _) in result.object_renames), "")

    # 4. Equivalência efetiva por firewall.
    an_before, an_after = run_analysis(original), run_analysis(transformed)
    before = State(objects_from_inventory(an_before.inventory), an_before.inventory.device_groups)
    after = State(objects_from_inventory(an_after.inventory), an_after.inventory.device_groups)
    refs = rule_refs_from_graph(an_before.graph)
    diffs = compare(before, after, refs, result.rule_renames)
    checked = sum(len(before.firewall_contexts(r.source_scope)) for r in refs)
    add("valor efetivo idêntico em todos os firewalls", not diffs,
        f"{checked} verificações (referência de regra × DG); {len(diffs)} diferenças")

    refs_after = {(r.source_xpath, r.field, r.value) for r in rule_refs_from_graph(an_after.graph)}
    mapped = {(r.source_xpath, r.field, result.rule_renames.get((r.source_xpath, r.field, r.value), r.value))
              for r in refs}
    add("referências de regra: mesmo conjunto após renomeação", refs_after == mapped,
        f"{len(mapped)} referências")

    known = {r.name for r in an_before.inventory.records}
    broken_before = {(o.scope, o.name, f, n) for o, f, n in before.unresolved_object_refs(known)}
    broken_after = [(o.scope, o.name, f, n) for o, f, n in after.unresolved_object_refs(known)]
    new_broken = [b for b in broken_after if b not in broken_before]
    add("membros e tags de objetos resolvem no próprio escopo", not new_broken,
        f"novas quebras: {new_broken[:5]}")

    tags_before = {(r.scope, r.object_type, r.name): r.tags for r in an_before.inventory.records}
    moved = {}
    for created_xpath, source_xpath in result.created_xpaths:
        moved[created_xpath] = source_xpath
    by_xpath_before = {r.xpath: r for r in an_before.inventory.records}
    tag_changes = []
    for r in an_after.inventory.records:
        origin = by_xpath_before.get(moved.get(r.xpath, r.xpath))
        if origin is not None and origin.tags != r.tags:
            tag_changes.append(r.xpath)
    add("tags de todos os objetos inalteradas", not tag_changes, f"divergentes: {tag_changes[:5]}")

    counts = Counter(r.scope for r in an_after.inventory.records)
    add("contagem de objetos coerente com o plano",
        len(an_after.inventory.records) == len(an_before.inventory.records)
        + len(result.created_xpaths) - len(result.deleted_xpaths),
        f"antes {len(an_before.inventory.records)}, depois {len(an_after.inventory.records)} "
        f"({dict(counts)})")
    return checks, policy_rows, an_after



def validate_end_to_end(original, final, rule_renames: dict, deleted_xpaths: list) -> list:
    """Original x final: políticas, valor efetivo e status dos objetos excluídos."""
    checks = []
    add = lambda name, ok, detail: checks.append(Check(name, bool(ok), detail))  # noqa: E731

    rows = compare_policies(original, final, rule_renames)
    different = [r for r in rows if r["status"] == DIFFERENT]
    add("ponta a ponta: políticas preservadas (original x final)", not different,
        f"{len(rows)} regras; {len(different)} divergentes")

    an_before, an_after = run_analysis(original), run_analysis(final)
    before = State(objects_from_inventory(an_before.inventory), an_before.inventory.device_groups)
    after = State(objects_from_inventory(an_after.inventory), an_after.inventory.device_groups)
    refs = rule_refs_from_graph(an_before.graph)
    diffs = compare(before, after, refs, rule_renames)
    add("ponta a ponta: valor efetivo idêntico em todos os firewalls", not diffs,
        f"{sum(len(before.firewall_contexts(r.source_scope)) for r in refs)} verificações; "
        f"{len(diffs)} diferenças")

    status = {r.xpath: an_before.usage[i].status for i, r in enumerate(an_before.inventory.records)}
    not_unused = [x for x in deleted_xpaths if status.get(x) != "UNUSED"]
    add("ponta a ponta: nenhum objeto USED ou UNKNOWN excluído", not not_unused,
        f"{len(deleted_xpaths)} excluídos; fora de UNUSED no original: {not_unused[:5]}")
    return checks
