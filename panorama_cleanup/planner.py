"""Planejamento da centralização em Shared (Etapa 4, modo PLAN; somente leitura).

O planejador propõe uma decisão por objeto e só a mantém se a simulação
(effective_model) provar que nenhum firewall muda o valor efetivo de nenhuma
referência de regra, em cada fase de execução. O que não puder ser provado é
rebaixado para KEEP_LOCAL, com o motivo registrado.

Decisões:
- KEEP: objeto de Shared permanece como está.
- MOVE: objeto de DG vai para Shared com o mesmo nome.
- RENAME_MOVE: objeto de DG vai para Shared com nome novo (naming.py: NOME-IP,
  NOME-FQDN, NOME-PROTOCOLO-PORTA ou, para grupos, NOME_DG); referências do DG atualizadas.
- CONSOLIDATE: cópia de DG removida porque outro objeto de mesmo valor passa a
  representá-la em Shared: o de mesmo nome ou, quando o nome novo coincide (duas
  cópias com o mesmo IP em DGs diferentes), o de mesmo nome novo; neste caso as
  referências do DG são atualizadas para o nome novo.
- DELETE_UNUSED: objeto sem utilização comprovada (executado só na Etapa 6).
- KEEP_LOCAL: permanece no Device Group (motivo em reason).
- PENDING_DECISION: permanece no Device Group até decisão do usuário.

Tags (requisito do usuário, D17): nenhuma tag é adicionada, removida ou trocada
em objetos ou regras. Tags nunca são renomeadas nem consolidadas com valores
diferentes; objetos movidos levam exatamente as mesmas tags.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field, replace

from panorama_cleanup.dependency_analyzer import Resolver
from panorama_cleanup.duplicate_analyzer import NAME_FAMILY
from panorama_cleanup.effective_model import (
    State, compare, compare_objects, objects_from_inventory, rule_refs_from_graph, used_contexts,
    with_changes,
)
from panorama_cleanup.errors import PanoramaCleanupError
from panorama_cleanup.naming import conflict_name, valid_name
from panorama_cleanup.xml_reader import SHARED

KEEP, MOVE, RENAME_MOVE, CONSOLIDATE = "KEEP", "MOVE", "RENAME_MOVE", "CONSOLIDATE"
DELETE_UNUSED, KEEP_LOCAL, PENDING = "DELETE_UNUSED", "KEEP_LOCAL", "PENDING_DECISION"
# Objetos que continuam no DG durante as fases 1-3 e cujos membros são atualizados na
# fase 2. DELETE_UNUSED só sai na fase 4 (que pode ser adiada): o membro precisa
# continuar resolvendo para o mesmo valor até lá.
LOCAL_UPDATE_ACTIONS = (KEEP_LOCAL, PENDING, DELETE_UNUSED)
# Objetos copiados para Shared: a atualização vai na cópia (fase 1).
COPY_UPDATE_ACTIONS = (MOVE, RENAME_MOVE)
CHANGES = (MOVE, RENAME_MOVE, CONSOLIDATE, DELETE_UNUSED)

MOVABLE_TYPES = frozenset({
    "address", "address-group", "service", "service-group", "tag", "application",
    "application-group", "application-filter", "schedule", "external-list",
    "log-settings/profiles",
})
# Security Profiles e Security Profile Groups nunca são movidos, renomeados, consolidados
# ou excluídos por não uso, em Shared ou em qualquer Device Group (CLAUDE.md §3: "Alterar
# Security Profiles" é proibido; requisito explícito do usuário, 13/11/2026). A decisão é
# sempre KEEP/KEEP_LOCAL, independente de duplicidade ou status de utilização.
NEVER_TOUCH_TYPES_PREFIX = "profiles/"
NEVER_TOUCH_TYPES = frozenset({"profile-group"})


def _never_touch(object_type: str) -> bool:
    return object_type in NEVER_TOUCH_TYPES or object_type.startswith(NEVER_TOUCH_TYPES_PREFIX)
# PAN-OS 11.1: até 63 caracteres para nomes de address e address group
# (docs/arquitetura.md, D18). Aplicado a todos os tipos renomeáveis.
NAME_LIMIT = 63
VERIFIED_NAME_LIMIT_TYPES = frozenset({"address", "address-group"})
TAG_FIELDS = frozenset({"tag", "group-tag"})

RISK = {
    KEEP: "nenhum", KEEP_LOCAL: "nenhum", PENDING: "nenhum",
    CONSOLIDATE: "baixo", MOVE: "baixo", RENAME_MOVE: "médio", DELETE_UNUSED: "médio",
}
PHASES = (
    (0, "Criar cópias locais de grupos de Shared que variam por Device Group"),
    (1, "Criar objetos em Shared"),
    (2, "Atualizar referências no Device Group"),
    (3, "Excluir cópias locais"),
    (4, "Excluir objetos não utilizados (Etapa 6)"),
)


class PlanError(PanoramaCleanupError):
    """Plano não pôde ser provado equivalente."""


@dataclass(frozen=True)
class Decision:
    key: int
    action: str
    new_scope: str
    new_name: str
    target: int
    reason: str


@dataclass
class Plan:
    decisions: dict
    rule_updates: list
    object_updates: list
    verification: list           # [(fase, contexto, referências verificadas, diferenças)]
    demotions: list = field(default_factory=list)
    iterations: int = 0
    # Regra 3: descrição herdada da cópia local quando o objeto mantido não tem descrição.
    # [{"target": chave, "source": chave, "description": texto, "xpath": xpath final}]
    descriptions: list = field(default_factory=list)
    # Fase 0 (localization.py): cópias locais de grupos de Shared, uma por (grupo, DG).
    # [{"key": chave da cópia, "object_type", "name", "dg", "source_xpath"}]
    localizations: list = field(default_factory=list)


def build_plan(an, max_iterations: int = 50, forced_rename: frozenset = frozenset()) -> Plan:
    """forced_rename: cópias locais da fase 0; vão para Shared sempre com nome novo
    (nunca consolidadas no grupo de Shared de origem, cujo valor no DG elas assumem)."""
    plan = _Planner(an, forced_rename).run(max_iterations)
    check_tag_invariant(an.inventory, plan)
    plan.descriptions = inherited_descriptions(an.inventory, plan.decisions)
    return plan


def final_xpath(inv, key, decision) -> str:
    """XPath do objeto que representa a chave ao fim das fases 1-3."""
    from panorama_cleanup.inventory import quote
    rec = inv.records[key]
    if decision.action in (MOVE, RENAME_MOVE):
        return f"/config/shared/{rec.object_type}/entry[@name={quote(decision.new_name)}]"
    return rec.xpath


def inherited_descriptions(inv, decisions) -> list:
    """Regra 3: ao consolidar cópias, a descrição do objeto mantido prevalece; se ele
    não tiver descrição e alguma cópia tiver, a da primeira cópia (ordem de DG e nome)
    é copiada para ele antes de a cópia ser excluída. Só texto: nenhum valor muda."""
    copies = defaultdict(list)
    for key, d in decisions.items():
        if d.action == CONSOLIDATE and d.target >= 0:
            copies[d.target].append(key)
    out = []
    for target, keys in sorted(copies.items()):
        td = decisions[target]
        if td.action not in (KEEP, MOVE, RENAME_MOVE):
            continue
        if (inv.records[target].description or "").strip():
            continue
        for key in sorted(keys, key=lambda k: (inv.records[k].scope, inv.records[k].name, k)):
            text = inv.records[key].description or ""
            if text.strip():
                out.append({"target": target, "source": key, "description": text,
                            "xpath": final_xpath(inv, target, td)})
                break
    return out


def check_tag_invariant(inv, plan) -> None:
    """Recusa o plano se alguma operação mexer em tags de objetos ou regras."""
    for u in plan.rule_updates:
        if u["field"] in TAG_FIELDS:
            raise PlanError(f"Plano alteraria tag da regra {u['source_xpath']}")
    for u in plan.object_updates:
        if u["field"] in TAG_FIELDS:
            raise PlanError(f"Plano alteraria tag do objeto {inv.records[u['source']].xpath}")
    for key, d in plan.decisions.items():
        if inv.records[key].object_type == "tag" and d.action in (MOVE, RENAME_MOVE, CONSOLIDATE):
            raise PlanError(f"Plano moveria ou consolidaria a tag {inv.records[key].xpath} "
                            f"({d.action}); tags não são criadas nem movidas")


class _Planner:
    def __init__(self, an, forced_rename=frozenset()):
        self.an, self.inv = an, an.inventory
        self.forced_rename = frozenset(forced_rename)
        self.objects = objects_from_inventory(self.inv, "name+color")
        self.before = State(self.objects, self.inv.device_groups)
        self.rule_refs = rule_refs_from_graph(an.graph)
        self.known_names = {r.name for r in self.inv.records}
        self.inbound = defaultdict(list)
        for ref in an.graph.references:
            if ref.target >= 0:
                self.inbound[ref.target].append(ref)
        self.demotions = []
        self.resolver = Resolver(self.inv)
        self.used = used_contexts(an.graph)

    # --- decisões iniciais ----------------------------------------------------

    def initial(self) -> dict:
        inv, usage, dup = self.inv, self.an.usage, self.an.duplicates
        decisions = {}
        groups = defaultdict(list)
        for i, rec in enumerate(inv.records):
            groups[(NAME_FAMILY.get(rec.object_type, rec.object_type), rec.name)].append(i)

        for i, rec in enumerate(inv.records):
            status = usage[i].status
            if rec.scope == SHARED:
                if _never_touch(rec.object_type):
                    decisions[i] = Decision(i, KEEP, SHARED, rec.name, -1,
                                            "Security Profile / Security Profile Group: nunca "
                                            "excluído por não uso (CLAUDE.md §3)")
                elif status == "UNUSED":
                    decisions[i] = Decision(i, DELETE_UNUSED, "", "", -1, usage[i].reason)
                else:
                    decisions[i] = Decision(i, KEEP, SHARED, rec.name, -1, "já em Shared")
                continue
            if _never_touch(rec.object_type):
                decisions[i] = Decision(i, KEEP_LOCAL, rec.scope, rec.name, -1,
                                        "Security Profile / Security Profile Group: nunca movido, "
                                        "renomeado, consolidado ou excluído (CLAUDE.md §3)")
                continue
            movable = rec.object_type in MOVABLE_TYPES
            if not rec.recognized or not movable:
                decisions[i] = Decision(i, KEEP_LOCAL, rec.scope, rec.name, -1,
                                        f"tipo {rec.object_type} fora do escopo de centralização")
            elif status == "UNKNOWN":
                decisions[i] = Decision(i, KEEP_LOCAL, rec.scope, rec.name, -1,
                                        "utilização UNKNOWN")
            elif status == "UNUSED":
                decisions[i] = Decision(i, DELETE_UNUSED, "", "", -1, usage[i].reason)
            elif rec.object_type == "tag":
                # Decisão padrão (D17): nenhuma tag é criada, movida ou excluída. Objetos
                # que dependem dela e iriam para Shared são mantidos pela verificação de
                # dependências (a cópia em Shared não resolveria a tag).
                decisions[i] = Decision(i, KEEP_LOCAL, rec.scope, rec.name, -1,
                                        "tags não são movidas para Shared (decisão padrão: "
                                        "nenhuma tag é criada, movida ou excluída)")

        for (family, name), indexes in sorted(groups.items()):
            pending = [i for i in indexes if i not in decisions]
            if not pending:
                continue
            shared = [i for i in indexes if inv.records[i].scope == SHARED]
            used_dg = sorted(pending, key=lambda i: inv.records[i].scope)
            for i in used_dg:
                decisions[i] = self._decide(i, shared, used_dg, dup)

        # Objeto de Shared que recebe cópias consolidadas passa a ser usado.
        for d in list(decisions.values()):
            if d.action == CONSOLIDATE and decisions[d.target].action == DELETE_UNUSED:
                t = inv.records[d.target]
                decisions[d.target] = Decision(d.target, KEEP, SHARED, t.name, -1,
                                               "exclusão cancelada: passa a representar cópias "
                                               "consolidadas dos Device Groups")

        # Regra 3: cópia local sem uso, idêntica ao objeto de Shared de mesmo nome que
        # continua existindo, é consolidada nele (fase 3, provada pela simulação) em vez
        # de ficar para a exclusão de não utilizados. Não ressuscita objeto de Shared sem uso.
        for i, d in list(decisions.items()):
            rec = inv.records[i]
            if d.action != DELETE_UNUSED or rec.scope == SHARED:
                continue
            shared = [k for k in groups[(NAME_FAMILY.get(rec.object_type, rec.object_type), rec.name)]
                      if inv.records[k].scope == SHARED]
            if len(shared) == 1 and decisions[shared[0]].action == KEEP and self._same(i, shared[0]):
                decisions[i] = Decision(i, CONSOLIDATE, SHARED, rec.name, shared[0],
                                        "sem uso; idêntico ao objeto de Shared de mesmo nome")
        return decisions

    def _same(self, copy: int, keeper: int) -> bool:
        """A cópia pode ser substituída pelo objeto mantido sem mudar nenhum firewall?

        Compara a expansão dos dois objetos em cada contexto onde a cópia vale
        (o DG dela e descendentes), não no escopo de definição de cada um: um
        grupo de Shared pode resolver membros de forma diferente no DG.
        """
        rc, rk = self.inv.records[copy], self.inv.records[keeper]
        if rc.object_type != rk.object_type or sorted(rc.tags) != sorted(rk.tags):
            return False
        oc, ok = self.objects[copy], self.objects[keeper]
        return all(
            self.before.expand_object(ctx, oc)[0] == self.before.expand_object(ctx, ok)[0]
            for ctx in self.before.firewall_contexts(rc.scope)
        )

    def _decide(self, i, shared, used_dg, dup) -> Decision:
        rec = self.inv.records[i]
        if i in self.forced_rename:
            return self._rename(i, "cópia da fase 0 de grupo de Shared com valor próprio no DG")
        if shared:
            if len(shared) > 1:
                return Decision(i, KEEP_LOCAL, rec.scope, rec.name, -1,
                                "nome ambíguo em Shared (mais de um tipo)")
            s = shared[0]
            if self._same(i, s):
                return Decision(i, CONSOLIDATE, SHARED, rec.name, s, "mesmo nome e valor em Shared")
            return self._rename(i, "mesmo nome em Shared com valor diferente")
        rep = used_dg[0]
        if all(self._same(j, rep) for j in used_dg):
            if i == rep:
                return Decision(i, MOVE, SHARED, rec.name, -1,
                                "nome único ou cópias idênticas nos Device Groups")
            return Decision(i, CONSOLIDATE, SHARED, rec.name, rep,
                            f"idêntico à cópia de {self.inv.records[rep].scope}")
        return self._rename(i, "mesmo nome com valores diferentes entre Device Groups")

    def _rename(self, i, why) -> Decision:
        rec = self.inv.records[i]
        if rec.object_type == "tag":
            return Decision(i, KEEP_LOCAL, rec.scope, rec.name, -1,
                            f"tag em conflito ({why}); tags não são renomeadas nem alteradas "
                            "(requisito de transparência)")
        new_name, _ = conflict_name(rec.name, rec.object_type, self.inv.elements[i], rec.scope)
        return Decision(i, RENAME_MOVE, SHARED, new_name, -1, why)

    # --- validações de nome e referências -----------------------------------

    def check_renames(self, decisions) -> dict:
        """Valida os nomes novos; cópias de mesmo valor e mesmo nome novo são consolidadas.

        - nome inválido ou acima do limite: PENDING_DECISION (nada é truncado);
        - nome novo já usado por objeto de Shared de valor idêntico: CONSOLIDATE nele;
        - nome novo já planejado para outra cópia de valor idêntico: CONSOLIDATE nela;
        - nome novo já usado por objeto de valor diferente: PENDING_DECISION.
        """
        taken = defaultdict(dict)   # família -> nome -> chave do objeto existente
        for k, r in enumerate(self.inv.records):
            taken[NAME_FAMILY.get(r.object_type, r.object_type)].setdefault(r.name, k)
        planned = defaultdict(dict)  # família -> nome novo -> chave do representante
        out = dict(decisions)
        for key in sorted(k for k, d in decisions.items() if d.action == RENAME_MOVE):
            d, rec = decisions[key], self.inv.records[key]
            family = NAME_FAMILY.get(rec.object_type, rec.object_type)
            if len(d.new_name) > NAME_LIMIT or not valid_name(d.new_name):
                out[key] = Decision(key, PENDING, rec.scope, rec.name, -1,
                                    f"nome {d.new_name!r} inválido ou acima de {NAME_LIMIT} "
                                    "caracteres; definir nome manualmente")
                continue
            blocked = self._blocking_reference(key)
            if blocked:
                out[key] = Decision(key, KEEP_LOCAL, rec.scope, rec.name, -1, blocked)
                continue
            rep = planned[family].get(d.new_name)
            if rep is not None:
                if self._same(key, rep):
                    out[key] = Decision(key, CONSOLIDATE, SHARED, d.new_name, rep,
                                        f"mesmo valor da cópia de {self.inv.records[rep].scope} "
                                        f"(nome novo {d.new_name!r})")
                else:
                    out[key] = Decision(key, PENDING, rec.scope, rec.name, -1,
                                        f"nome {d.new_name!r} já planejado para valor diferente")
                continue
            existing = taken[family].get(d.new_name)
            if existing is not None:
                er = self.inv.records[existing]
                if (er.scope == SHARED and decisions[existing].action == KEEP
                        and self._same(key, existing)):
                    out[key] = Decision(key, CONSOLIDATE, SHARED, d.new_name, existing,
                                        f"já existe em Shared {d.new_name!r} com o mesmo valor")
                else:
                    out[key] = Decision(key, PENDING, rec.scope, rec.name, -1,
                                        f"nome {d.new_name!r} já existe com outro valor ou escopo")
                continue
            planned[family][d.new_name] = key
        return out

    def _blocking_reference(self, key) -> str:
        rec = self.inv.records[key]
        allowed = set(self.before.firewall_contexts(rec.scope))
        for ref in self.inbound[key]:
            if self._shadowed_source(ref):
                continue
            if ref.confidence != "confirmed":
                return f"referência não confirmada ({ref.source_kind} {ref.source_name}); nome não pode mudar"
            if ref.source_scope not in allowed:
                return (f"usado por definição de {ref.source_scope} ({ref.source_kind} "
                        f"{ref.source_name}, campo {ref.field}); renomear mudaria o valor "
                        f"efetivo nos firewalls de {rec.scope}")
        return ""

    def _shadowed_source(self, ref) -> bool:
        """Objeto de origem irrelevante no contexto da referência.

        Sem uso no contexto (nenhuma regra o alcança ali): o valor dele nesse DG não entra
        em regra nenhuma.

        Se, no contexto, o nome do objeto de origem resolve para outro objeto (uma
        cópia local de mesmo nome), os firewalls desse contexto nunca usam a origem;
        a referência dela não depende do nome do alvo ali.
        """
        if ref.source_kind != "object" or ref.source_index is None:
            return False
        source = self.inv.records[ref.source_index]
        if source.scope != ref.context and (ref.source_index, ref.context) not in self.used:
            # Só para definições de outro escopo (Shared ou DG ancestral). Objetos do próprio
            # DG, mesmo sem uso, têm os membros atualizados para continuarem resolvendo
            # até a fase 4.
            return True
        family = NAME_FAMILY.get(source.object_type, source.object_type)
        types = {t for t, f in NAME_FAMILY.items() if f == family} or {source.object_type}
        resolution, hits = self.resolver.resolve(source.name, types, ref.context)
        return resolution == "resolved" and hits != [ref.source_index]

    def updates(self, decisions) -> tuple:
        """Atualizações de referência exigidas pelas renomeações."""
        rule_updates, object_updates = {}, {}
        for key, d in decisions.items():
            rec = self.inv.records[key]
            if d.action not in (RENAME_MOVE, CONSOLIDATE) or d.new_name == rec.name:
                continue
            for ref in self.inbound[key]:
                if self._shadowed_source(ref):
                    continue
                if ref.source_kind == "rule":
                    rule_updates[(ref.source_xpath, ref.field, ref.value)] = dict(
                        source_xpath=ref.source_xpath, source_scope=ref.source_scope,
                        source_type=ref.source_type, source_name=ref.source_name,
                        rule_disabled=ref.rule_disabled, field=ref.field,
                        old=rec.name, new=d.new_name, target=key)
                elif ref.source_kind == "object":
                    source = decisions[ref.source_index]
                    field_name = "tag" if ref.field == "tag" else ref.field
                    object_updates[(ref.source_index, field_name, rec.name)] = dict(
                        source=ref.source_index, field=field_name, old=rec.name,
                        new=d.new_name, target=key, source_action=source.action)
        return rule_updates, object_updates

    # --- simulação ------------------------------------------------------------

    def states(self, decisions, rule_updates, object_updates) -> list:
        renames = {(u["source"], u["field"], u["old"]): u["new"] for u in object_updates.values()}
        copies = []
        for key, d in sorted(decisions.items()):
            if d.action in (MOVE, RENAME_MOVE):
                obj = self.objects[key]
                members = tuple((f, renames.get((key, f, n), n), t) for f, n, t in obj.members)
                tags = tuple(renames.get((key, "tag", t), t) for t in obj.tags)
                copies.append(replace(obj, key=-(key + 1), scope=SHARED, name=d.new_name,
                                      members=members, tags=tags))
        phase1 = self.objects + copies

        local_renames = {k: v for k, v in renames.items()
                         if decisions[k[0]].action in LOCAL_UPDATE_ACTIONS}
        phase2 = with_changes(phase1, {}, set(), local_renames)

        removed = {k for k, d in decisions.items() if d.action in (MOVE, RENAME_MOVE, CONSOLIDATE)}
        phase3 = with_changes(phase2, {}, removed, {})
        unused = {k for k, d in decisions.items() if d.action == DELETE_UNUSED}
        phase4 = with_changes(phase3, {}, unused, {})

        rule_renames = {k: u["new"] for k, u in rule_updates.items()}
        groups = self.inv.device_groups
        return [
            (1, State(phase1, groups), {}),
            (2, State(phase2, groups), rule_renames),
            (3, State(phase3, groups), rule_renames),
            (4, State(phase4, groups), rule_renames),
        ]

    def run(self, max_iterations) -> Plan:
        decisions = self.check_renames(self.initial())
        for iteration in range(1, max_iterations + 1):
            rule_updates, object_updates = self.updates(decisions)
            # Uma cópia consolidada some na fase 3; se um membro dela for renomeado, não há
            # onde aplicar a troca (o objeto mantido é a definição de outro escopo, que não
            # muda). Ela passa a ir para Shared com nome próprio, com o membro trocado.
            stuck = sorted({u["source"] for u in object_updates.values()
                            if decisions[u["source"]].action == CONSOLIDATE})
            if stuck:
                for k in stuck:
                    decisions[k] = self._rename(k, "membro renomeado; a cópia não pode ser "
                                                   "consolidada no objeto de mesmo nome")
                decisions = self.check_renames(decisions)
                continue
            states = self.states(decisions, rule_updates, object_updates)
            demote = {}
            verification = []
            for phase, state, renames in states:
                diffs = compare(self.before, state, self.rule_refs, renames)
                per_context = defaultdict(int)
                for context, ref, _, _, touched in diffs:
                    per_context[context] += 1
                    keys = {abs(k) - 1 if k < 0 else k for k in touched}
                    changed = [k for k in keys if decisions[k].action in CHANGES]
                    if not changed:
                        raise PlanError(f"Diferença na fase {phase} em {context} sem objeto "
                                        f"alterado: {ref}")
                    for k in changed:
                        demote.setdefault(k, (
                            f"transparência: alteraria o valor efetivo na fase {phase} em "
                            f"{context} ({ref.source_xpath} campo {ref.field})"))
                checked = sum(len(state.firewall_contexts(r.source_scope)) for r in self.rule_refs)
                verification.append((phase, "referências de regra", checked, len(diffs)))
                present = {o.key: o for o in state.objects if o.key >= 0}
                pairs = [(o, present[o.key]) for o in self.before.objects
                         if o.leaf is None and o.key in present]
                object_diffs = compare_objects(self.before, state, pairs, used=self.used)
                for context, obj, _, _, touched in object_diffs:
                    keys = {abs(k) - 1 if k < 0 else k for k in touched}
                    changed = [k for k in keys if decisions[k].action in CHANGES]
                    if not changed:
                        raise PlanError(f"Diferença na fase {phase} em {context} sem objeto "
                                        f"alterado: grupo {obj.scope}:{obj.name}")
                    for k in changed:
                        demote.setdefault(k, (
                            f"transparência: alteraria o valor efetivo do grupo {obj.scope}:"
                            f"{obj.name} na fase {phase} em {context}"))
                verification.append((phase, "grupos remanescentes",
                                     sum(len(state.firewall_contexts(o.scope)) for o, _ in pairs),
                                     len(object_diffs)))
                for problem_obj, field_name, name in state.unresolved_object_refs(self.known_names):
                    key = abs(problem_obj.key) - 1 if problem_obj.key < 0 else problem_obj.key
                    culprits = [key] if decisions[key].action in CHANGES else []
                    culprits += [k for k, d in decisions.items() if d.action in CHANGES
                                 and self.inv.records[k].name == name]
                    if not culprits:
                        raise PlanError(f"Referência {field_name}={name} de "
                                        f"{problem_obj.scope}:{problem_obj.name} sem objeto")
                    for k in culprits:
                        demote.setdefault(k, (
                            f"dependência: {field_name} {name!r} de {problem_obj.scope}:"
                            f"{problem_obj.name} deixaria de existir no escopo (fase {phase})"))
            if not demote:
                return Plan(decisions, list(rule_updates.values()),
                            list(object_updates.values()), verification, self.demotions, iteration)
            decisions = self._demote(decisions, demote)
        raise PlanError(f"Plano não convergiu em {max_iterations} iterações")

    def _demote(self, decisions, demote) -> dict:
        out = dict(decisions)
        queue = list(demote.items())
        while queue:
            key, reason = queue.pop()
            d, rec = out[key], self.inv.records[key]
            if d.action not in CHANGES:
                continue
            self.demotions.append((key, d.action, reason))
            if rec.scope == SHARED:
                out[key] = Decision(key, KEEP, SHARED, rec.name, -1, f"exclusão rebaixada: {reason}")
            else:
                out[key] = Decision(key, KEEP_LOCAL, rec.scope, rec.name, -1, reason)
            for other, od in out.items():
                if od.target == key and od.action == CONSOLIDATE:
                    queue.append((other, f"depende de {rec.scope}:{rec.name}, que foi mantido local"))
        return out


# --- sequência de execução ------------------------------------------------------

TYPE_ORDER = ("tag", "address", "service", "application", "application-filter", "schedule",
              "external-list", "log-settings/profiles", "address-group", "service-group",
              "application-group")


def operation_sequence(inv, plan) -> list:
    """Operações em ordem executável: dependências antes dos dependentes."""
    decisions = plan.decisions
    level = _group_levels(inv)
    rank = {t: n for n, t in enumerate(TYPE_ORDER)}

    def order(key):
        rec = inv.records[key]
        return (rank.get(rec.object_type, len(rank)), level.get(key, 0), rec.scope, rec.name)

    ops = []
    for item in sorted(plan.localizations, key=lambda x: (x["object_type"], x["name"], x["dg"])):
        ops.append((0, "CREATE_LOCAL_COPY", item["key"], item))
    for key in sorted((k for k, d in decisions.items() if d.action in (MOVE, RENAME_MOVE)), key=order):
        ops.append((1, "CREATE_SHARED", key, decisions[key]))
    for item in plan.descriptions:
        ops.append((1, "SET_DESCRIPTION", item["target"], item))
    for u in sorted(plan.rule_updates, key=lambda u: (u["source_xpath"], u["field"], u["old"])):
        ops.append((2, "UPDATE_RULE_REFERENCE", u["target"], u))
    for u in sorted(plan.object_updates, key=lambda u: (u["source"], u["field"], u["old"])):
        action = decisions[u["source"]].action
        if action in LOCAL_UPDATE_ACTIONS:
            ops.append((2, "UPDATE_OBJECT_REFERENCE", u["target"], u))
        elif action not in COPY_UPDATE_ACTIONS:
            src = inv.records[u["source"]]
            raise PlanError(f"Plano inconsistente: atualização de membro {u['old']!r} -> "
                            f"{u['new']!r} em {src.scope}:{src.name}, cuja decisão é {action}; "
                            "não há operação que a aplique")
    deletions = [k for k, d in decisions.items() if d.action in (MOVE, RENAME_MOVE, CONSOLIDATE)]
    for key in sorted(deletions, key=order, reverse=True):
        ops.append((3, "DELETE_LOCAL_COPY", key, decisions[key]))
    unused = [k for k, d in decisions.items() if d.action == DELETE_UNUSED]
    for key in sorted(unused, key=order, reverse=True):
        ops.append((4, "DELETE_UNUSED", key, decisions[key]))
    return ops


def _group_levels(inv) -> dict:
    """Profundidade de aninhamento: grupo que contém grupo vem depois."""
    by_scope_name = {(r.scope, r.name): i for i, r in enumerate(inv.records)}
    memo = {}

    def depth(i, seen=()):
        if i in memo:
            return memo[i]
        rec = inv.records[i]
        if i in seen or not rec.members:
            return 0
        children = [by_scope_name.get((rec.scope, m), by_scope_name.get((SHARED, m)))
                    for m in rec.members]
        memo[i] = 1 + max([depth(c, seen + (i,)) for c in children if c is not None] or [0])
        return memo[i]

    return {i: depth(i) for i in range(len(inv.records))}
