"""Remoção de objetos não utilizados (Etapa 6): fase 4 do plano aprovado, aplicada ao
XML convertido da Etapa 5.

Cada candidato (DELETE_UNUSED no plano) é reavaliado no XML convertido e só é
removido se todas as provas abaixo valerem:

1. o objeto existe no mesmo XPath, com o mesmo valor do plano;
2. a reanálise o classifica como UNUSED (nunca USED ou UNKNOWN), com tipo reconhecido;
3. toda referência de entrada vem de outro objeto que também será removido;
4. o nome não aparece como texto em áreas não modeladas pela análise (templates,
   configuração do Panorama, mgt-config etc.).

Candidatos reprovados são mantidos, e a regra 3 é reaplicada até estabilizar.
"""

from __future__ import annotations

from dataclasses import dataclass



@dataclass
class Candidate:
    seq: int
    xpath: str
    scope: str
    object_type: str
    name: str
    planned_value: str
    index: int = -1           # índice no inventário do XML convertido
    usage: str = ""
    inbound: tuple = ()
    text_hits: tuple = ()
    delete: bool = False
    reason: str = ""


def review_candidates(converted_tree, an_converted, operations, planned_values: dict,
                      keep_types=()) -> list:
    """Reavalia cada DELETE_UNUSED do plano no XML convertido.

    keep_types: tipos de objeto que o usuário decidiu não remover (ex.: "tag").
    """
    inv = an_converted.inventory
    by_xpath = {r.xpath: i for i, r in enumerate(inv.records)}
    inbound = {}
    for ref in an_converted.graph.references:
        if ref.target >= 0:
            inbound.setdefault(ref.target, []).append(ref)

    candidates = []
    for op in operations:
        if op["operation"] != "DELETE_UNUSED":
            continue
        c = Candidate(int(op["seq"]), op["xpath"], op["scope"], op["object_type"], op["name"],
                      planned_values.get(op["xpath"], ""))
        i = by_xpath.get(c.xpath)
        if i is None:
            c.reason = "objeto não encontrado no XML convertido"
        else:
            c.index = i
            rec = inv.records[i]
            c.usage = an_converted.usage[i].status
            c.inbound = tuple(inbound.get(i, ()))
            if c.object_type in keep_types:
                c.reason = f"mantido por decisão do usuário: tipo {c.object_type} não é alterado"
            elif rec.value != c.planned_value:
                c.reason = "valor difere do plano"
            elif not rec.recognized:
                c.reason = "tipo não reconhecido"
            elif c.usage != "UNUSED":
                c.reason = f"reanálise classifica como {c.usage}"
            else:
                c.delete = True
        candidates.append(c)

    hits = _text_occurrences(converted_tree, {c.name for c in candidates if c.delete})
    for c in candidates:
        if c.delete and hits.get(c.name):
            c.text_hits = tuple(hits[c.name][:5])
            c.delete = False
            c.reason = "nome aparece como texto fora das áreas analisadas"

    # Referência de entrada só é aceita se a origem também for removida.
    changed = True
    while changed:
        changed = False
        removing = {c.index for c in candidates if c.delete}
        for c in candidates:
            if not c.delete:
                continue
            keepers = [r for r in c.inbound
                       if r.source_kind != "object" or r.source_index not in removing]
            if keepers:
                r = keepers[0]
                c.delete = False
                c.reason = (f"referenciado por {r.source_kind} {r.source_scope}:{r.source_name} "
                            f"({r.field}), que não será removido")
                changed = True
    for c in candidates:
        if c.delete:
            c.reason = ("sem referências" if not c.inbound else
                        "referenciado apenas por objetos também removidos")
    return candidates


# Caminhos (por tag) cobertos pela análise de dependências; o resto é varrido por texto.
MODELED_PATHS = frozenset({"/config/shared", "/config/devices/entry/device-group"})


def _text_occurrences(tree, names: set) -> dict:
    """Ocorrências exatas de nomes como texto fora de Shared e dos Device Groups.

    /config/readonly fica de fora: contém só IDs internos mantidos pelo Panorama.
    """
    hits = {}

    def walk(el, path):
        if path in MODELED_PATHS:
            return
        text = (el.text or "").strip()
        if text in names:
            hits.setdefault(text, []).append(path)
        for child in el:
            if isinstance(child.tag, str):
                walk(child, f"{path}/{child.tag}")

    for child in tree.getroot():
        if isinstance(child.tag, str) and child.tag != "readonly":
            walk(child, f"/config/{child.tag}")
    return hits


def deletion_operations(candidates) -> list:
    """Operações de exclusão confirmadas, na ordem do plano."""
    return [
        {"seq": c.seq, "phase": 4, "operation": "DELETE_UNUSED", "xpath": c.xpath,
         "scope": c.scope, "object_type": c.object_type, "name": c.name, "new_name": ""}
        for c in sorted(candidates, key=lambda c: c.seq) if c.delete
    ]
