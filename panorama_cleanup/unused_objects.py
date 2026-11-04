"""Classificação de uso: USED / UNUSED / UNKNOWN (CLAUDE.md §7).

- USED: alcançável a partir de uma regra por referências confirmadas.
- UNKNOWN: alcançável apenas por algum caminho com referência possível, tipo
  não reconhecido, ou escopo com hierarquia desconhecida.
- UNUSED: nenhum caminho a partir de regras ou templates. Pode ainda ser
  referenciado por outros objetos não utilizados (ver reason e member_of).

Objetos UNKNOWN nunca são candidatos a remoção automática.
"""

from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass

from panorama_cleanup.dependency_analyzer import DependencyGraph, Resolver
from panorama_cleanup.inventory import Inventory

USED, UNUSED, UNKNOWN = "USED", "UNUSED", "UNKNOWN"
ROOT_KINDS = ("rule", "template")


@dataclass(frozen=True)
class UsageResult:
    status: str
    reason: str
    evidence: str
    direct_rule_refs: int
    inbound_object_refs: int
    member_of: tuple


def classify_usage(inv: Inventory, graph: DependencyGraph) -> list:
    resolver = Resolver(inv)
    confirmed_roots, possible_roots = {}, {}
    adjacency_confirmed, adjacency_all = defaultdict(set), defaultdict(set)
    rule_refs, inbound, member_of = defaultdict(set), defaultdict(set), defaultdict(set)

    for ref in graph.references:
        if ref.target < 0:
            continue
        if ref.source_kind in ROOT_KINDS:
            label = f"{ref.source_kind} {ref.source_scope}:{ref.source_type}:{ref.source_name} [{ref.field}]"
            if ref.source_kind == "rule":
                rule_refs[ref.target].add(ref.source_xpath)
            if ref.confidence == "confirmed":
                confirmed_roots.setdefault(ref.target, label)
            else:
                possible_roots.setdefault(ref.target, label + " (possível)")
        else:
            adjacency_all[ref.source_index].add(ref.target)
            inbound[ref.target].add(ref.source_index)
            src = inv.records[ref.source_index]
            member_of[ref.target].add(f"{src.scope}:{src.object_type}:{src.name}")
            if ref.confidence == "confirmed":
                adjacency_confirmed[ref.source_index].add(ref.target)

    used = _reach(confirmed_roots, adjacency_confirmed, inv)
    reachable = _reach({**possible_roots, **confirmed_roots}, adjacency_all, inv)

    results = []
    for i, rec in enumerate(inv.records):
        inbound_count = len(inbound[i])
        common = dict(direct_rule_refs=len(rule_refs[i]), inbound_object_refs=inbound_count,
                      member_of=tuple(sorted(member_of[i])))
        if i in used:
            direct = i in confirmed_roots
            results.append(UsageResult(USED, "referência direta" if direct else "referência indireta",
                                       used[i], **common))
        elif not rec.recognized:
            results.append(UsageResult(UNKNOWN, "tipo de objeto não reconhecido", "", **common))
        elif i in reachable:
            results.append(UsageResult(UNKNOWN, "alcançável apenas por referência possível",
                                       reachable[i], **common))
        elif not resolver.hierarchy_known(rec.scope):
            results.append(UsageResult(UNKNOWN, "hierarquia do Device Group desconhecida", "", **common))
        elif inbound_count:
            results.append(UsageResult(UNUSED, "referenciado apenas por objetos não utilizados", "",
                                       **common))
        else:
            results.append(UsageResult(UNUSED, "nenhuma referência", "", **common))
    return results


def _reach(roots: dict, adjacency, inv) -> dict:
    """BFS a partir das raízes; devolve índice -> cadeia de evidência."""
    evidence = {}
    queue = deque()
    for idx, label in roots.items():
        evidence[idx] = label
        queue.append(idx)
    while queue:
        current = queue.popleft()
        for nxt in adjacency.get(current, ()):
            if nxt in evidence:
                continue
            rec = inv.records[current]
            evidence[nxt] = f"{evidence[current]} > {rec.scope}:{rec.object_type}:{rec.name}"
            queue.append(nxt)
    return evidence
