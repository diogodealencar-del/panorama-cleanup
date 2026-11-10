"""Modelo de valores efetivos por firewall (critério de transparência, docs/arquitetura.md D17).

Um estado é o conjunto de objetos com seus escopos e nomes, mais as referências
das regras. Para cada contexto de Device Group (os firewalls daquele DG), cada
referência de regra é expandida até os valores finais: endereços normalizados,
serviços, perfis etc. Dois estados são equivalentes quando todas as expansões,
em todos os contextos, são idênticas.

A resolução segue D13: o nome é procurado do contexto para cima e o mais próximo
vence, inclusive para membros de grupos de Shared.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, replace

from panorama_cleanup.dependency_analyzer import (
    APPLICATION_TYPES, GROUP_MEMBER_ADDRESS_TYPES, SERVICE_TYPES, TAG_TYPES, parse_tag_filter,
)
from panorama_cleanup.duplicate_analyzer import normalize_address
from panorama_cleanup.xml_reader import SHARED, UNKNOWN

GROUP_MEMBER_TYPES = {
    "address-group": GROUP_MEMBER_ADDRESS_TYPES,
    "service-group": SERVICE_TYPES,
    "application-group": APPLICATION_TYPES,
}


# Tipos que disputam o mesmo nome na resolução (família de nomes).
NAME_TYPES = {
    "address-group": tuple(GROUP_MEMBER_ADDRESS_TYPES),
    "service-group": tuple(SERVICE_TYPES),
    "application-group": tuple(APPLICATION_TYPES),
}


@dataclass(frozen=True)
class ModelObject:
    key: int              # índice original em inv.records
    scope: str
    name: str
    object_type: str
    leaf: tuple           # valor final (None para grupos)
    members: tuple        # ((campo, nome, tipos), ...)
    dynamic: str          # filtro de grupo dinâmico ou None
    tags: tuple


@dataclass(frozen=True)
class RuleRef:
    source_xpath: str
    source_scope: str
    field: str
    value: str
    types: tuple


def objects_from_inventory(inv, tag_identity: str = "name+color") -> list:
    """tag_identity: "name+color" (estrito) ou "name" (cor tratada como cosmética)."""
    objects = []
    for i, (rec, el) in enumerate(zip(inv.records, inv.elements)):
        members, leaf, dynamic = (), None, None
        if rec.object_type == "address-group" and el.find("dynamic") is not None:
            dynamic = " ".join((el.findtext("dynamic/filter") or "").split())
        elif rec.object_type in GROUP_MEMBER_TYPES:
            field = "static" if rec.object_type == "address-group" else "members"
            members = tuple((field, m, GROUP_MEMBER_TYPES[rec.object_type]) for m in rec.members)
        elif rec.object_type == "profile-group":
            members = tuple(
                (child.tag, (m.text or "").strip(), (f"profiles/{child.tag}",))
                for child in el if isinstance(child.tag, str) and child.tag != "description"
                for m in child.findall("member")
            )
        elif rec.object_type == "address":
            leaf = ("address",) + normalize_address(rec)
        elif rec.object_type == "tag":
            leaf = ("tag", rec.name) if tag_identity == "name" else ("tag", rec.name, rec.value_sha256)
        else:
            leaf = (rec.object_type, rec.value_sha256)
        objects.append(ModelObject(i, rec.scope, rec.name, rec.object_type, leaf, members,
                                   dynamic, rec.tags))
    return objects


def used_contexts(graph) -> frozenset:
    """(chave do objeto, contexto) alcançados a partir de regras e templates.

    Um objeto é "usado no contexto" se alguma regra (ou template) avaliada nos
    firewalls desse DG chega até ele, direta ou indiretamente por membros de grupos
    resolvidos no mesmo contexto. Fora disso, o valor do objeto naquele contexto não
    entra em nenhuma regra.
    """
    adjacency = defaultdict(list)
    queue = []
    for ref in graph.references:
        if ref.target < 0:
            continue
        if ref.source_kind == "object":
            adjacency[(ref.source_index, ref.context)].append((ref.target, ref.context))
        else:
            queue.append((ref.target, ref.context))
    seen = set(queue)
    while queue:
        node = queue.pop()
        for nxt in adjacency.get(node, ()):
            if nxt not in seen:
                seen.add(nxt)
                queue.append(nxt)
    return frozenset(seen)


def rule_refs_from_graph(graph) -> list:
    seen = {}
    for ref in graph.references:
        if ref.source_kind != "rule":
            continue
        key = (ref.source_xpath, ref.field, ref.value)
        if key not in seen:
            seen[key] = RuleRef(ref.source_xpath, ref.source_scope, ref.field, ref.value, ref.types)
    return list(seen.values())


class State:
    def __init__(self, objects, device_groups):
        self.objects = list(objects)
        self.parent = {dg.name: dg.parent for dg in device_groups}
        self.paths = {dg.name: dg.path for dg in device_groups}
        self.by_scope = defaultdict(lambda: defaultdict(list))
        for obj in self.objects:
            self.by_scope[obj.scope][obj.name].append(obj)
        self._cache = {}

    def chain(self, context) -> list:
        if context == SHARED:
            return [SHARED]
        scopes, current = [context], self.parent[context]
        while current not in (SHARED, UNKNOWN):
            scopes.append(current)
            current = self.parent[current]
        return scopes + ([SHARED] if current == SHARED else [])

    def firewall_contexts(self, scope) -> list:
        """Contextos de DG (firewalls) onde uma definição do escopo vale."""
        if scope == SHARED:
            return list(self.parent)
        return [scope] + [n for n, path in self.paths.items() if scope in path[:-1]]

    def resolve(self, context, name, types) -> list:
        for scope in self.chain(context):
            hits = [o for o in self.by_scope[scope].get(name, ())
                    if types is None or o.object_type in types]
            if hits:
                return hits
        return []

    def visible_addresses(self, context) -> list:
        seen, out = set(), []
        for scope in self.chain(context):
            for name, objs in self.by_scope[scope].items():
                if name not in seen:
                    seen.add(name)
                    out += [o for o in objs if o.object_type == "address"]
        return out

    def expand(self, context, name, types) -> tuple:
        """(valores finais, chaves dos objetos atravessados)."""
        key = (context, name, types)
        if key in self._cache:
            return self._cache[key]
        self._cache[key] = (frozenset({("cycle", name)}), frozenset())
        hits = self.resolve(context, name, types)
        if not hits:
            result = (frozenset({("unresolved", name)}), frozenset())
        elif len(hits) > 1:
            result = (frozenset({("ambiguous", name)}), frozenset(o.key for o in hits))
        else:
            result = self._expand_object(context, hits[0])
        self._cache[key] = result
        return result

    def expand_object(self, context, obj) -> tuple:
        """Expansão de um objeto específico no contexto, independente de sombreamento."""
        return self._expand_object(context, obj)

    def _expand_object(self, context, obj) -> tuple:
        touched = {obj.key}
        if obj.leaf is not None:
            return frozenset({obj.leaf}), frozenset(touched)
        values = set()
        if obj.dynamic is not None:
            try:
                predicate, _ = parse_tag_filter(obj.dynamic)
            except ValueError:
                return frozenset({("dynamic-unparsed", obj.dynamic)}), frozenset(touched)
            for addr in self.visible_addresses(context):
                if predicate(set(addr.tags)):
                    values.add(addr.leaf)
                    touched.add(addr.key)
            return frozenset(values), frozenset(touched)
        for field, member, types in obj.members:
            v, t = self.expand(context, member, types)
            values |= v
            touched |= t
        return frozenset(values), frozenset(touched)

    def unresolved_object_refs(self, known_names: set) -> list:
        """Referências de objetos (membros e tags) que não resolvem no próprio escopo.

        Nomes que não existem em nenhum lugar do inventário (predefinidos) são aceitos.
        """
        problems = []
        for obj in self.objects:
            refs = list(obj.members) + [("tag", t, TAG_TYPES) for t in obj.tags]
            for field, name, types in refs:
                if name in known_names and not self.resolve(obj.scope, name, types):
                    problems.append((obj, field, name))
        return problems


def compare(before: State, after: State, refs_before: list, renames: dict) -> list:
    """Diferenças de expansão por contexto de firewall.

    renames: (source_xpath, field, valor antigo) -> valor novo, para regras.
    Retorna [(contexto, RuleRef, valores antes, valores depois, chaves atravessadas)].
    """
    diffs = []
    for ref in refs_before:
        new_value = renames.get((ref.source_xpath, ref.field, ref.value), ref.value)
        for context in before.firewall_contexts(ref.source_scope):
            v_before, t_before = before.expand(context, ref.value, ref.types)
            v_after, t_after = after.expand(context, new_value, ref.types)
            if v_before != v_after:
                diffs.append((context, ref, v_before, v_after, t_before | t_after))
    return diffs


def compare_objects(before: State, after: State, pairs: list, skip: frozenset = frozenset(),
                    used: frozenset = None) -> list:
    """Valor efetivo de cada grupo que continua existindo, em cada contexto de firewall.

    pairs: [(objeto antes, mesmo objeto depois)]. Cobre também grupos sem uso: o XML
    intermediário (fases 1-3) pode ir para produção sem a fase 4, e um grupo que muda de
    significado em silêncio quebraria quem passar a usá-lo.

    Contextos onde o objeto está sombreado no estado anterior (o nome resolve para outro
    objeto, como uma cópia local de mesmo nome) não são comparados: ali nenhum firewall
    usa esse objeto, e o significado do nome é o da cópia, verificada por conta própria.
    skip: pares (chave do objeto, contexto) também excluídos da comparação.
    used: se informado (used_contexts), um objeto de escopo ancestral (Shared) só é comparado
    nos DGs onde alguma regra o alcança: ali o valor dele não chega a regra nenhuma. Objetos
    de DG são sempre comparados no próprio DG, com ou sem uso.
    Retorna [(contexto, objeto antes, valores antes, valores depois, chaves atravessadas)].
    """
    diffs = []
    for obj_before, obj_after in pairs:
        if obj_before.leaf is not None:
            continue
        types = NAME_TYPES.get(obj_before.object_type, (obj_before.object_type,))
        for context in before.firewall_contexts(obj_before.scope):
            if (used is not None and context != obj_before.scope
                    and (obj_before.key, context) not in used):
                continue
            if (obj_before.key, context) in skip:
                continue
            if before.resolve(context, obj_before.name, types) != [obj_before]:
                continue
            v_before, t_before = before.expand_object(context, obj_before)
            v_after, t_after = after.expand_object(context, obj_after)
            if v_before != v_after:
                diffs.append((context, obj_before, v_before, v_after, t_before | t_after))
    return diffs


def with_changes(objects: list, scope_name: dict, removed: set, member_renames: dict) -> list:
    """Novo conjunto de objetos aplicando movimentos, remoções e renomeação de membros.

    scope_name: chave -> (novo escopo, novo nome); member_renames: (chave, campo, antigo) -> novo.
    """
    out = []
    for obj in objects:
        if obj.key in removed:
            continue
        scope, name = scope_name.get(obj.key, (obj.scope, obj.name))
        members = tuple(
            (f, member_renames.get((obj.key, f, n), n), t) for f, n, t in obj.members
        )
        tags = tuple(member_renames.get((obj.key, "tag", t), t) for t in obj.tags)
        out.append(replace(obj, scope=scope, name=name, members=members, tags=tags))
    return out
