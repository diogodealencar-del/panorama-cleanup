"""Orquestração do modo ANALYZE: dependências, uso e duplicidades (somente leitura)."""

from __future__ import annotations

from dataclasses import dataclass

from panorama_cleanup.dependency_analyzer import DependencyGraph, analyze_dependencies
from panorama_cleanup.duplicate_analyzer import (
    CONFLICT, DUPLICATE, POSSIBLE_DUPLICATE, DuplicateAnalysis, analyze_duplicates,
)
from panorama_cleanup.inventory import Inventory, build_inventory
from panorama_cleanup.unused_objects import classify_usage

PREDEFINED_SERVICES = frozenset({"service-http", "service-https"})


@dataclass
class Analysis:
    inventory: Inventory
    graph: DependencyGraph
    usage: list
    duplicates: DuplicateAnalysis

    @property
    def warnings(self) -> list:
        return self.inventory.warnings + self.graph.warnings

    def classifications(self, i: int) -> tuple:
        labels = [self.usage[i].status]
        name = self.duplicates.name_status[i].status
        if name in (DUPLICATE, CONFLICT):
            labels.append(name)
        if i in self.duplicates.possible_group_of:
            labels.append(POSSIBLE_DUPLICATE)
        if i in self.duplicates.context_variants:
            labels.append("CONTEXT_DEPENDENT")
        return tuple(labels)


def run_analysis(tree) -> Analysis:
    inv = build_inventory(tree)
    graph = analyze_dependencies(tree, inv)
    return Analysis(inv, graph, classify_usage(inv, graph), analyze_duplicates(inv))


def unresolved_hint(ref) -> str:
    """Explicação provável para referência não resolvida; não é prova."""
    if ref.resolution == "literal":
        return "endereço literal na regra"
    if ref.field == "source-user":
        return "usuário/grupo de diretório (não é objeto do Panorama)"
    if ref.field in ("application", "members") and ref.source_type.endswith(
            ("security", "application-override", "application-group")):
        return "provável App-ID predefinida"
    if ref.value in PREDEFINED_SERVICES:
        return "serviço predefinido do PAN-OS"
    if ref.value == "default":
        return "provável perfil predefinido"
    if ref.resolution == "unknown-hierarchy":
        return "hierarquia do Device Group desconhecida"
    return "não encontrado: predefinido ou referência quebrada; verificar"
