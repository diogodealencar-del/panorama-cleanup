"""Relatórios do modo COMPARE: CSVs, XLSX e um HTML autocontido no modelo de auditoria
comparativa (cabeçalho, parecer, métricas, seções Arquivos, Regras, Objetos, Grupos,
Referências e Parecer, com tabelas filtráveis)."""

from __future__ import annotations

import html
from datetime import datetime
from pathlib import Path

from openpyxl import Workbook

from panorama_cleanup.report_generator import _fill_sheet, _write_csv
from panorama_cleanup.safety import ensure_writable_target
from panorama_cleanup.xml_compare import NOT_VALIDATED, VERDICT_DIFF, Comparison

e = html.escape

COLUMNS = {
    "files": ("role", "path", "size_bytes", "sha256", "well_formed"),
    "sections": ("section", "status", "detail"),
    "policy-types": ("policy", "original", "modified", "xml_changed"),
    "rulebases": ("scope", "rulebase", "original", "modified", "xml_changed"),
    "rule-order": ("scope", "rulebase", "original", "modified", "relative_order_preserved"),
    "rules": ("scope", "rulebase", "rule", "rule_modified", "uuid", "position_original",
              "position_modified", "matched_by", "status", "changed_fields", "worst_risk",
              "xpath_original", "xpath_modified"),
    "rule-field-changes": ("scope", "rulebase", "rule", "rule_modified", "uuid", "field", "kind",
                           "before", "after", "before_xml", "after_xml", "risk", "effective",
                           "detail"),
    "objects-by-type": ("object_type", "original", "modified", "added", "removed",
                        "modified_entries"),
    "objects-by-scope": ("scope", "object_type", "original", "modified", "added", "removed",
                         "modified_entries"),
    "object-changes": ("change", "risk", "scope", "object_type", "name", "changed_attributes",
                       "value_original", "value_modified", "description_original",
                       "description_modified", "xml_original", "xml_modified", "usage_original",
                       "usage_modified", "visible_equivalent", "line_original", "line_modified"),
    "classification": ("classification", "original", "modified"),
    "conflicts-by-type": ("object_type", "original", "modified"),
    "group-effective-diffs": ("context", "scope", "object_type", "name", "values_original",
                              "only_original_count", "common_count", "only_modified_count",
                              "values_modified", "only_original", "only_modified",
                              "usage_original", "usage_modified", "reached_by_rules_original",
                              "reached_by_rules_modified", "risk", "manual_check", "full_original",
                              "full_common", "full_modified"),
    "group-rule-trace": ("group", "object_type", "context", "rule_scope", "rulebase", "rule",
                         "field", "original_reference", "original_field_references",
                         "modified_field_references", "xpath"),
    "resolution-warnings": ("state", "scope", "source_type", "source_kind", "context", "field",
                            "value", "diagnosis", "occurrences_original", "occurrences_modified",
                            "risk"),
    "new-unresolved-references": ("source_kind", "source_scope", "source_type", "source_name",
                                  "field", "value"),
}


def _sets(cmp: Comparison, meta: dict) -> dict:
    rows = {
        "files": meta["files"], "sections": cmp.sections, "policy-types": cmp.policy_types,
        "rulebases": cmp.rulebases, "rule-order": cmp.rule_order, "rules": cmp.rules,
        "rule-field-changes": cmp.rule_changes, "objects-by-type": cmp.object_types,
        "objects-by-scope": cmp.object_scopes, "object-changes": cmp.object_changes,
        "classification": cmp.classification, "conflicts-by-type": cmp.conflicts_by_type,
        "group-effective-diffs": cmp.group_diffs, "group-rule-trace": cmp.group_rules,
        "resolution-warnings": cmp.resolution_warnings,
        "new-unresolved-references": cmp.new_unresolved,
    }
    return {name: (COLUMNS[name], rows[name]) for name in COLUMNS}


def write_compare_reports(cmp: Comparison, ctx, settings, meta: dict) -> list:
    out = settings.reports_dir / ctx.run_id
    out.mkdir(parents=True, exist_ok=True)

    def target(name: str) -> Path:
        return ensure_writable_target(out / name, settings, ctx.input_path)

    sets = _sets(cmp, meta)
    paths = [_write_csv(target(f"compare-{name}.csv"), cols, rows) for name, (cols, rows) in sets.items()]
    wb = Workbook()
    wb.remove(wb.active)
    for name, (cols, rows) in sets.items():
        _fill_sheet(wb.create_sheet(name[:31]), cols, rows)
    xlsx = target("compare-report.xlsx")
    wb.save(xlsx)
    paths.append(xlsx)
    html_path = target("compare-report.html")
    html_path.write_text(render_html(cmp, ctx, meta), encoding="utf-8")
    paths.append(html_path)
    return paths


_CSS = """
:root{font-family:system-ui,sans-serif;color:#152238;background:#eef2f7;line-height:1.5}
*{box-sizing:border-box}body{margin:0}main{max-width:1280px;margin:auto;padding:28px 30px 65px}
header{background:#132842;color:#fff;padding:28px 34px;border-radius:14px}
h1{margin:0;font-size:clamp(25px,4vw,40px);line-height:1.12}h2{font-size:23px;margin:28px 0 9px}
p{margin:10px 0}header p{color:#dce8f6}a{color:#135db1}
.alert{margin:20px 0;background:#fff6de;border-left:5px solid #ae6413;padding:12px 17px;border-radius:8px}
.alert.bad{background:#fde8e8;border-left-color:#a32020}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(155px,1fr));gap:12px;margin-top:18px}
.metric{background:white;padding:14px;border-radius:10px}.metric strong{display:block;color:#11345a;font-size:28px}
details{background:white;border-radius:9px;margin:12px 0;border:1px solid #d2dce9}
summary{cursor:pointer;padding:11px 15px;font-weight:650}summary small{color:#405b77;font-weight:500;margin-left:8px}
label{display:block;padding:0 15px 10px}input{width:min(455px,100%);font:inherit;padding:5px 9px}
.scroll{overflow:auto;max-height:73vh;border:1px solid #d2dce9;border-radius:8px;background:white}
details .scroll{margin:0 12px 12px}table{border-collapse:collapse;width:100%;font-size:13px}
th,td{text-align:left;padding:7px 10px;vertical-align:top;border-bottom:1px solid #dbe4ef;overflow-wrap:anywhere}
td{white-space:pre-wrap;min-width:65px}th{background:#e3edf8;color:#132842;position:sticky;top:0;z-index:1}
tbody tr:nth-child(even){background:#f8fafc}code{overflow-wrap:anywhere}li{margin:5px 0}
.r-ALTO{color:#a32020;font-weight:650}.r-MÉDIO{color:#ae6413;font-weight:650}
.r-BAIXO,.r-INFORMATIVO{color:#405b77}
.expand-group>p{margin:8px 15px}.expand-group[open]{min-width:430px}.expand-group .scroll{max-height:40vh}
.expand-group td{min-width:130px}
@media(max-width:590px){main{padding:13px}header{padding:20px}}
"""

_JS = """
document.querySelectorAll('input[type=search]').forEach(input=>{
    input.addEventListener('input', ()=>{
        const query=input.value.trim().toLocaleLowerCase('pt-BR');
        input.closest('details').querySelectorAll(':scope > .scroll > table > tbody > tr').forEach(row=>{
            row.hidden=query&&!row.textContent.toLocaleLowerCase('pt-BR').includes(query);
        });
    });
});
"""

_MONTHS = ("janeiro", "fevereiro", "março", "abril", "maio", "junho", "julho", "agosto",
           "setembro", "outubro", "novembro", "dezembro")


def _n(value) -> str:
    return f"{value:,}".replace(",", ".") if isinstance(value, int) else str(value)


def _table(headers, rows, keys=None, risk_key=None, numeric=()) -> str:
    """headers: [(rótulo, chave)]; rows: dicts."""
    head = "".join(f"<th scope='col'>{e(h)}</th>" for h, _ in headers)
    body = []
    for r in rows:
        cells = []
        for _, key in headers:
            v = r.get(key, "")
            text = _n(v) if key in numeric else str(v)
            cls = f" class='r-{e(text)}'" if key == risk_key else ""
            cells.append(f"<td{cls}>{e(text)}</td>")
        body.append("<tr>" + "".join(cells) + "</tr>")
    return (f"<div class='scroll'><table><thead><tr>{head}</tr></thead><tbody>"
            + "".join(body) + "</tbody></table></div>")


def _details(title, headers, rows, risk_key=None) -> str:
    return (f"<details><summary>{e(title)}<small>{_n(len(rows))}</small></summary>"
            f"<label>Filtrar <input type='search' placeholder='Digite para filtrar'></label>"
            + _table(headers, rows, risk_key=risk_key) + "</details>")


def _expand(row, trace) -> str:
    sets_table = _table(
        [("Original completo", "full_original"), ("Em comum", "full_common"),
         ("Candidato completo · mesmo nome", "full_modified")], [row])
    rules = ""
    if trace:
        rules = ("<p>Regras que alcançavam este grupo no original (referência direta). Os valores "
                 "abrangem o campo inteiro da regra.</p>"
                 + _table([("Regra", "rule"), ("Política", "rulebase"), ("Campo", "field"),
                           ("Referência original rastreada", "original_reference"),
                           ("Referências do campo original", "original_field_references"),
                           ("Referências do campo candidato", "modified_field_references")], trace))
    else:
        rules = "<p>Nenhuma referência direta de regra a este grupo foi rastreada no original neste Device Group.</p>"
    return f"<details class='expand-group'><summary>Ver conjuntos e regras</summary>{sets_table}{rules}</details>"


def render_html(cmp: Comparison, ctx, meta: dict) -> str:
    s = cmp.summary
    files = meta["files"]
    started = datetime.fromisoformat(ctx.started_at.replace("Z", "+00:00")) if ctx.started_at else datetime.now()
    date = f"{started.day} de {_MONTHS[started.month - 1]} de {started.year}"
    panos = f" · PAN-OS {cmp.metadata['panos']}" if cmp.metadata.get("panos") else ""
    bad = cmp.verdict == VERDICT_DIFF
    n_groups = len({(g["scope"], g["object_type"], g["name"]) for g in cmp.group_diffs})
    reached = s["group_context_diffs_reached_by_rules"]

    if bad:
        verdict = "<strong>Parecer geral: DIFERENÇAS RELEVANTES ENCONTRADAS.</strong> " + e("; ".join(cmp.attention)) + "."
    else:
        verdict = ("<strong>Parecer geral: INCONCLUSIVO para importação.</strong> "
                   "As regras mantêm os valores efetivos comparados. ")
        if cmp.group_diffs:
            verdict += (f"Há diferenças no conteúdo efetivo de grupos em {_n(len(cmp.group_diffs))} "
                        f"par(es) grupo-contexto, nenhum alcançado por regra no modificado. "
                        if not reached else "")
        verdict += ("A configuração efetiva nos firewalls e o resultado de Validate Changes "
                    "continuam pendentes.")
    metrics = [
        (_n(s["rules_modified"]) if s["rules_original"] == s["rules_modified"] else
         f"{_n(s['rules_original'])} → {_n(s['rules_modified'])}",
         "Regras em cada arquivo" if s["rules_original"] == s["rules_modified"] else "Regras (original → candidato)"),
        (_n(s["rules_field_changed"]), "Regras com referência reescrita"),
        (_n(s["field_changes_effective_differ"]), "Diferenças efetivas nas regras modeladas"),
        (_n(n_groups), "Grupos com valor diferente por contexto"),
    ]
    p = [
        "<!doctype html><html lang='pt-BR'><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width,initial-scale=1'>"
        f"<title>Auditoria Panorama | {e(date)}</title><style>{_CSS}</style></head><body><main>",
        f"<header><h1>Auditoria comparativa do Panorama</h1><p>{e(date)} · Original e candidato à "
        f"importação{e(panos)} · run <code>{e(ctx.run_id)}</code></p></header>",
        f"<section class='alert{' bad' if bad else ''}'>{verdict}</section>",
        "<div class='grid'>" + "".join(
            f"<div class='metric'><strong>{e(v)}</strong>{e(t)}</div>" for v, t in metrics) + "</div>",

        "<h2>Arquivos e estrutura</h2>",
        "<p>O XML original é a referência exclusiva dos achados. Os dois arquivos foram lidos "
        "integralmente e o sha256 de cada um foi conferido antes e depois da leitura. Nenhum arquivo "
        "de entrada foi alterado.</p>",
        _table([("Papel", "role"), ("Arquivo", "path"), ("Bytes", "size_bytes"), ("SHA-256", "sha256")], files),
        "<p>Seções fora de objetos inventariados e rulebases (cabeçalho, hierarquia, associações de "
        "dispositivos, templates, template stacks e demais configurações) "
        + ("preservaram o conteúdo." if not s["sections_different"] else
           f"apresentam {_n(s['sections_different'])} diferença(s); veja a tabela abaixo.") + "</p>",
        _details("Comparação por seção", [("Seção", "section"), ("Status", "status"), ("Detalhe", "detail")],
                 cmp.sections),

        "<h2>Regras</h2>",
        f"<p>{_n(s['rules_structural'])} regra(s) adicionada(s), removida(s), renomeada(s), reordenada(s) "
        f"ou com UUID diferente. Das {_n(s['rules_modified'])} regras, {_n(s['rules_identical'])} têm "
        "conteúdo XML idêntico após normalizar a indentação.</p>",
        f"<p>Foram registradas {_n(s['field_changes'])} alterações de campo; "
        f"{_n(s['field_changes_effective_equal'])} com valor efetivo igual em todos os contextos "
        f"(risco BAIXO) e {_n(s['field_changes_effective_differ'])} com valor efetivo diferente ou fora "
        "das regras permitidas (risco ALTO). A expansão compara endereços, grupos aninhados e serviços "
        "em cada Device Group, com a resolução do contexto mais próximo.</p>",
        _table([("Política", "policy"), ("Original", "original"), ("Candidato", "modified"),
                ("XML alterado", "xml_changed")], cmp.policy_types, numeric=("original", "modified", "xml_changed")),
        _table([("Campo", "field"), ("Ocorrências", "occurrences")],
               [{"field": k, "occurrences": v} for k, v in sorted(s["field_changes_by_field"].items())]),
        _details("Campos alterados com antes e depois",
                 [("Escopo", "scope"), ("Política", "rulebase"), ("Regra", "rule"), ("Campo", "field"),
                  ("Original", "before_xml"), ("Candidato", "after_xml"), ("Risco", "risk"),
                  ("Valor efetivo", "effective"), ("Detalhe", "detail")], cmp.rule_changes, "risk"),
        _details("Todas as regras",
                 [("Escopo", "scope"), ("Política", "rulebase"), ("Original", "rule"),
                  ("Candidato", "rule_modified"), ("Posição original", "position_original"),
                  ("Posição candidata", "position_modified"), ("Status", "status"),
                  ("Risco", "worst_risk"), ("XPath original", "xpath_original"),
                  ("XPath candidato", "xpath_modified")], cmp.rules, "worst_risk"),
        _details("Contagem por escopo e rulebase",
                 [("Escopo", "scope"), ("Rulebase", "rulebase"), ("Original", "original"),
                  ("Candidato", "modified"), ("Ordem relativa preservada", "relative_order_preserved")],
                 cmp.rule_order),

        "<h2>Objetos</h2>",
        f"<p>O saldo foi de {_n(s['objects_original'])} para {_n(s['objects_modified'])} entradas. Os "
        "totais são por chave escopo, tipo e nome; mudança de escopo ou de nome aparece como remoção e "
        f"adição. As {_n(s['objects_modified_entries'])} alterações em entradas que continuaram com a "
        "mesma chave são listadas no inventário. A coluna de possível equivalente mostra candidatos por "
        "nome ou valor visíveis do mesmo escopo para cima, sem atribuir autoria nem aprovação à troca.</p>",
        _table([("Tipo", "object_type"), ("Original", "original"), ("Candidato", "modified"),
                ("Adições", "added"), ("Remoções", "removed"), ("Modificações", "modified_entries")],
               cmp.object_types, numeric=("original", "modified", "added", "removed", "modified_entries")),
        _table([("Classificação entre escopos", "classification"), ("Original", "original"),
                ("Candidato", "modified")], cmp.classification, numeric=("original", "modified")),
        _table([("Tipo em conflito", "object_type"), ("Original", "original"), ("Candidato", "modified")],
               cmp.conflicts_by_type, numeric=("original", "modified")),
        _details("Inventário de alterações dos objetos",
                 [("Alteração", "change"), ("Risco", "risk"), ("Escopo", "scope"), ("Tipo", "object_type"),
                  ("Nome", "name"), ("Atributos alterados", "changed_attributes"),
                  ("XML original", "xml_original"), ("XML candidato", "xml_modified"),
                  ("Uso original", "usage_original"), ("Uso candidato", "usage_modified"),
                  ("Possível equivalente visível", "visible_equivalent"),
                  ("Linha original", "line_original"), ("Linha candidata", "line_modified")],
                 cmp.object_changes, "risk"),
        _details("Contagem de objetos por escopo e tipo",
                 [("Escopo", "scope"), ("Tipo", "object_type"), ("Original", "original"),
                  ("Candidato", "modified"), ("Adições", "added"), ("Remoções", "removed"),
                  ("Modificações", "modified_entries")], cmp.object_scopes),

        "<h2>Grupos e impacto</h2>",
        f"<p>{_n(n_groups)} grupo(s) de mesmo nome apresentam {_n(len(cmp.group_diffs))} diferença(s) de "
        "expansão por contexto, calculadas sem filtrar por uso por regra. Contextos em que o nome é "
        "sombreado por outro objeto no original não são comparados. Risco ALTO: alcançado por regra no "
        "candidato; MÉDIO: alcançado por regra só no original; INFORMATIVO: sem uso por regra.</p>",
    ]
    trace_by = {}
    for t in cmp.group_rules:
        trace_by.setdefault((t["group"], t["object_type"], t["context"]), []).append(t)
    head = [("Grupo", "name"), ("Contexto DG", "context"), ("Total original", "values_original"),
            ("Só no original", "only_original_count"), ("Em comum", "common_count"),
            ("Só no candidato", "only_modified_count"),
            ("Total candidato · mesmo nome", "values_modified"), ("Uso original", "usage_original"),
            ("Uso candidato · mesmo nome", "usage_modified"), ("Risco", "risk"),
            ("Conferência manual", "manual_check")]
    rows = []
    for g in cmp.group_diffs:
        r = dict(g)
        rows.append(r)
    table_head = "".join(f"<th scope='col'>{e(h)}</th>" for h, _ in head) + "<th scope='col'>Conjuntos e regras</th>"
    table_rows = []
    for g in rows:
        cells = "".join(
            f"<td class='r-{e(str(g[k]))}'>{e(str(g[k]))}</td>" if k == "risk" else f"<td>{e(str(g[k]))}</td>"
            for _, k in head)
        trace = trace_by.get((g["name"], g["object_type"], g["context"]), [])
        table_rows.append(f"<tr>{cells}<td>{_expand(g, trace)}</td></tr>")
    p.append(f"<details><summary>Diferenças efetivas nos grupos<small>{_n(len(rows))}</small></summary>"
             "<label>Filtrar <input type='search' placeholder='Digite para filtrar'></label>"
             f"<div class='scroll'><table id='grupos-completos'><thead><tr>{table_head}</tr></thead><tbody>"
             + "".join(table_rows) + "</tbody></table></div></details>")
    p += [
        _details("Rastreio das regras ligadas aos grupos no original",
                 [("Grupo", "group"), ("Tipo", "object_type"), ("Contexto DG", "context"),
                  ("Escopo da regra", "rule_scope"), ("Política", "rulebase"), ("Regra", "rule"),
                  ("Campo", "field"), ("Referência original encontrada", "original_reference"),
                  ("Referências do campo no candidato", "modified_field_references"), ("XPath", "xpath")],
                 cmp.group_rules),

        "<h2>Referências e limites da comparação</h2>",
        f"<p>Referências sem resolução: {_n(s['unresolved_original'])} no original e "
        f"{_n(s['unresolved_modified'])} no candidato; novas referências sem resolução: "
        f"{_n(s['new_unresolved_references'])}. As pré-existentes costumam ser objetos predefinidos "
        "(App-ID, serviços predefinidos, usuários) que o export não define; o diagnóstico é uma "
        "explicação provável, não prova de referência inválida.</p>",
        _details("Avisos de resolução",
                 [("Estado", "state"), ("Escopo", "scope"), ("Tipo", "source_type"), ("Origem", "source_kind"),
                  ("Contexto", "context"), ("Campo", "field"), ("Valor", "value"),
                  ("Diagnóstico", "diagnosis"), ("Ocorrências original", "occurrences_original"),
                  ("Ocorrências candidato", "occurrences_modified"), ("Risco", "risk")],
                 cmp.resolution_warnings, "risk"),
        "<p><strong>NÃO VALIDADO:</strong> " + e("; ".join(NOT_VALIDATED)) + ".</p>",
    ]
    if cmp.warnings:
        p.append(_details("Avisos da leitura dos XMLs", [("Aviso", "warning")],
                          [{"warning": w} for w in cmp.warnings]))
    p += [
        "<h2>Parecer</h2>",
        "<p>A comparação não usa plano aprovado: descreve o que mudou e prova a equivalência de valor "
        "efetivo das referências de regra pelo modelo de resolução do projeto. A aprovação para "
        "implantação continua dependendo do modo validate, de Validate Candidate Configuration no "
        "Panorama e da autorização humana.</p>",
        f"</main><script>{_JS}</script></body></html>\n",
    ]
    return "".join(p)
