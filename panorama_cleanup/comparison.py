"""Read-only comparison of arbitrary Panorama XML backups and optional Traffic CSV."""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from hashlib import file_digest
import json
import os
from pathlib import Path
import tempfile

from lxml import etree
from openpyxl import Workbook
from openpyxl.cell import WriteOnlyCell
from openpyxl.styles import Font, PatternFill
from openpyxl.cell.cell import ILLEGAL_CHARACTERS_RE
from openpyxl.utils import get_column_letter

from panorama_cleanup.inventory import build_inventory, quote
from panorama_cleanup.policy_comparator import canonical, rulebases
from panorama_cleanup.traffic_report import rule_uuid, traffic
from panorama_cleanup.xml_reader import parse_backup


def checksum(path):
    with path.open("rb") as source:
        return file_digest(source, "sha256").hexdigest()


def markup(node):
    return "" if node is None else etree.tostring(node, encoding="unicode", with_tail=False)


def signature(node):
    return None if node is None else canonical(node)


def nodes_by_identity(children):
    seen, result = Counter(), {}
    for node in children:
        if isinstance(node, etree._Comment):
            key = ("comment()", None)
        elif isinstance(node, etree._ProcessingInstruction):
            key = ("processing-instruction()", None)
        else:
            key = (node.tag, node.get("name"))
        seen[key] += 1
        result[(*key, seen[key])] = node
    return result


def xml_changes(initial, final):
    def visit(a, b, path, preserve=False):
        if a is None or b is None:
            yield ("Adicionado" if a is None else "Removido", path, markup(a), markup(b),
                   a.sourceline if a is not None else None, b.sourceline if b is not None else None)
            node = b if a is None else a
            if node.tail and (preserve or node.tail.strip()):
                yield ("Texto após nó", path, a.tail if a is not None else "", b.tail if b is not None else "",
                       a.sourceline if a is not None else None, b.sourceline if b is not None else None)
            return

        def change(kind, loc, before, after):
            return kind, loc, before, after, a.sourceline, b.sourceline

        for key in sorted(set(a.attrib) | set(b.attrib)):
            if a.get(key) != b.get(key):
                yield change("Atributo", path + "/@" + key, a.get(key), b.get(key))
        space = "{http://www.w3.org/XML/1998/namespace}space"
        preserve_text = preserve or a.get(space) == "preserve" or b.get(space) == "preserve"

        def text(value, preserve_space):
            return value if value and (preserve_space or value.strip()) else ""

        if text(a.text, preserve_text) != text(b.text, preserve_text):
            yield change("Texto", path, a.text, b.text)
        if text(a.tail, preserve) != text(b.tail, preserve):
            yield change("Texto após nó", path, a.tail, b.tail)
        if isinstance(a, etree._ProcessingInstruction) and a.target != b.target:
            yield change("Alvo da instrução", path, a.target, b.target)
        left, right = nodes_by_identity(a), nodes_by_identity(b)

        def part(key):
            tag, name, occurrence = key
            base = tag if name is None else f"{tag}[@name={quote(name)}]"
            return f"{base}[{occurrence}]" if (tag, name, 2) in left or (tag, name, 2) in right else base

        before = [part(k) for k in left if k in right]
        after = [part(k) for k in right if k in left]
        if before != after:
            yield change("Ordem relativa", path, "\n".join(before), "\n".join(after))

        children = list(a) + list(b)
        if children and all(c.tag == "member" and not c.attrib and not len(c) and not text(c.tail, preserve_text) for c in children):
            values_a, values_b = ([c.text or "" for c in node] for node in (a, b))
            if values_a != values_b:
                yield change("Lista de membros", path, json.dumps(values_a, ensure_ascii=False), json.dumps(values_b, ensure_ascii=False))
            return
        for key in dict.fromkeys((*left, *right)):
            yield from visit(left.get(key), right.get(key), path + "/" + part(key), preserve_text)

    yield from visit(initial.getroot(), final.getroot(), "/config")
    for label, method in (("Prólogo", "getprevious"), ("Epílogo", "getnext")):
        def outside(tree):
            result = []
            node = getattr(tree.getroot(), method)()
            while node is not None:
                result.append(markup(node))
                node = getattr(node, method)()
            return result[::-1] if method == "getprevious" else result
        a, b = outside(initial), outside(final)
        if a != b:
            yield ("Nós externos à raiz", label, "\n".join(a), "\n".join(b), None, None)


@dataclass(frozen=True)
class Rule:
    scope: str
    base: str
    xpath: str
    position: int
    element: etree._Element

    @property
    def name(self):
        return self.element.get("name", "")


def rules(tree):
    return [Rule(scope, base, xpath, i, el) for (scope, base), rows in rulebases(tree).items()
            for i, (xpath, el) in enumerate(rows, 1)]


def rule_pairs(initial, final):
    a, b = rules(initial), rules(final)
    # Pair unique UUIDs first, including renamed/moved rules. Unpaired rules use
    # a unique XPath so replacing the UUID at a stable path remains visible.
    def unique(rows, key):
        counts = Counter(map(key, rows))
        return {key(r): r for r in rows if key(r) and counts[key(r)] == 1}

    uid = lambda r: rule_uuid(r.element.get("uuid", ""))
    by_id_a, by_id_b = unique(a, uid), unique(b, uid)
    matched = {id(r): by_id_b[key] for key, r in by_id_a.items() if key in by_id_b}
    matched_b = {id(r) for r in matched.values()}
    xpath = lambda r: r.xpath
    by_path_a = unique([r for r in a if id(r) not in matched], xpath)
    by_path_b = unique([r for r in b if id(r) not in matched_b], xpath)
    for path, rule in by_path_a.items():
        if path in by_path_b:
            matched[id(rule)] = by_path_b[path]
            matched_b.add(id(by_path_b[path]))

    old_order, new_order = {}, {}
    for old in a:
        new = matched.get(id(old))
        if new is not None and (old.scope, old.base) == (new.scope, new.base):
            key = old.scope, old.base
            old_order.setdefault(key, []).append(old)
            new_order.setdefault(key, []).append(old)
    moved = set()
    for key, old in old_order.items():
        new = sorted(new_order[key], key=lambda r: matched[id(r)].position)
        moved.update(id(r) for r, other in zip(old, new) if r is not other)

    result = []
    for old in a:
        new = matched.get(id(old))
        status = []
        if new is None:
            status.append("Removida")
        else:
            if (old.scope, old.base) != (new.scope, new.base):
                status.append("Movida")
            if id(old) in moved:
                status.append("Reordenada")
            if signature(old.element) != signature(new.element):
                status.append("Alterada")
        result.append((old, new, ", ".join(status) or "Igual"))
    result.extend((None, new, "Adicionada") for new in b if id(new) not in matched_b)
    return result


def rule_fields(pairs):
    for i, (old, new, status) in enumerate(pairs, 1):
        if status == "Igual":
            continue
        if old is None or new is None:
            yield i, "Regra inteira", markup(old.element) if old else "", markup(new.element) if new else ""
            continue
        a, b = old.element, new.element
        for key in sorted(set(a.attrib) | set(b.attrib)):
            if a.get(key) != b.get(key):
                yield i, "@" + key, a.get(key), b.get(key)
        if (old.scope, old.base) != (new.scope, new.base):
            yield i, "Escopo / rulebase", f"{old.scope} / {old.base}", f"{new.scope} / {new.base}"
        if "Reordenada" in status:
            yield i, "Posição", old.position, new.position
        for field in dict.fromkeys(c.tag for c in (*a, *b) if isinstance(c.tag, str)):
            x, y = a.findall(field), b.findall(field)
            if list(map(signature, x)) != list(map(signature, y)):
                yield i, field, "\n".join(map(markup, x)), "\n".join(map(markup, y))


def object_changes(initial, final):
    def inventory(tree):
        inv = build_inventory(tree)
        return {r.xpath: (r, e) for r, e in zip(inv.records, inv.elements)}
    a, b = inventory(initial), inventory(final)
    for path in sorted(a.keys() | b.keys()):
        rec_a, old = a.get(path, (None, None))
        rec_b, new = b.get(path, (None, None))
        if signature(old) == signature(new):
            continue
        record = rec_b or rec_a
        action = "Adicionado" if old is None else "Removido" if new is None else "Alterado"
        yield record.scope, record.object_type, record.name, action, path, markup(old), markup(new)


class Report:
    def __init__(self):
        self.book = Workbook(write_only=True)
        self.long_text = []

    def add(self, title, headers, rows):
        # Excel has a fixed row limit. Continue in another tab instead of truncating.
        page = 1
        ws, row_no = None, 0

        def append(values, heading=False):
            cells = []
            for index, value in enumerate(values, 1):
                if isinstance(value, str):
                    value = ILLEGAL_CHARACTERS_RE.sub(lambda m: f"\\u{ord(m[0]):04x}", value)
                    if len(value) > 15000:
                        ref = f"{ws.title}!{get_column_letter(index)}{row_no}"
                        self.long_text.extend((ref, n // 15000 + 1, value[n:n + 15000])
                                              for n in range(0, len(value), 15000))
                        value = f"Consulte Texto longo, referência {ref}"
                cell = WriteOnlyCell(ws, value=value)
                if isinstance(value, str):
                    cell.data_type = "s"  # Treat values from XML/CSV as data, including leading '='.
                if heading:
                    cell.fill = PatternFill("solid", fgColor="243F58")
                    cell.font = Font(color="FFFFFF", bold=True)
                cells.append(cell)
            ws.append(cells)

        def start_tab():
            nonlocal ws, row_no
            ws = self.book.create_sheet(title if page == 1 else f"{title} {page}")
            ws.freeze_panes = "A2"
            for index, name in enumerate(headers, 1):
                ws.column_dimensions[get_column_letter(index)].width = min(66, max(20, len(name) + 5))
            row_no = 1
            append(headers, heading=True)

        start_tab()
        for row in rows:
            if row_no == 1048576:
                ws.auto_filter.ref = f"A1:{get_column_letter(len(headers))}{row_no}"
                page += 1
                start_tab()
            row_no += 1
            append(row)
        ws.auto_filter.ref = f"A1:{get_column_letter(len(headers))}{row_no}"


def compare_report(initial: Path, final: Path, output: Path, traffic_csv: Path | None = None):
    sources = [initial.resolve(), final.resolve()] + ([traffic_csv.resolve()] if traffic_csv else [])
    initial, final = sources[:2]
    traffic_csv = sources[2] if len(sources) > 2 else None
    output = output.resolve()
    if output.suffix.casefold() != ".xlsx" or output in sources:
        raise ValueError("Escolha um arquivo .xlsx de destino separado dos arquivos de entrada.")
    if output.exists() and any(output.samefile(source) for source in sources):
        raise ValueError("O destino aponta para um arquivo de entrada.")
    hashes = [checksum(path) for path in sources]
    original, modified = parse_backup(initial), parse_backup(final)
    differences = list(xml_changes(original, modified))
    objects = list(object_changes(original, modified))
    pairs = rule_pairs(original, modified)
    fields = list(rule_fields(pairs))
    changed = {i for i, (_, _, status) in enumerate(pairs, 1) if status != "Igual"}
    report = Report()
    summary = [
        ("XML inicial", initial.name), ("SHA-256 inicial", hashes[0]),
        ("XML final", final.name), ("SHA-256 final", hashes[1]),
        ("Diferenças de XML", len(differences)), ("Regras com mudanças", len(changed)),
        ("Linhas de campos de regras", len(fields)),
        ("Objetos adicionados", sum(r[3] == "Adicionado" for r in objects)),
        ("Objetos removidos", sum(r[3] == "Removido" for r in objects)),
        ("Objetos alterados", sum(r[3] == "Alterado" for r in objects)),
        ("Critério XML", "Compara nós, conteúdo, atributos e ordem. Ignora indentação de formatação. Subárvores novas ou removidas aparecem completas."),
        ("Critério de regras", "UUID único ou XPath único. As posições relativas consideram as regras presentes nos dois XMLs."),
        ("Escopo da aba Regras", "Políticas pre e post de Shared e Device Groups. Alterações em outras partes do XML aparecem em Mudanças XML."),
        ("Escopo", "Comparação offline de configuração. Mudança de referência ou conteúdo não simula o encaminhamento ou o resultado de um commit."),
    ]
    rule_stats, intervals = {}, {}
    if traffic_csv:
        stats, rule_stats, flows, unlinked, intervals, coverage = traffic(traffic_csv, pairs)
        summary += [("CSV de tráfego", traffic_csv.name), ("SHA-256 CSV", hashes[2]),
                    ("Primeiro horário legível", coverage[0] or "Indisponível"),
                    ("Último horário legível", coverage[1] or "Indisponível"),
                    ("Regras alteradas com tráfego observado", len(changed & rule_stats.keys())),
                    ("Critério de vínculo", "Somente Security. UUID quando informado; na ausência, DG + nome ou nome único."),
                    ("Sessões identificáveis", "Chave por regra: serial, vsys, ID e horário de início. Repeat Count não é somado como sessões."),
                    ("Cobertura", "O CSV representa o período e filtros exportados. Sem registro vinculado não comprova ausência de uso.")]
        summary += list(stats.items())
    else:
        summary.append(("CSV de tráfego", "Não informado"))
    report.add("Resumo", ("Indicador", "Resultado"), summary)
    report.add("Mudanças XML", ("Tipo", "Caminho XML", "Antes", "Depois", "Linha inicial", "Linha final"), differences)
    report.add("Objetos", ("Escopo", "Tipo", "Objeto", "Mudança", "XPath", "XML inicial", "XML final"), objects)
    stat_keys = ("Linhas", "Allow", "Deny", "Drop", "Outras", "Sessões identificáveis", "Linhas sem chave de sessão", "UUID", "DG + nome", "Nome único")

    def rule_rows():
        for i, (old, new, status) in enumerate(pairs, 1):
            r = new or old
            stat = rule_stats.get(i, {})
            visible = "CSV não informado" if not traffic_csv else "Fora do vínculo Traffic" if r.base.rsplit("/", 1)[-1] != "security" else "Observado" if stat else "Sem registro vinculado"
            yield (i, r.scope, r.base, old.name if old else "", new.name if new else "",
                   old.element.get("uuid", "") if old else "", new.element.get("uuid", "") if new else "",
                   status, old.position if old else None, new.position if new else None,
                   old.xpath if old else "", new.xpath if new else "", visible,
                   *(stat.get(k, 0) if traffic_csv and visible != "Fora do vínculo Traffic" else None for k in stat_keys),
                   *intervals.get(i, ("", "")))

    report.add("Regras", ("ID", "Escopo", "Rulebase", "Regra inicial", "Regra final", "UUID inicial", "UUID final", "Mudança no XML",
                          "Posição inicial", "Posição final", "XPath inicial", "XPath final", "Tráfego", *stat_keys,
                          "Primeiro horário", "Último horário"), rule_rows())
    report.add("Campos de regras", ("ID da regra", "Campo", "Antes", "Depois"), fields)
    if traffic_csv:
        report.add("Fluxos observados", ("ID da regra", "Regra", "Mudança na regra", "Ação", "Zona origem", "Origem", "Zona destino", "Destino", "Aplicação", "Protocolo", "Porta destino", "Linhas"),
                   ((key[0], (pairs[key[0] - 1][1] or pairs[key[0] - 1][0]).name, pairs[key[0] - 1][2], *key[1:], count)
                    for key, count in flows.most_common()))
        report.add("Logs sem vínculo", ("Motivo", "Regra no log", "UUID", "DG no log", "Linhas"),
                   ((*key, count) for key, count in unlinked.most_common()))
    if report.long_text:
        report.add("Texto longo", ("Referência", "Parte", "Conteúdo"), list(report.long_text))
    with tempfile.NamedTemporaryFile(dir=output.parent, suffix=".xlsx", delete=False) as staging:
        temporary = Path(staging.name)
    try:
        report.book.save(temporary)
        if [checksum(path) for path in sources] != hashes:
            raise ValueError("Um arquivo de entrada mudou durante a análise. Execute a comparação novamente.")
        os.replace(temporary, output)
    finally:
        temporary.unlink(missing_ok=True)
    return {"xml_differences": len(differences), "rules_changed": len(changed),
            "changed_rules_with_traffic": len(changed & rule_stats.keys()) if traffic_csv else None}
