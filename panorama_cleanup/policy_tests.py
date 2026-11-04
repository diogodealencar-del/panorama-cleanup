"""Roteiro de testes de política para homologação (Etapa 9).

Gera comandos "test security-policy-match" e "test nat-policy-match" (CLI do
firewall, modo operacional) para as regras afetadas pela limpeza. O método não
depende de prever o resultado: os mesmos comandos são executados antes da mudança
(linha de base) e depois de cada push, e a saída precisa ser idêntica.

Cada linha gerada é conferida contra a hierarquia operacional da versão no guia
oficial (check_ops_lines), aceitando qualquer subconjunto dos parâmetros documentados,
na ordem documentada.
"""

from __future__ import annotations

import ipaddress
import re
import shlex
from collections import defaultdict
from dataclasses import dataclass

from panorama_cleanup.effective_model import State, objects_from_inventory
from panorama_cleanup.xml_reader import SHARED

PORT = re.compile(r"protocol/(tcp|udp)/port=(\d+)(?:$| \|)")
PROTOCOL = {"tcp": "6", "udp": "17"}
TEST_VERBS = ("test security-policy-match", "test nat-policy-match")


@dataclass(frozen=True)
class PolicyTest:
    device_group: str
    rulebase: str
    rule: str
    reason: str
    command: str


def build_tests(an, plan_decisions: dict, rule_renames: dict, per_dg_limit: int = 40) -> list:
    """Testes para regras com referência renomeada e, até o limite, regras que usam objetos
    consolidados ou movidos."""
    inv = an.inventory
    state = State(objects_from_inventory(inv), inv.device_groups)
    changed_objects = {r.xpath for r in inv.records
                       if plan_decisions.get(r.xpath, {}).get("action") in
                       ("CONSOLIDATE", "MOVE", "RENAME_MOVE")}
    renamed_rules = {x for x, _, _ in rule_renames}
    by_rule = defaultdict(set)
    for ref in an.graph.references:
        if ref.source_kind == "rule" and ref.target >= 0 and inv.records[ref.target].xpath in changed_objects:
            by_rule[ref.source_xpath].add(ref.source_scope)

    from panorama_cleanup.policy_comparator import rulebases
    tests, extra = [], defaultdict(int)
    for (scope, rulebase), rules in rulebases(_tree_of(an)).items():
        kind = rulebase.split("/")[1]
        if kind not in ("security", "nat"):
            continue
        for xpath, rule in rules:
            if xpath in renamed_rules:
                reason = "referência renomeada"
            elif xpath in by_rule:
                if extra[scope] >= per_dg_limit:
                    continue
                extra[scope] += 1
                reason = "usa objeto consolidado ou movido"
            else:
                continue
            for context in state.firewall_contexts(scope):
                command = _command(kind, rule, state, inv, context)
                if command:
                    tests.append(PolicyTest(context, rulebase, rule.get("name", ""), reason, command))
    return tests


def _tree_of(an):
    return an.inventory.elements[0].getroottree()


def _members(rule, field):
    el = rule.find(field)
    if el is None:
        return []
    found = [(m.text or "").strip() for m in el.findall("member")]
    return found or ([(el.text or "").strip()] if (el.text or "").strip() else [])


def _ip(state, context, names) -> str:
    for name in names:
        if name in ("any", ""):
            continue
        try:
            ipaddress.ip_network(name, strict=False)
            return str(ipaddress.ip_network(name, strict=False).network_address)
        except ValueError:
            pass
        values, _ = state.expand(context, name, ("address", "address-group"))
        for v in sorted(values, key=str):
            if len(v) == 3 and v[0] == "address":
                kind, value = v[1], v[2]
                try:
                    if kind == "ip-netmask":
                        net = ipaddress.ip_network(value, strict=False)
                        host = net.network_address if net.num_addresses <= 2 else net.network_address + 1
                        return str(host)
                    if kind == "ip-range":
                        return str(ipaddress.ip_address(value.split("-")[0]))
                except ValueError:
                    continue
    return ""


def _port(state, inv, context, names) -> tuple:
    for name in names:
        if name in ("any", "application-default", ""):
            continue
        for obj in state.resolve(context, name, ("service",)):
            m = PORT.search(inv.records[obj.key].value)
            if m:
                return PROTOCOL[m.group(1)], m.group(2)
        for obj in state.resolve(context, name, ("service-group",)):
            for _, member, _ in obj.members:
                found = _port(state, inv, context, [member])
                if found:
                    return found
    return ()


def _command(kind, rule, state, inv, context) -> str:
    args = []
    zones_from, zones_to = _members(rule, "from"), _members(rule, "to")
    source = _ip(state, context, _members(rule, "source"))
    destination = _ip(state, context, _members(rule, "destination"))
    if not destination:
        return ""
    service = _port(state, inv, context, _members(rule, "service"))
    if kind == "security":
        if zones_from and zones_from[0] != "any":
            args += ["from", zones_from[0]]
        if zones_to and zones_to[0] != "any":
            args += ["to", zones_to[0]]
        if source:
            args += ["source", source]
        args += ["destination", destination]
        if service:
            args += ["destination-port", service[1], "protocol", service[0]]
        apps = [a for a in _members(rule, "application") if a != "any"]
        if apps and not state.resolve(context, apps[0], None):
            args += ["application", apps[0]]
        verb = "test security-policy-match"
    else:
        args += ["from", zones_from[0] if zones_from else "any",
                 "to", zones_to[0] if zones_to else "any"]
        if source:
            args += ["source", source]
        args += ["destination", destination]
        if service:
            args += ["destination-port", service[1], "protocol", service[0]]
        verb = "test nat-policy-match"
    return verb + " " + " ".join(shlex.quote(a) if " " in a else a for a in args)


# --- conferência contra a hierarquia operacional -------------------------------------

def load_ops_patterns(path, version: str) -> dict:
    from panorama_cleanup.cli_reference import hierarchy_sections

    sections = hierarchy_sections(path)
    if (version, "ops") not in sections:
        return {}
    start, end = sections[(version, "ops")]
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()[start:end]
    out, current = {}, None
    for text in lines + [""]:
        text = text.strip()
        starts = next((v for v in TEST_VERBS if text.startswith(v + " ")), None)
        if starts or not text or text.startswith(("test ", "show ", "set ", "PAN-OS", "CLI ")):
            if current:
                verb = next(v for v in TEST_VERBS if current.startswith(v))
                out.setdefault(verb, []).append(_ops_pairs(current[len(verb):]))
            current = text if starts else None
            continue
        if current:
            current = current + (text if current.endswith("-") or current.endswith("/") else " " + text)
    return out


def _ops_pairs(rest: str) -> list:
    words = rest.split()
    pairs, i = [], 0
    while i < len(words):
        if not words[i].startswith("<"):
            has_value = i + 1 < len(words) and words[i + 1].startswith("<")
            pairs.append(words[i])
            i += 2 if has_value else 1
        else:
            i += 1
    return pairs


def check_ops_lines(lines, patterns) -> list:
    """[(linha, ok)]: palavras-chave existem no padrão documentado e na mesma ordem."""
    results = []
    for line in lines:
        verb = next((v for v in TEST_VERBS if line.startswith(v + " ")), None)
        if verb is None:
            results.append((line, False))
            continue
        words = shlex.split(line[len(verb):])
        keys = words[0::2]
        ok = False
        for pattern in patterns.get(verb, ()):
            position, ok = -1, True
            for k in keys:
                if k not in pattern[position + 1:]:
                    ok = False
                    break
                position = pattern.index(k, position + 1)
            if ok:
                break
        results.append((line, ok))
    return results
