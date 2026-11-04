"""Análise de dependências: extração e resolução de referências (CLAUDE.md §7 e §12).

Resolução de nomes (evidências em docs/arquitetura.md, D13):

- Sem a opção "Objects defined in ancestors will take higher precedence", o
  objeto do escopo mais próximo vence: Device Group, ancestrais e, por fim,
  Shared. Só essa cópia é enviada ao firewall.
- Toda definição (regra ou objeto) no escopo S é avaliada em S e em cada Device
  Group descendente de S (para Shared: todos). Em cada contexto o nome é
  resolvido subindo a hierarquia a partir do contexto. Quando o alvo difere do
  resolvido no próprio escopo da definição, a resolução é marcada "override".
- Campos não mapeados de regras, objetos sem esquema de referência conhecido e
  templates geram apenas referências possíveis, por coincidência de nome.

Referência possível nunca torna um objeto USED: leva no máximo a UNKNOWN.
"""

from __future__ import annotations

import ipaddress
import re
from collections import Counter, defaultdict
from dataclasses import dataclass

from lxml import etree

from panorama_cleanup.errors import AnalysisError
from panorama_cleanup.inventory import METADATA_CHILDREN, Inventory, quote, scope_xpath
from panorama_cleanup.xml_reader import SHARED, UNKNOWN, panorama_entry

ADDRESS_TYPES = ("address", "address-group", "external-list", "region")
GROUP_MEMBER_ADDRESS_TYPES = ("address", "address-group")
SERVICE_TYPES = ("service", "service-group")
APPLICATION_TYPES = ("application", "application-group", "application-filter")
TAG_TYPES = ("tag",)
URL_CATEGORY_TYPES = ("profiles/custom-url-category", "external-list")
ANY_TYPE = None  # casa com qualquer tipo inventariado

KEYWORDS = frozenset({"", "any", "application-default", "none"})

# Campo da regra (caminho sem o /member final) -> tipos que podem ser referenciados.
RULE_FIELD_TYPES = (
    (re.compile(r"^(source|destination)$"), ADDRESS_TYPES),
    (re.compile(r"^source-user$"), ("dynamic-user-group",)),
    (re.compile(r"^service$"), SERVICE_TYPES),
    (re.compile(r"^application$"), APPLICATION_TYPES),
    (re.compile(r"^category$"), URL_CATEGORY_TYPES),
    (re.compile(r"^(tag|group-tag)$"), TAG_TYPES),
    (re.compile(r"^profile-setting/group$"), ("profile-group",)),
    (re.compile(r"^profile-setting/profiles/(?P<kind>[\w-]+)$"), "profiles/{kind}"),
    (re.compile(r"^log-setting$"), ("log-settings/profiles",)),
    (re.compile(r"^schedule$"), ("schedule",)),
    (re.compile(r"^(source|destination)-hip$"), ("profiles/hip-profiles",)),
    (re.compile(r"(^|/)translated-address$"), ADDRESS_TYPES),
    (re.compile(r"^profile$"), ("profiles/decryption",)),
)
# Campos de regra que não referenciam objetos inventariáveis (zonas, interfaces,
# ações, flags, portas literais, alvo de dispositivos).
RULE_NON_REFERENCE = re.compile(
    r"^(action|description|disabled|from|to|from/zone|from/interface|log-start|log-end"
    r"|negate-[\w-]+|option/[\w/-]+|rule-type|target(/.*)?|to-interface|port|protocol"
    r"|action/forward/egress-interface|enforce-symmetric-return/[\w/-]+"
    r"|.*translated-port|.*bi-directional)$"
)
# Tipos de objeto cujas referências internas não têm esquema mapeado: varredura
# genérica por coincidência de nome, sempre como referência possível.
OBJECT_FALLBACK_TYPES = ("application", "external-list", "log-settings/profiles")
OBJECT_NO_REFERENCES = ("address", "service", "tag", "schedule", "region")


@dataclass(frozen=True)
class Reference:
    source_kind: str      # rule | object | template
    source_scope: str
    source_type: str      # ex.: post-rulebase/security, address-group, template
    source_name: str
    source_xpath: str
    source_index: int     # índice do objeto de origem em inv.records; -1 para regra/template
    rule_disabled: bool
    field: str
    value: str
    context: str
    target: int           # índice em inv.records; -1 quando não resolvido
    confidence: str       # confirmed | possible | none
    resolution: str       # resolved | override | ambiguous | unknown-hierarchy | unmapped-field
                          # | dynamic-membership | template-match | literal | unresolved
    types: tuple = None   # tipos que o campo pode referenciar; None = qualquer tipo


@dataclass
class DependencyGraph:
    references: list
    warnings: list
    unmapped_fields: Counter


class Resolver:
    """Resolve nomes considerando hierarquia e herança dos Device Groups."""

    def __init__(self, inv: Inventory):
        self.records = inv.records
        self.by_scope = defaultdict(lambda: defaultdict(list))
        self.by_name = defaultdict(list)
        for i, r in enumerate(inv.records):
            self.by_scope[r.scope][r.name].append(i)
            self.by_name[r.name].append(i)
        self.groups = {dg.name: dg for dg in inv.device_groups}

    def chain(self, context: str) -> tuple:
        """Escopos consultados a partir do contexto e se a cadeia é completa."""
        if context == SHARED:
            return [SHARED], True
        scopes, current = [context], self.groups[context].parent
        while current not in (SHARED, UNKNOWN):
            scopes.append(current)
            current = self.groups[current].parent
        if current == UNKNOWN:
            return scopes, False
        return scopes + [SHARED], True

    def descendants(self, scope: str) -> list:
        if scope == SHARED:
            return list(self.groups)
        return [n for n, dg in self.groups.items() if scope in dg.path[:-1]]

    def hierarchy_known(self, scope: str) -> bool:
        return scope == SHARED or self.chain(scope)[1]

    def _match(self, indexes, types) -> list:
        if types is ANY_TYPE:
            return list(indexes)
        return [i for i in indexes if self.records[i].object_type in types]

    def resolve(self, name: str, types, context: str) -> tuple:
        """Retorna (resolution, [índices]) para o nome visto a partir do contexto."""
        scopes, complete = self.chain(context)
        for scope in scopes:
            hits = self._match(self.by_scope[scope].get(name, ()), types)
            if len(hits) == 1:
                return "resolved", hits
            if hits:
                return "ambiguous", hits
        if not complete:
            hits = [i for i in self._match(self.by_name.get(name, ()), types)
                    if self.records[i].scope not in scopes]
            return "unknown-hierarchy", hits
        return "unresolved", []

    def contexts(self, scope: str, is_rule: bool = False) -> tuple:
        """(contextos confirmados, contextos possíveis) para uma definição no escopo.

        Regras e objetos seguem a mesma regra: o escopo da definição e todos os
        descendentes. O primeiro contexto é sempre o próprio escopo.
        """
        return [scope] + self.descendants(scope), []


def check_precedence_setting(tree: etree._ElementTree) -> None:
    """Interrompe se houver configuração de precedência de objetos ancestrais."""
    device = panorama_entry(tree.getroot())
    for el in device.iter():
        if isinstance(el.tag, str) and "precedence" in el.tag:
            value = (el.text or "").strip()
            if value != "no":
                raise AnalysisError(
                    f"Configuração <{el.tag}>{value}</{el.tag}> encontrada (linha {el.sourceline}). "
                    "A resolução com precedência de ancestrais não está implementada; "
                    "análise interrompida."
                )


def analyze_dependencies(tree: etree._ElementTree, inv: Inventory) -> DependencyGraph:
    check_precedence_setting(tree)
    resolver = Resolver(inv)
    refs, warnings, unmapped = [], [], Counter()

    for dg in inv.device_groups:
        if not resolver.hierarchy_known(dg.name):
            warnings.append(
                f"Device Group {dg.name}: hierarquia desconhecida; referências que não "
                "resolvem no próprio escopo ficam como possíveis"
            )

    _rule_references(tree, inv, resolver, refs, unmapped)
    _object_references(inv, resolver, refs, warnings)
    _template_references(tree, inv, resolver, refs)

    for ref in refs:
        if ref.resolution == "ambiguous":
            target = inv.records[ref.target]
            warnings.append(
                f"Referência ambígua: {ref.source_type} {ref.source_name!r} ({ref.source_scope}) "
                f"campo {ref.field} = {ref.value!r} casa com mais de um tipo em "
                f"{target.scope}; tratada como possível"
            )
    for field_path, count in sorted(unmapped.items()):
        warnings.append(
            f"Campo de regra não mapeado {field_path!r} ({count} valores): coincidências "
            "de nome tratadas como referência possível"
        )
    return DependencyGraph(refs, list(dict.fromkeys(warnings)), unmapped)


# --- regras -------------------------------------------------------------------

def _rule_references(tree, inv, resolver, refs, unmapped) -> None:
    root = tree.getroot()
    scope_elements = [(SHARED, root.find("shared"))]
    device = panorama_entry(root)
    scope_elements += [(e.get("name"), e) for e in device.findall("device-group/entry")]

    for scope, element in scope_elements:
        if element is None:
            continue
        base = scope_xpath(inv.metadata, scope)
        confirmed, possible = resolver.contexts(scope, is_rule=True)
        for rulebase in ("pre-rulebase", "post-rulebase"):
            rb = element.find(rulebase)
            if rb is None:
                continue
            for kind in rb:
                if not isinstance(kind.tag, str):
                    continue
                source_type = f"{rulebase}/{kind.tag}"
                for rule in kind.findall("rules/entry"):
                    name = rule.get("name", "")
                    meta = dict(
                        source_kind="rule",
                        source_scope=scope,
                        source_type=source_type,
                        source_name=name,
                        source_xpath=f"{base}/{source_type}/rules/entry[@name={quote(name)}]",
                        source_index=-1,
                        rule_disabled=(rule.findtext("disabled") or "").strip() == "yes",
                    )
                    for field_path, value in _rule_leaves(rule):
                        if value in KEYWORDS:
                            continue
                        mapped = _rule_field_types(field_path)
                        if mapped == "skip":
                            continue
                        if mapped is None:
                            unmapped[f"{source_type}:{field_path}"] += 1
                            _emit_fallback(refs, meta, resolver, field_path, value, confirmed)
                            continue
                        _emit(refs, meta, resolver, field_path, value, mapped, confirmed, possible)


def _rule_leaves(rule) -> list:
    leaves = []
    for el in rule.iter():
        if el is rule or not isinstance(el.tag, str):
            continue
        if any(isinstance(c.tag, str) for c in el):
            continue
        parts, parent = [], el.getparent()
        while parent is not rule:
            parts.append(parent.tag)
            parent = parent.getparent()
        path = "/".join(reversed(parts))
        if el.tag != "member":
            path = f"{path}/{el.tag}" if path else el.tag
        leaves.append((path, (el.text or "").strip()))
    return leaves


def _rule_field_types(field_path: str):
    if RULE_NON_REFERENCE.match(field_path):
        return "skip"
    for pattern, types in RULE_FIELD_TYPES:
        match = pattern.search(field_path)
        if match:
            if isinstance(types, str):
                return (types.format(**match.groupdict()),)
            return types
    return None


# --- objetos ------------------------------------------------------------------

def _object_references(inv, resolver, refs, warnings) -> None:
    for i, (rec, el) in enumerate(zip(inv.records, inv.elements)):
        confirmed, possible = resolver.contexts(rec.scope, is_rule=False)
        meta = dict(
            source_kind="object",
            source_scope=rec.scope,
            source_type=rec.object_type,
            source_name=rec.name,
            source_xpath=rec.xpath,
            source_index=i,
            rule_disabled=False,
        )

        for tag in rec.tags:
            _emit(refs, meta, resolver, "tag", tag, TAG_TYPES, confirmed, possible)

        if rec.object_type == "address-group":
            for member in rec.members:
                _emit(refs, meta, resolver, "static", member, GROUP_MEMBER_ADDRESS_TYPES,
                      confirmed, possible)
            dynamic = el.findtext("dynamic/filter")
            if dynamic is not None:
                _dynamic_references(inv, resolver, refs, warnings, meta, dynamic.strip(),
                                    confirmed, possible)
        elif rec.object_type == "service-group":
            for member in rec.members:
                _emit(refs, meta, resolver, "members", member, SERVICE_TYPES, confirmed, possible)
        elif rec.object_type == "application-group":
            for member in rec.members:
                _emit(refs, meta, resolver, "members", member, APPLICATION_TYPES,
                      confirmed, possible)
        elif rec.object_type == "profile-group":
            for child in el:
                if not isinstance(child.tag, str) or child.tag in METADATA_CHILDREN:
                    continue
                for m in child.findall("member"):
                    _emit(refs, meta, resolver, child.tag, (m.text or "").strip(),
                          (f"profiles/{child.tag}",), confirmed, possible)
        elif rec.object_type in OBJECT_NO_REFERENCES or rec.object_type == "application-filter":
            pass
        elif (rec.object_type in OBJECT_FALLBACK_TYPES or rec.object_type.startswith("profiles/")
              or not rec.recognized):
            for child in el:
                if not isinstance(child.tag, str) or child.tag in METADATA_CHILDREN:
                    continue
                for leaf in child.iter():
                    if not isinstance(leaf.tag, str) or any(isinstance(c.tag, str) for c in leaf):
                        continue
                    value = (leaf.text or "").strip()
                    if value in KEYWORDS or value == rec.name:
                        continue
                    _emit_fallback(refs, meta, resolver, child.tag, value, confirmed)


def _dynamic_references(inv, resolver, refs, warnings, meta, expression, confirmed, possible):
    try:
        predicate, tag_names = parse_tag_filter(expression)
    except ValueError as exc:
        warnings.append(
            f"Filtro dinâmico não interpretado em {meta['source_name']!r} ({meta['source_scope']}): "
            f"{exc}; membros tratados como possíveis"
        )
        predicate, tag_names = None, _quoted_tokens(expression)

    for tag in tag_names:
        if predicate is not None:
            _emit(refs, meta, resolver, "dynamic/filter", tag, TAG_TYPES, confirmed, possible)
            continue
        # Filtro não interpretado: a extração das tags é heurística.
        for context in confirmed + possible:
            for idx in resolver.resolve(tag, TAG_TYPES, context)[1]:
                refs.append(Reference(**meta, field="dynamic/filter", value=tag, context=context,
                                      target=idx, confidence="possible",
                                      resolution="unmapped-field"))

    for contexts, confidence in ((confirmed, "confirmed"), (possible, "possible")):
        for context in contexts:
            for idx in _visible_addresses(resolver, context):
                tags = set(inv.records[idx].tags)
                if predicate is None:
                    matched, conf = bool(tags & set(tag_names)), "possible"
                else:
                    matched, conf = predicate(tags), confidence
                if matched:
                    refs.append(Reference(
                        **meta, field="dynamic/filter", value=expression, context=context,
                        target=idx, confidence=conf, resolution="dynamic-membership",
                    ))


def _visible_addresses(resolver, context) -> list:
    """Objetos address visíveis no contexto; o mais próximo vence em nomes repetidos."""
    seen, visible = set(), []
    scopes, _ = resolver.chain(context)
    for scope in scopes:
        for name, indexes in resolver.by_scope[scope].items():
            if name in seen:
                continue
            seen.add(name)
            visible += [i for i in indexes if resolver.records[i].object_type == "address"]
    return visible


_TOKEN = re.compile(r"\s*(?:(\()|(\))|'([^']*)'|\"([^\"]*)\"|([^\s()'\"]+))")


def parse_tag_filter(expression: str) -> tuple:
    """Interpreta filtro de address group dinâmico: tags entre aspas, and/or/not, parênteses.

    Retorna (predicado sobre conjunto de tags, lista de tags citadas).
    """
    tokens, pos = [], 0
    text = expression.strip()
    while pos < len(text):
        m = _TOKEN.match(text, pos)
        if not m or m.end() == pos:
            raise ValueError(f"token inválido na posição {pos}")
        pos = m.end()
        if m.group(1):
            tokens.append(("(", None))
        elif m.group(2):
            tokens.append((")", None))
        elif m.group(3) is not None or m.group(4) is not None:
            tokens.append(("tag", m.group(3) if m.group(3) is not None else m.group(4)))
        else:
            word = m.group(5)
            if word.lower() in ("and", "or", "not"):
                tokens.append((word.lower(), None))
            else:
                tokens.append(("tag", word))
    if not tokens:
        raise ValueError("filtro vazio")

    index = [0]

    def peek():
        return tokens[index[0]][0] if index[0] < len(tokens) else None

    def take(kind):
        if peek() != kind:
            raise ValueError(f"esperado {kind!r}")
        index[0] += 1
        return tokens[index[0] - 1]

    def expr():
        node = term()
        while peek() == "or":
            take("or")
            left, right = node, term()
            node = (lambda a, b: lambda tags: a(tags) or b(tags))(left, right)
        return node

    def term():
        node = factor()
        while peek() == "and":
            take("and")
            left, right = node, factor()
            node = (lambda a, b: lambda tags: a(tags) and b(tags))(left, right)
        return node

    def factor():
        kind = peek()
        if kind == "not":
            take("not")
            inner = factor()
            return lambda tags: not inner(tags)
        if kind == "(":
            take("(")
            node = expr()
            take(")")
            return node
        if kind == "tag":
            name = take("tag")[1]
            return lambda tags: name in tags
        raise ValueError(f"token inesperado {kind!r}")

    predicate = expr()
    if index[0] != len(tokens):
        raise ValueError("tokens restantes após o fim da expressão")
    return predicate, [v for k, v in tokens if k == "tag"]


def _quoted_tokens(expression: str) -> list:
    return re.findall(r"'([^']*)'", expression) + re.findall(r'"([^"]*)"', expression)


# --- templates ----------------------------------------------------------------

def _template_references(tree, inv, resolver, refs) -> None:
    device = panorama_entry(tree.getroot())
    base = f"/config/devices/entry[@name={quote(inv.metadata.panorama_entry)}]"
    for kind in ("template", "template-stack"):
        for template in device.findall(f"{kind}/entry"):
            name = template.get("name", "")
            meta = dict(
                source_kind="template", source_scope=name, source_type=kind, source_name=name,
                source_xpath=f"{base}/{kind}/entry[@name={quote(name)}]",
                source_index=-1, rule_disabled=False,
            )
            for leaf in template.iter():
                if not isinstance(leaf.tag, str) or any(isinstance(c.tag, str) for c in leaf):
                    continue
                value = (leaf.text or "").strip()
                if value in KEYWORDS:
                    continue
                for idx in resolver.by_name.get(value, ()):
                    refs.append(Reference(
                        **meta, field=leaf.tag, value=value, context=name, target=idx,
                        confidence="possible", resolution="template-match",
                    ))


# --- emissão --------------------------------------------------------------------

def _emit(refs, meta, resolver, field_path, value, types, confirmed, possible) -> None:
    if value in KEYWORDS:
        return
    confirmed_targets, own_hits = set(), None
    for context in confirmed:
        resolution, hits = resolver.resolve(value, types, context)
        if own_hits is None:
            own_hits = hits
        elif resolution == "resolved" and hits != own_hits:
            resolution = "override"
        if not hits:
            if resolution == "unresolved" and _is_literal(value, types):
                resolution = "literal"
            refs.append(Reference(**meta, field=field_path, value=value, context=context,
                                  target=-1, confidence="none", resolution=resolution,
                                  types=types))
            continue
        confidence = "confirmed" if resolution in ("resolved", "override") else "possible"
        for idx in hits:
            if confidence == "confirmed":
                confirmed_targets.add(idx)
            refs.append(Reference(**meta, field=field_path, value=value, context=context,
                                  target=idx, confidence=confidence, resolution=resolution,
                                  types=types))
    for context in possible:
        _, hits = resolver.resolve(value, types, context)
        for idx in hits:
            if idx in confirmed_targets:
                continue
            refs.append(Reference(**meta, field=field_path, value=value, context=context,
                                  target=idx, confidence="possible", resolution="override",
                                  types=types))


def _emit_fallback(refs, meta, resolver, field_path, value, contexts) -> None:
    for context in contexts:
        _, hits = resolver.resolve(value, ANY_TYPE, context)
        for idx in hits:
            if idx == meta["source_index"]:
                continue
            refs.append(Reference(**meta, field=field_path, value=value, context=context,
                                  target=idx, confidence="possible", resolution="unmapped-field"))


def _is_literal(value: str, types) -> bool:
    if types is ANY_TYPE or "address" not in types:
        return False
    candidates = value.split("-", 1) if "-" in value else [value]
    try:
        for c in candidates:
            if "/" in c:
                ipaddress.ip_network(c, strict=False)
            else:
                ipaddress.ip_address(c)
    except ValueError:
        return False
    return True
