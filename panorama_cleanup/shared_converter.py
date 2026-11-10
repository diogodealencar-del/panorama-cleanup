"""Conversão para Shared (Etapa 5, modo APPLY): aplica as fases 1 a 3 do plano aprovado
em uma cópia do XML. O original nunca é alterado.

Fase 1: cria em Shared as cópias dos objetos MOVE/RENAME_MOVE (membros renomeados
        conforme reference-updates.csv, "cópia criada em Shared").
Fase 2: atualiza referências em regras e objetos locais do Device Group.
Fase 3: remove as cópias locais (originais movidos e cópias consolidadas).
Fase 4: exclusão de não utilizados, aplicada só na Etapa 6 (cleanup.py) sobre o
        XML convertido.

Cada operação verifica sua pré-condição e falha (ApplyError) se o XML não estiver
exatamente como o plano espera.
"""

from __future__ import annotations

import copy
import re
from dataclasses import dataclass, field

from lxml import etree

from panorama_cleanup.errors import PanoramaCleanupError

APPLY_PHASES = (0, 1, 2, 3)


class ApplyError(PanoramaCleanupError):
    """Operação do plano não aplicável ao XML."""


@dataclass
class AppliedChange:
    seq: int
    phase: int
    operation: str
    xpath: str
    detail: str
    before: str = ""
    after: str = ""


@dataclass
class ApplyResult:
    tree: etree._ElementTree
    changes: list = field(default_factory=list)
    created_xpaths: list = field(default_factory=list)   # (xpath em Shared, xpath de origem)
    deleted_xpaths: list = field(default_factory=list)
    rule_renames: dict = field(default_factory=dict)      # (xpath regra, campo, antigo) -> novo
    object_renames: list = field(default_factory=list)    # (xpath objeto, campo, antigo, novo)
    copy_member_renames: dict = field(default_factory=dict)  # xpath origem -> [(campo, antigo, novo)]
    descriptions: list = field(default_factory=list)      # (xpath, descrição copiada)
    local_copies: list = field(default_factory=list)      # fase 0: xpaths das cópias locais


def apply_plan(tree, operations: list, copy_member_updates: list, phases=APPLY_PHASES) -> ApplyResult:
    """Aplica as operações (linhas de plan-operations) nas fases pedidas, em ordem."""
    work = copy.deepcopy(tree)
    result = ApplyResult(work)
    renames_for_copy = {}
    for u in copy_member_updates:
        renames_for_copy.setdefault(u["source_xpath"], []).append((u["field"], u["old"], u["new"]))

    for op in sorted(operations, key=lambda o: int(o["seq"])):
        phase = int(op["phase"])
        if phase not in phases:
            continue
        kind = op["operation"]
        if kind == "CREATE_LOCAL_COPY":
            from panorama_cleanup.localization import create_local_copy
            create_local_copy(work, op["object_type"], op["name"], op["scope"])
            result.changes.append(AppliedChange(int(op["seq"]), 0, kind, op["xpath"],
                                                f"cópia local de {op['source']}",
                                                before=op["source"], after=op["xpath"]))
            result.local_copies.append(op["xpath"])
        elif kind == "CREATE_SHARED":
            _create_shared(work, op, renames_for_copy.get(op["xpath"], []), result)
        elif kind == "SET_DESCRIPTION":
            _set_description(work, op, result)
        elif kind == "UPDATE_RULE_REFERENCE":
            _update_reference(work, op, result, is_rule=True)
        elif kind == "UPDATE_OBJECT_REFERENCE":
            _update_reference(work, op, result, is_rule=False)
        elif kind in ("DELETE_LOCAL_COPY", "DELETE_UNUSED"):
            _delete(work, op, result)
        else:
            raise ApplyError(f"Operação desconhecida na fase {phase}: {kind}")
    return result


def _one(tree, xpath: str, what: str):
    found = tree.xpath(xpath)
    if len(found) != 1:
        raise ApplyError(f"{what}: esperado 1 elemento em {xpath}, encontrados {len(found)}")
    return found[0]


def _create_shared(tree, op, member_renames, result) -> None:
    source = _one(tree, op["xpath"], "CREATE_SHARED origem")
    if source.get("name") != op["name"]:
        raise ApplyError(f"CREATE_SHARED: nome {source.get('name')!r} difere do plano {op['name']!r}")
    shared = _one(tree, "/config/shared", "Shared")
    container = shared
    for step in op["object_type"].split("/"):
        child = container.find(step)
        if child is None:
            child = etree.SubElement(container, step)
        container = child
    new_name = op["new_name"]
    if container.xpath("entry[@name=$n]", n=new_name):
        raise ApplyError(f"CREATE_SHARED: {new_name!r} já existe em shared/{op['object_type']}")

    entry = copy.deepcopy(source)
    entry.set("name", new_name)
    for field_path, old, new in member_renames:
        if not replace_reference(entry, field_path, old, new):
            raise ApplyError(f"CREATE_SHARED: membro {old!r} não encontrado em {field_path} da cópia")
    if member_renames:
        result.copy_member_renames[op["xpath"]] = list(member_renames)
    _reindent(entry, _indent_of(source), _child_indent(container))
    _append_entry(container, entry)

    xpath = f"/config/shared/{op['object_type']}/entry[@name={_quote(new_name)}]"
    result.created_xpaths.append((xpath, op["xpath"]))
    detail = f"criado em Shared como {new_name!r}"
    if member_renames:
        detail += "; membros " + ", ".join(f"{o}->{n}" for _, o, n in member_renames)
    result.changes.append(AppliedChange(int(op["seq"]), 1, "CREATE_SHARED", xpath, detail,
                                        before=op["xpath"], after=xpath))


def _set_description(tree, op, result) -> None:
    """Regra 3: copia a descrição da cópia local para o objeto mantido sem descrição."""
    entry = _one(tree, op["xpath"], "SET_DESCRIPTION")
    current = entry.find("description")
    if current is not None and (current.text or "").strip():
        raise ApplyError(f"SET_DESCRIPTION: {op['xpath']} já tem descrição; o plano não a substitui")
    if current is None:
        # Acrescentado como último filho, como faz o set da XML API (merge): o XML gerado
        # e o resultado dos comandos ficam idênticos (generate_cli.same_config).
        current = etree.Element("description")
        children = [c for c in entry if isinstance(c.tag, str)]
        if children:
            last = children[-1]
            current.tail = last.tail
            last.tail = children[-2].tail if len(children) > 1 else entry.text
            last.addnext(current)
        else:
            entry.append(current)
    current.text = op["value"]
    result.descriptions.append((op["xpath"], op["value"]))
    result.changes.append(AppliedChange(int(op["seq"]), 1, "SET_DESCRIPTION", op["xpath"],
                                        f"descrição copiada de {op['source']}", before="",
                                        after=op["value"]))


def _update_reference(tree, op, result, is_rule: bool) -> None:
    owner = _one(tree, op["xpath"], op["operation"])
    field_path, old, new = op["field"], op["name"], op["new_name"]
    count = replace_reference(owner, field_path, old, new)
    if not count:
        raise ApplyError(f"{op['operation']}: valor {old!r} não encontrado em {field_path} de {op['xpath']}")
    if is_rule:
        result.rule_renames[(op["xpath"], field_path, old)] = new
    else:
        result.object_renames.append((op["xpath"], field_path, old, new))
    result.changes.append(AppliedChange(int(op["seq"]), 2, op["operation"], op["xpath"],
                                        f"{field_path}: {old!r} -> {new!r} ({count}x)",
                                        before=old, after=new))


def replace_reference(owner, field_path: str, old: str, new: str) -> int:
    """Troca o valor referenciado (membro ou folha) no campo; devolve quantas trocas."""
    target = owner.find(field_path)
    if target is None:
        return 0
    candidates = [m for m in target.findall("member") if (m.text or "").strip() == old]
    if not candidates and not len(target) and (target.text or "").strip() == old:
        candidates = [target]
    for el in candidates:
        el.text = el.text.replace(old, new)
    return len(candidates)


def _delete(tree, op, result) -> None:
    entry = _one(tree, op["xpath"], "DELETE_LOCAL_COPY")
    if entry.get("name") != op["name"]:
        raise ApplyError(f"DELETE_LOCAL_COPY: nome {entry.get('name')!r} difere do plano")
    parent = entry.getparent()
    previous = entry.getprevious()
    if previous is not None:
        previous.tail = entry.tail
    else:
        parent.text = entry.tail
    parent.remove(entry)
    result.deleted_xpaths.append(op["xpath"])
    if op["operation"] == "DELETE_UNUSED":
        detail = f"objeto não utilizado removido de {op['scope']}"
    else:
        detail = f"removido do {op['scope']}; DG passa a usar shared:{op['new_name']}"
    result.changes.append(AppliedChange(int(op["seq"]), int(op["phase"]), op["operation"],
                                        op["xpath"], detail))


# --- formatação ----------------------------------------------------------------

def _indent_of(el) -> str:
    previous = el.getprevious()
    text = previous.tail if previous is not None else el.getparent().text
    return (text or "").rsplit("\n", 1)[-1]


def _child_indent(container) -> str:
    children = [c for c in container if isinstance(c.tag, str)]
    if children:
        return _indent_of(children[-1])
    parent_indent = (_indent_of(container) if container.getparent() is not None else "")
    return parent_indent + "  "


def _reindent(entry, old: str, new: str) -> None:
    if old == new:
        return
    for el in entry.iter():
        for attr in ("text", "tail"):
            value = getattr(el, attr)
            if value and not value.strip() and "\n" in value:
                setattr(el, attr, value.replace("\n" + old, "\n" + new))
    entry.tail = None


def _append_entry(container, entry) -> None:
    children = [c for c in container if isinstance(c.tag, str)]
    indent = _child_indent(container)
    if children:
        last = children[-1]
        entry.tail = last.tail
        last.tail = "\n" + indent
    else:
        closing = _indent_of(container) if container.getparent() is not None else ""
        container.text = "\n" + indent
        entry.tail = "\n" + closing
    container.append(entry)


def _quote(value: str) -> str:
    from panorama_cleanup.inventory import quote
    return quote(value)


# --- serialização ----------------------------------------------------------------

def serialize(tree, original_bytes: bytes) -> bytes:
    """Mesma declaração e codificação do original (ASCII com referências &#xHH;)."""
    header = original_bytes[: original_bytes.index(b"?>") + 2] if original_bytes.startswith(b"<?xml") else b""
    body = etree.tostring(tree.getroot(), encoding="ascii", xml_declaration=False)
    body = re.sub(rb"&#(\d+);", lambda m: b"&#x%X;" % int(m.group(1)), body)
    out = (header + b"\n" if header else b"") + body
    return out + b"\n" if original_bytes.endswith(b"\n") else out
