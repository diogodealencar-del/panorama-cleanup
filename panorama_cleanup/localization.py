"""Fase 0: cópia local de grupos de Shared que mudam de valor conforme o Device Group.

Problema: um grupo de Shared (G) com membro X vale outra coisa nos firewalls de um
DG que tem um X local (o nome mais próximo vence, D13). Esse X local não pode ser
renomeado nem movido: G passaria a usar o X de Shared e o IP mudaria nas regras do DG.

Solução (transparente por construção): na fase 0, G ganha uma cópia local idêntica
no DG. No DG, o nome G passa a resolver para a cópia, cujos membros resolvem no mesmo
contexto, com o mesmo valor. A partir daí o planejador trata a cópia como qualquer
objeto local em conflito: ela vai para Shared como G_DG (regras do DG atualizadas) e
o X local fica livre para ir para Shared como X-IP. O G de Shared não muda.

Uma cópia só é mantida se o planejamento conseguir movê-la (RENAME_MOVE ou
CONSOLIDATE); as demais são descartadas e o planejamento é refeito sem elas, até
estabilizar. Assim o XML final nunca contém cópias locais criadas aqui.

Toda a prova de equivalência continua no planejador (effective_model) sobre o XML com
as cópias, e a Etapa 8 compara o XML final com o original verdadeiro (rule_equivalence).
"""

from __future__ import annotations

import copy
from dataclasses import dataclass

from lxml import etree

from panorama_cleanup.analysis import run_analysis
from panorama_cleanup.dependency_analyzer import Resolver
from panorama_cleanup.duplicate_analyzer import NAME_FAMILY
from panorama_cleanup.errors import PanoramaCleanupError
from panorama_cleanup.inventory import quote
from panorama_cleanup.xml_reader import SHARED

LOCALIZABLE_TYPES = ("address-group", "service-group")
KEPT_ACTIONS = ("RENAME_MOVE", "CONSOLIDATE")


class LocalizationError(PanoramaCleanupError):
    """Cópias locais da fase 0 inconsistentes."""


@dataclass
class Localized:
    tree: object          # XML com as cópias locais (base do plano e das fases 1-3)
    analysis: object      # análise sobre esse XML
    plan: object
    pairs: list           # [(tipo, nome, device group)]


def shared_xpath(object_type: str, name: str) -> str:
    return f"/config/shared/{object_type}/entry[@name={quote(name)}]"


def candidate_pairs(an) -> list:
    """(tipo, nome, DG) dos grupos de Shared cujo valor efetivo muda no DG."""
    inv, resolver = an.inventory, Resolver(an.inventory)
    pairs = set()
    for i, contexts in an.duplicates.context_variants.items():
        rec = inv.records[i]
        if rec.scope != SHARED or rec.object_type not in LOCALIZABLE_TYPES:
            continue
        family = NAME_FAMILY[rec.object_type]
        types = {t for t, f in NAME_FAMILY.items() if f == family}
        for dg in contexts:
            if dg == SHARED:
                continue
            resolution, hits = resolver.resolve(rec.name, types, dg)
            if resolution == "resolved" and hits == [i]:
                pairs.add((rec.object_type, rec.name, dg))
    return sorted(pairs)


def localize(tree, pairs: list):
    """Cópia do XML com as cópias locais. O original não é alterado."""
    work = copy.deepcopy(tree)
    for object_type, name, dg in pairs:
        create_local_copy(work, object_type, name, dg)
    return work


def create_local_copy(tree, object_type: str, name: str, dg: str) -> str:
    """Copia shared/<tipo>/entry[name] para o DG; devolve o xpath da cópia."""
    from panorama_cleanup.shared_converter import (
        _append_entry, _child_indent, _indent_of, _reindent,
    )
    from panorama_cleanup.xml_reader import panorama_entry
    found = tree.xpath(shared_xpath(object_type, name))
    if len(found) != 1:
        raise LocalizationError(f"Fase 0: {object_type} {name!r} não encontrado em Shared")
    source = found[0]
    device = panorama_entry(tree.getroot())
    groups = device.xpath("device-group/entry[@name=$n]", n=dg)
    if len(groups) != 1:
        raise LocalizationError(f"Fase 0: Device Group {dg!r} não encontrado")
    container = groups[0].find(object_type)
    if container is None:
        container = etree.SubElement(groups[0], object_type)
    if container.xpath("entry[@name=$n]", n=name):
        raise LocalizationError(f"Fase 0: {dg} já tem {object_type} {name!r}")
    entry = copy.deepcopy(source)
    _reindent(entry, _indent_of(source), _child_indent(container))
    _append_entry(container, entry)
    return tree.getpath(entry)


def _local_copy_key(inv, object_type, name, dg):
    for k, r in enumerate(inv.records):
        if r.scope == dg and r.object_type == object_type and r.name == name:
            return k
    raise LocalizationError(f"Fase 0: cópia {dg}:{object_type}:{name} ausente do inventário")


def plan_with_localization(original, max_rounds: int = 20) -> Localized:
    """Plano sobre o XML com as cópias locais úteis; determinístico."""
    from panorama_cleanup.planner import build_plan
    pairs = candidate_pairs(run_analysis(original))
    for _ in range(max_rounds):
        tree = localize(original, pairs) if pairs else original
        an = run_analysis(tree)
        keys = frozenset(_local_copy_key(an.inventory, *p) for p in pairs)
        plan = build_plan(an, forced_rename=keys)
        keep = [p for p in pairs
                if plan.decisions[_local_copy_key(an.inventory, *p)].action in KEPT_ACTIONS]
        if keep == pairs:
            plan.localizations = [
                {"key": _local_copy_key(an.inventory, *p), "object_type": p[0], "name": p[1],
                 "dg": p[2], "source_xpath": shared_xpath(p[0], p[1])}
                for p in pairs]
            return Localized(tree, an, plan, pairs)
        pairs = keep
    raise LocalizationError(f"Fase 0 não estabilizou em {max_rounds} rodadas")
