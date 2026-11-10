"""Verificação independente: IPs, portas e applications de cada regra (Etapas 5, 6 e 8).

Requisito do usuário: o nome de um objeto pode mudar; o valor que ele representa nas
regras não. Este módulo compara o XML original e o XML final, regra a regra, em cada
Device Group onde a regra vale, e expande cada referência até as folhas:

- address / address-group (estático e dinâmico, recursivo) -> IP, rede, range,
  wildcard ou FQDN normalizado;
- service / service-group (recursivo) -> (protocolo, porta, porta de origem);
- application-group (recursivo) e application/application-filter customizados ->
  definição; App-IDs predefinidas ficam pelo nome.

Campos comparados, em todas as rulebases (pre e post; security, nat, pbf,
decryption, application-override, qos, authentication, dos, sdwan e outras):
source, destination, service, application, negate-source, negate-destination,
endereços traduzidos do NAT e next-hop do PBF. Também exige o mesmo conjunto de
regras, na mesma ordem, com o mesmo UUID.

É deliberadamente independente do restante do projeto (não usa inventory,
dependency_analyzer, planner nem effective_model): só lxml. Um defeito no modelo
usado para planejar não pode aprovar o próprio resultado.
"""

from __future__ import annotations

import ipaddress
import re
from collections import defaultdict
from dataclasses import dataclass, field

FAMILY = {
    "address": ("address", "address-group"),
    "service": ("service", "service-group"),
    "application": ("application", "application-group", "application-filter"),
}
ADDRESS_FIELDS = ("source", "destination")
NAT_ADDRESS_PATHS = (
    "source-translation/static-ip/translated-address",
    "source-translation/dynamic-ip/translated-address",
    "source-translation/dynamic-ip-and-port/translated-address",
    "destination-translation/translated-address",
    "dynamic-destination-translation/translated-address",
)
LITERAL_PATHS = (
    "negate-source", "negate-destination",
    "destination-translation/translated-port", "dynamic-destination-translation/translated-port",
    "action/forward/nexthop/ip-address", "action/forward/nexthop/fqdn",
    "action/forward/egress-interface",
)


@dataclass
class Difference:
    scope: str
    rulebase: str
    rule: str
    context: str
    field: str
    only_original: list
    only_final: list


@dataclass
class EquivalenceResult:
    checked: int = 0
    rules: int = 0
    differences: list = field(default_factory=list)
    structural: list = field(default_factory=list)   # regras ausentes, novas, ordem, UUID

    @property
    def ok(self) -> bool:
        return not self.differences and not self.structural


class _Config:
    def __init__(self, tree):
        root = tree.getroot()
        self.shared = root.find("shared")
        device = root.xpath("/config/devices/entry")
        self.dgs = {e.get("name"): e for e in (device[0].findall("device-group/entry") if device else [])}
        self.parent = {e.get("name"): (e.findtext("parent-dg") or None)
                       for e in root.xpath("/config/readonly/devices/entry/device-group/entry")}
        self.objects = {}
        for scope, el in [("shared", self.shared)] + list(self.dgs.items()):
            index = defaultdict(dict)
            if el is not None:
                for types in FAMILY.values():
                    for t in types:
                        for e in el.findall(f"{t}/entry"):
                            index[t][e.get("name")] = e
            self.objects[scope] = index
        self.cache = {}

    def chain(self, context):
        out, seen = [], set()
        while context and context not in seen:
            seen.add(context)
            out.append(context)
            context = self.parent.get(context)
        return out + ["shared"]

    def lookup(self, name, types, context):
        for scope in self.chain(context):
            for t in types:
                e = self.objects[scope][t].get(name)
                if e is not None:
                    return t, e
        return None, None

    # --- expansão -------------------------------------------------------------

    def addresses(self, name, context, stack=()):
        t, e = self.lookup(name, FAMILY["address"], context)
        if e is None:
            return frozenset({("literal", _norm_literal(name))})
        key = ("a", id(e), context)
        if key in self.cache:
            return self.cache[key]
        if key in stack:
            return frozenset({("cycle", name)})
        stack += (key,)
        if t == "address":
            out = frozenset({_address_value(e)})
        elif e.find("dynamic") is not None:
            expression = " ".join((e.findtext("dynamic/filter") or "").split())
            predicate = _tag_predicate(expression)
            visible = {}
            for scope in reversed(self.chain(context)):
                for n, el in self.objects[scope]["address"].items():
                    visible[n] = el
                for n in self.objects[scope]["address-group"]:
                    visible.pop(n, None)
            out = frozenset(_address_value(el) for el in visible.values()
                            if predicate({(m.text or "").strip() for m in el.findall("tag/member")}))
            out = out or frozenset({("dynamic-empty", expression)})
        else:
            out = frozenset().union(*[self.addresses((m.text or "").strip(), context, stack)
                                      for m in e.findall("static/member")] or [frozenset()])
        self.cache[key] = out
        return out

    def services(self, name, context, stack=()):
        t, e = self.lookup(name, FAMILY["service"], context)
        if e is None:
            return frozenset({("literal", name)})
        key = ("s", id(e), context)
        if key in self.cache:
            return self.cache[key]
        if key in stack:
            return frozenset({("cycle", name)})
        stack += (key,)
        if t == "service":
            out = frozenset(
                (p.tag, "".join((p.findtext("port") or "").split()),
                 "".join((p.findtext("source-port") or "").split()))
                for p in e.findall("protocol/*") if isinstance(p.tag, str))
        else:
            out = frozenset().union(*[self.services((m.text or "").strip(), context, stack)
                                      for m in e.findall("members/member")] or [frozenset()])
        self.cache[key] = out
        return out

    def applications(self, name, context, stack=()):
        t, e = self.lookup(name, FAMILY["application"], context)
        if e is None:
            return frozenset({("app", name)})
        key = ("p", id(e), context)
        if key in stack:
            return frozenset({("cycle", name)})
        stack += (key,)
        if t == "application-group":
            return frozenset().union(*[self.applications((m.text or "").strip(), context, stack)
                                       for m in e.findall("members/member")] or [frozenset()])
        return frozenset({(t, _canonical(e))})

    def rules(self):
        """(escopo, rulebase, nome) -> (elemento, contextos, posição)."""
        out = {}
        sources = [("shared", self.shared, list(self.dgs))]
        for name, el in self.dgs.items():
            sources.append((name, el, [d for d in self.dgs if name in self.chain(d)]))
        for scope, el, contexts in sources:
            if el is None:
                continue
            for base in ("pre-rulebase", "post-rulebase"):
                for rb in el.findall(f"{base}/*"):
                    for pos, rule in enumerate(rb.findall("rules/entry")):
                        out[(scope, f"{base}/{rb.tag}", rule.get("name"))] = (rule, contexts, pos)
        return out


def _members(el, path):
    node = el.find(path)
    if node is None:
        return []
    members = [(m.text or "").strip() for m in node.findall("member")]
    if not members and not len(node) and (node.text or "").strip():
        members = [(node.text or "").strip()]
    return members


def _leaves(cfg, rule, context) -> dict:
    out = {}
    for f in ADDRESS_FIELDS:
        out[f] = frozenset().union(*[cfg.addresses(n, context) for n in _members(rule, f)] or [frozenset()])
    out["service"] = frozenset().union(*[cfg.services(n, context) for n in _members(rule, "service")]
                                       or [frozenset()])
    out["application"] = frozenset().union(
        *[cfg.applications(n, context) for n in _members(rule, "application")] or [frozenset()])
    for path in NAT_ADDRESS_PATHS:
        names = _members(rule, path)
        if names:
            out[path] = frozenset().union(*[cfg.addresses(n, context) for n in names])
    for path in LITERAL_PATHS:
        node = rule.find(path)
        if node is not None:
            out[path] = frozenset({_canonical(node)})
    return out


def compare_rules(original, final) -> EquivalenceResult:
    a, b = _Config(original), _Config(final)
    ra, rb = a.rules(), b.rules()
    result = EquivalenceResult(rules=len(ra))
    for key in sorted(set(ra) - set(rb)):
        result.structural.append(("regra ausente no final", key))
    for key in sorted(set(rb) - set(ra)):
        result.structural.append(("regra nova no final", key))
    for key, (rule_a, contexts, pos_a) in sorted(ra.items()):
        if key not in rb:
            continue
        rule_b, _, pos_b = rb[key]
        if pos_a != pos_b:
            result.structural.append(("ordem alterada", key))
        if rule_a.get("uuid") != rule_b.get("uuid"):
            result.structural.append(("UUID alterado", key))
        for context in contexts:
            result.checked += 1
            la, lb = _leaves(a, rule_a, context), _leaves(b, rule_b, context)
            for f in sorted(set(la) | set(lb)):
                va, vb = la.get(f, frozenset()), lb.get(f, frozenset())
                if va != vb:
                    result.differences.append(Difference(
                        key[0], key[1], key[2], context, f,
                        sorted(map(str, va - vb)), sorted(map(str, vb - va))))
    return result


def summary(result: EquivalenceResult) -> str:
    sample = [f"{d.scope}/{d.rule} em {d.context} campo {d.field}" for d in result.differences[:3]]
    return (f"{result.rules} regras; {result.checked} verificações (regra × DG) de IP, porta e "
            f"application; diferenças: {len(result.differences)} {sample}; estruturais: "
            f"{len(result.structural)} {result.structural[:3]}")


# --- normalização -------------------------------------------------------------------

def _norm_literal(value: str):
    try:
        return str(ipaddress.ip_network(value, strict=False))
    except ValueError:
        return value


def _address_value(e):
    for kind in ("ip-netmask", "ip-range", "fqdn", "ip-wildcard"):
        text = e.findtext(kind)
        if text is not None:
            text = text.strip()
            if kind == "ip-netmask":
                try:
                    text = str(ipaddress.ip_network(text, strict=False))
                except ValueError:
                    pass
            elif kind == "fqdn":
                text = text.lower().rstrip(".")
            return (kind, text)
    return ("address-sem-valor", _canonical(e))


def _canonical(e) -> str:
    """Conteúdo do elemento sem nome, descrição, tags e espaços de formatação."""
    if not len(e):
        return f"{e.tag}={(e.text or '').strip()}"
    kids = sorted(_canonical(c) for c in e if isinstance(c.tag, str)
                  and c.tag not in ("description", "tag"))
    return f"{e.tag}({','.join(kids)})"


_TOKEN = re.compile(r"\s*(\(|\)|'[^']*'|\"[^\"]*\"|and\b|or\b|not\b|[^\s()]+)")


def _tag_predicate(expression: str):
    tokens = _TOKEN.findall(expression)
    pos = [0]

    def peek():
        return tokens[pos[0]] if pos[0] < len(tokens) else None

    def take():
        pos[0] += 1
        return tokens[pos[0] - 1]

    def p_or():
        left = p_and()
        while peek() == "or":
            take()
            right = p_and()
            left = (lambda x, y: lambda s: x(s) or y(s))(left, right)
        return left

    def p_and():
        left = p_not()
        while peek() == "and":
            take()
            right = p_not()
            left = (lambda x, y: lambda s: x(s) and y(s))(left, right)
        return left

    def p_not():
        if peek() == "not":
            take()
            inner = p_not()
            return lambda s: not inner(s)
        if peek() == "(":
            take()
            inner = p_or()
            take()
            return inner
        tag = take().strip("'\"")
        return lambda s: tag in s

    try:
        predicate = p_or()
    except IndexError:
        return lambda s: False
    return predicate
