"""Duplicidades e conflitos de nome (CLAUDE.md §5).

- A. mesmo nome, mesmo valor efetivo em escopos diferentes: DUPLICATE.
- B. mesmo nome, valores diferentes (ou tipos diferentes): CONFLICT.
- C. nomes diferentes, mesmo valor efetivo: POSSIBLE_DUPLICATE (nunca consolidado
  automaticamente).

O valor efetivo de grupos é calculado resolvendo cada membro no contexto, de
forma recursiva: dois grupos com os mesmos nomes de membros, mas que resolvem
para objetos de valores diferentes, NÃO são iguais. Como membros podem ser
sobrepostos por objetos de mesmo nome em Device Groups descendentes, o valor
efetivo de um grupo é calculado em cada contexto onde ele vale; grupos cujo
valor muda conforme o Device Group são sinalizados (context_variants).
A normalização de valores é mínima e conservadora (docs/arquitetura.md, D15).
"""

from __future__ import annotations

import ipaddress
from collections import defaultdict
from dataclasses import dataclass

from panorama_cleanup.dependency_analyzer import (
    APPLICATION_TYPES, GROUP_MEMBER_ADDRESS_TYPES, SERVICE_TYPES, Resolver, _visible_addresses,
    parse_tag_filter,
)
from panorama_cleanup.inventory import Inventory

UNIQUE, DUPLICATE, CONFLICT = "UNIQUE", "DUPLICATE", "CONFLICT"
POSSIBLE_DUPLICATE = "POSSIBLE_DUPLICATE"

# Famílias de nome: tipos que compartilham o espaço de nomes na resolução.
NAME_FAMILY = {
    "address": "address", "address-group": "address",
    "service": "service", "service-group": "service",
    "application": "application", "application-group": "application",
    "application-filter": "application",
}
GROUP_TYPES = ("address-group", "service-group", "application-group")
VALUE_DUPLICATE_TYPES = ("address", "address-group", "service", "service-group",
                         "application-group")


@dataclass(frozen=True)
class NameStatus:
    status: str
    group: str            # família:nome
    cluster: int          # 0 quando UNIQUE; mesmo número = mesmo valor efetivo
    clusters: int
    literal_equal: bool
    tags_equal: bool
    description_equal: bool


@dataclass(frozen=True)
class DuplicateAnalysis:
    name_status: list     # paralelo a inv.records
    possible_groups: list  # [(id, tipo, [índices])]
    possible_group_of: dict  # índice -> id
    context_variants: dict   # índice -> contextos onde o valor efetivo difere do próprio escopo


def analyze_duplicates(inv: Inventory) -> DuplicateAnalysis:
    resolver = Resolver(inv)
    fingerprints = _Fingerprinter(inv, resolver)

    by_name = defaultdict(list)
    for i, rec in enumerate(inv.records):
        by_name[(NAME_FAMILY.get(rec.object_type, rec.object_type), rec.name)].append(i)

    status = [NameStatus(UNIQUE, "", 0, 0, True, True, True)] * len(inv.records)
    for (family, name), indexes in by_name.items():
        if len(indexes) < 2:
            continue
        keys = [(inv.records[i].object_type, fingerprints.of(i)) for i in indexes]
        cluster_ids = {}
        for k in keys:
            cluster_ids.setdefault(k, len(cluster_ids) + 1)
        same = len(cluster_ids) == 1
        literal_equal = len({(inv.records[i].object_type, inv.records[i].value_sha256)
                             for i in indexes}) == 1
        tags_equal = len({tuple(sorted(inv.records[i].tags)) for i in indexes}) == 1
        description_equal = len({inv.records[i].description for i in indexes}) == 1
        for i, k in zip(indexes, keys):
            status[i] = NameStatus(
                DUPLICATE if same else CONFLICT, f"{family}:{name}", cluster_ids[k],
                len(cluster_ids), literal_equal, tags_equal, description_equal,
            )

    by_value = defaultdict(list)
    for i, rec in enumerate(inv.records):
        if rec.object_type in VALUE_DUPLICATE_TYPES:
            by_value[(rec.object_type, fingerprints.of(i))].append(i)
    possible_groups, possible_group_of = [], {}
    for (object_type, _), indexes in sorted(by_value.items(), key=lambda kv: min(kv[1])):
        if len({inv.records[i].name for i in indexes}) < 2:
            continue
        group_id = f"PD-{len(possible_groups) + 1:05d}"
        possible_groups.append((group_id, object_type, indexes))
        for i in indexes:
            possible_group_of[i] = group_id
    context_variants = {}
    for i, rec in enumerate(inv.records):
        if rec.object_type not in GROUP_TYPES:
            continue
        contexts = resolver.contexts(rec.scope)[0]
        own = fingerprints.of(i, rec.scope)
        differing = [c for c in contexts[1:] if fingerprints.of(i, c) != own]
        if differing:
            context_variants[i] = tuple(differing)
    return DuplicateAnalysis(status, possible_groups, possible_group_of, context_variants)


def normalize_address(rec) -> tuple:
    """Normalização mínima: IP sem máscara = /32 ou /128; FQDN sem diferença de caixa."""
    kind, _, value = rec.value.partition("=")
    if kind == "ip-netmask":
        try:
            if "/" not in value:
                ip = ipaddress.ip_address(value)
                return kind, f"{ip}/{ip.max_prefixlen}"
            address, prefix = value.split("/", 1)
            return kind, f"{ipaddress.ip_address(address)}/{int(prefix)}"
        except ValueError:
            return kind, value
    if kind == "fqdn":
        return kind, value.lower().rstrip(".")
    return kind, value


class _Fingerprinter:
    """Valor efetivo de cada objeto em um contexto (escopo de avaliação)."""

    def __init__(self, inv, resolver):
        self.inv, self.resolver = inv, resolver
        self.cache, self.active = {}, set()

    def of(self, i: int, context: str = None):
        rec = self.inv.records[i]
        context = context or rec.scope
        key = (i, context)
        if key in self.cache:
            return self.cache[key]
        if key in self.active:
            return ("cycle", rec.name)
        self.active.add(key)
        el = self.inv.elements[i]
        if rec.object_type == "address":
            fp = ("address",) + normalize_address(rec)
        elif rec.object_type == "address-group" and el.find("dynamic") is not None:
            fp = self._dynamic(el, context)
        elif rec.object_type == "address-group":
            fp = ("static", self._members(rec, GROUP_MEMBER_ADDRESS_TYPES, context))
        elif rec.object_type == "service-group":
            fp = ("service-group", self._members(rec, SERVICE_TYPES, context))
        elif rec.object_type == "application-group":
            fp = ("application-group", self._members(rec, APPLICATION_TYPES, context))
        else:
            fp = (rec.object_type, rec.value_sha256)
        self.active.discard(key)
        self.cache[key] = fp
        return fp

    def _members(self, rec, types, context) -> frozenset:
        """Membros resolvidos no contexto; ordem de membros não altera o valor."""
        out = []
        for name in rec.members:
            resolution, hits = self.resolver.resolve(name, types, context)
            if resolution == "resolved":
                out.append(self.of(hits[0], context))
            else:
                out.append((resolution, name))
        return frozenset(out)

    def _dynamic(self, el, context):
        expression = " ".join((el.findtext("dynamic/filter") or "").split())
        try:
            predicate, _ = parse_tag_filter(expression)
        except ValueError:
            return ("dynamic-unparsed", expression)
        members = frozenset(
            self.of(idx, context) for idx in _visible_addresses(self.resolver, context)
            if predicate(set(self.inv.records[idx].tags))
        )
        return ("dynamic", expression, members)
