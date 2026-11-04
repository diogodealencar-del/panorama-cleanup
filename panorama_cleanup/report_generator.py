"""Relatórios rastreáveis ao run (CLAUDE.md §13).

Etapa 2: relatórios de inventário. Etapa 3: dependências e classificação.
Todo arquivo é gravado em output/reports/<run_id>/ através da guarda de escrita.
"""

from __future__ import annotations

import csv
import html
from pathlib import Path

from openpyxl import Workbook
from openpyxl.styles import Font
from openpyxl.utils import get_column_letter

from panorama_cleanup.config import Settings
from panorama_cleanup.inventory import Inventory
from panorama_cleanup.run_context import RunContext, sha256_file
from panorama_cleanup.safety import ensure_writable_target

INVENTORY_COLUMNS = (
    "scope", "scope_type", "object_type", "type_label", "recognized", "name", "value",
    "value_sha256", "member_count", "members", "description", "tags", "disable_override",
    "xpath", "source_line",
)
DG_COLUMNS = (
    "name", "parent", "parent_source", "depth", "path", "readonly_id", "devices",
    "reference_templates",
)
SUMMARY_COLUMNS = ("scope", "object_type", "xml_count", "inventoried", "reconciled")
LIST_SEP = "; "


def inventory_rows(inv: Inventory) -> list:
    return [
        {
            "scope": r.scope,
            "scope_type": r.scope_type,
            "object_type": r.object_type,
            "type_label": r.type_label,
            "recognized": "yes" if r.recognized else "no",
            "name": r.name,
            "value": r.value,
            "value_sha256": r.value_sha256,
            "member_count": len(r.members),
            "members": LIST_SEP.join(r.members),
            "description": r.description,
            "tags": LIST_SEP.join(r.tags),
            "disable_override": r.disable_override,
            "xpath": r.xpath,
            "source_line": r.source_line,
        }
        for r in inv.records
    ]


def device_group_rows(inv: Inventory) -> list:
    return [
        {
            "name": dg.name,
            "parent": dg.parent,
            "parent_source": dg.parent_source,
            "depth": dg.depth,
            "path": " > ".join(dg.path),
            "readonly_id": dg.readonly_id,
            "devices": LIST_SEP.join(dg.devices),
            "reference_templates": LIST_SEP.join(dg.reference_templates),
        }
        for dg in inv.device_groups
    ]


def summary_rows(inv: Inventory) -> list:
    return [
        {
            "scope": scope,
            "object_type": rel,
            "xml_count": xml_count,
            "inventoried": count,
            "reconciled": "yes" if xml_count == count else "NO",
        }
        for scope, rel, xml_count, count in inv.reconciliation
    ]


def run_rows(inv: Inventory, ctx: RunContext) -> list:
    m = inv.metadata
    return [
        ("run_id", ctx.run_id),
        ("modo", ctx.mode),
        ("início (UTC)", ctx.started_at),
        ("arquivo de entrada", str(ctx.input_path)),
        ("sha256 da entrada", ctx.input_sha256),
        ("tamanho (bytes)", ctx.input_size),
        ("config version", m.config_version),
        ("detail-version", m.detail_version),
        ("urldb", m.urldb),
        ("entrada Panorama", m.panorama_entry),
        ("firewalls gerenciados", len(m.managed_devices)),
        ("templates", LIST_SEP.join(m.templates)),
        ("template stacks", LIST_SEP.join(m.template_stacks)),
        ("device groups", len(inv.device_groups)),
        ("objetos inventariados", len(inv.records)),
        ("avisos", len(inv.warnings)),
    ]


def write_inventory_reports(inv: Inventory, ctx: RunContext, settings: Settings) -> list:
    out = settings.reports_dir / ctx.run_id
    out.mkdir(parents=True, exist_ok=True)

    def target(name: str) -> Path:
        return ensure_writable_target(out / name, settings, ctx.input_path)

    inventory = inventory_rows(inv)
    groups = device_group_rows(inv)
    summary = summary_rows(inv)
    warnings = [{"warning": w} for w in inv.warnings]

    paths = [
        _write_csv(target("inventory.csv"), INVENTORY_COLUMNS, inventory),
        _write_csv(target("device-groups.csv"), DG_COLUMNS, groups),
        _write_csv(target("inventory-summary.csv"), SUMMARY_COLUMNS, summary),
        _write_csv(target("inventory-warnings.csv"), ("warning",), warnings),
    ]

    wb = Workbook()
    ws = wb.active
    ws.title = "Execução"
    _fill_sheet(ws, ("campo", "valor"), [dict(zip(("campo", "valor"), r)) for r in run_rows(inv, ctx)])
    _fill_sheet(wb.create_sheet("Resumo"), SUMMARY_COLUMNS, summary)
    _fill_sheet(wb.create_sheet("Device Groups"), DG_COLUMNS, groups)
    _fill_sheet(wb.create_sheet("Inventário"), INVENTORY_COLUMNS, inventory)
    _fill_sheet(wb.create_sheet("Avisos"), ("warning",), warnings)
    xlsx = target("inventory.xlsx")
    wb.save(xlsx)
    paths.append(xlsx)

    html_path = target("inventory.html")
    html_path.write_text(render_inventory_html(inv, ctx), encoding="utf-8")
    paths.append(html_path)
    return paths


def report_hashes(paths: list) -> dict:
    return {p.name: sha256_file(p) for p in paths}


def _write_csv(path: Path, columns: tuple, rows: list) -> Path:
    with open(path, "w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=columns)
        writer.writeheader()
        writer.writerows(rows)
    return path


def _fill_sheet(ws, columns: tuple, rows: list) -> None:
    ws.append(list(columns))
    for cell in ws[1]:
        cell.font = Font(bold=True)
    for row in rows:
        ws.append([row[c] for c in columns])
    # Texto vindo do XML nunca é interpretado como fórmula (ex.: descrição "=HYPERLINK(...)").
    for row_cells in ws.iter_rows(min_row=2):
        for cell in row_cells:
            if isinstance(cell.value, str):
                cell.data_type = "s"
    ws.freeze_panes = "A2"
    if rows:
        ws.auto_filter.ref = ws.dimensions
    for idx, col in enumerate(columns, 1):
        width = max([len(str(col))] + [len(str(r[col])) for r in rows[:500]])
        ws.column_dimensions[get_column_letter(idx)].width = min(max(width + 2, 10), 60)


_CSS = """
:root { --bg:#ffffff; --fg:#1d232b; --muted:#5b6673; --line:#d9dee4; --head:#f3f5f7;
        --warn:#8a5a00; --warn-bg:#fff6e0; --ok:#1f6f43; --bad:#a32020; }
@media (prefers-color-scheme: dark) {
  :root { --bg:#14181d; --fg:#e4e8ec; --muted:#9aa5b1; --line:#2c333b; --head:#1c2229;
          --warn:#f0c264; --warn-bg:#2b2412; --ok:#6fcf97; --bad:#ff8080; }
}
body { background:var(--bg); color:var(--fg); margin:0; padding:24px 16px;
       font:14px/1.5 -apple-system, "Segoe UI", Roboto, sans-serif; }
main { max-width:1100px; margin:0 auto; }
h1 { font-size:22px; margin:0 0 4px; } h2 { font-size:17px; margin:28px 0 8px; }
p.sub { color:var(--muted); margin:0 0 16px; }
.table-wrap { overflow-x:auto; }
table { border-collapse:collapse; width:100%; font-size:13px; }
th, td { border-bottom:1px solid var(--line); padding:6px 8px; text-align:left; vertical-align:top; }
th { background:var(--head); font-weight:600; }
td.n, th.n { text-align:right; font-variant-numeric:tabular-nums; }
code { font-family: ui-monospace, Menlo, monospace; font-size:12px; overflow-wrap:anywhere; }
.warn { background:var(--warn-bg); color:var(--warn); padding:8px 12px; border-radius:6px; margin:4px 0; }
.ok { color:var(--ok); font-weight:600; } .bad { color:var(--bad); font-weight:600; }
"""


def render_inventory_html(inv: Inventory, ctx: RunContext) -> str:
    e = html.escape
    parts = [
        "<!doctype html><html lang='pt-BR'><head><meta charset='utf-8'>",
        "<meta name='viewport' content='width=device-width, initial-scale=1'>",
        f"<title>Inventário {e(ctx.run_id)}</title><style>{_CSS}</style></head><body><main>",
        "<h1>Inventário de objetos do Panorama</h1>",
        f"<p class='sub'>Run <code>{e(ctx.run_id)}</code>. Detalhe completo por objeto em "
        "<code>inventory.csv</code> e <code>inventory.xlsx</code>.</p>",
        "<h2>Execução e backup</h2><div class='table-wrap'><table>",
    ]
    for label, value in run_rows(inv, ctx):
        parts.append(f"<tr><th>{e(label)}</th><td><code>{e(str(value))}</code></td></tr>")
    parts.append("</table></div>")

    parts.append("<h2>Hierarquia dos Device Groups</h2><div class='table-wrap'><table>")
    parts.append("<tr><th>Device Group</th><th>Pai</th><th>Origem da hierarquia</th>"
                 "<th class='n'>Firewalls</th><th>Templates de referência</th></tr>")
    for dg in inv.device_groups:
        parts.append(
            f"<tr><td>{e(' > '.join(dg.path))}</td><td>{e(dg.parent)}</td>"
            f"<td>{e(dg.parent_source)}</td><td class='n'>{len(dg.devices)}</td>"
            f"<td>{e(LIST_SEP.join(dg.reference_templates)) or '—'}</td></tr>"
        )
    parts.append("</table></div>")

    counts = inv.counts()
    scopes = inv.scopes()
    types = sorted({rel for _, rel in counts})
    parts.append("<h2>Objetos por tipo e escopo</h2><div class='table-wrap'><table><tr><th>Tipo</th>")
    parts += [f"<th class='n'>{e(s)}</th>" for s in scopes] + ["<th class='n'>Total</th></tr>"]
    for rel in types:
        row = [counts.get((s, rel), 0) for s in scopes]
        parts.append(f"<tr><td><code>{e(rel)}</code></td>")
        parts += [f"<td class='n'>{n or '—'}</td>" for n in row]
        parts.append(f"<td class='n'><b>{sum(row)}</b></td></tr>")
    totals = [sum(counts.get((s, rel), 0) for rel in types) for s in scopes]
    parts.append("<tr><th>Total</th>" + "".join(f"<th class='n'>{n}</th>" for n in totals)
                 + f"<th class='n'>{sum(totals)}</th></tr></table></div>")

    bad = [r for r in inv.reconciliation if r[2] != r[3]]
    parts.append("<h2>Reconciliação de contagem</h2>")
    if bad:
        parts.append(f"<p class='bad'>{len(bad)} divergência(s) entre XML e inventário.</p>")
    else:
        parts.append(f"<p class='ok'>{len(inv.reconciliation)} contêineres conferidos; "
                     "contagem do XML igual à do inventário em todos.</p>")

    parts.append(f"<h2>Avisos ({len(inv.warnings)})</h2>")
    parts += [f"<div class='warn'>{e(w)}</div>" for w in inv.warnings] or ["<p>Nenhum aviso.</p>"]
    parts.append("</main></body></html>\n")
    return "".join(parts)


# --- Etapa 3: análise ----------------------------------------------------------

CLASSIFICATION_COLUMNS = (
    "scope", "object_type", "name", "value", "classifications", "usage", "usage_reason",
    "evidence", "direct_rule_refs", "inbound_object_refs", "member_of", "name_status",
    "name_group", "value_cluster", "value_clusters", "possible_duplicate_group",
    "value_differs_in", "xpath",
)
DUPLICATE_COLUMNS = (
    "name_group", "name_status", "value_cluster", "value_clusters", "scope", "object_type",
    "name", "value", "usage", "literal_equal", "tags_equal", "description_equal", "tags",
    "description", "xpath",
)
POSSIBLE_DUPLICATE_COLUMNS = (
    "possible_duplicate_group", "object_type", "scope", "name", "value", "usage", "xpath",
)
DEPENDENCY_COLUMNS = (
    "source_kind", "source_scope", "source_type", "source_name", "rule_disabled", "field",
    "value", "target_scope", "target_type", "target_name", "confidence", "resolution",
    "contexts", "source_xpath", "target_xpath",
)
UNRESOLVED_COLUMNS = ("source_type", "field", "value", "resolution", "hint", "occurrences",
                      "scopes")


def classification_rows(an) -> list:
    inv, rows = an.inventory, []
    for i, r in enumerate(inv.records):
        u, n = an.usage[i], an.duplicates.name_status[i]
        rows.append({
            "scope": r.scope, "object_type": r.object_type, "name": r.name, "value": r.value,
            "classifications": "|".join(an.classifications(i)),
            "usage": u.status, "usage_reason": u.reason, "evidence": u.evidence,
            "direct_rule_refs": u.direct_rule_refs, "inbound_object_refs": u.inbound_object_refs,
            "member_of": LIST_SEP.join(u.member_of),
            "name_status": n.status, "name_group": n.group,
            "value_cluster": n.cluster or "", "value_clusters": n.clusters or "",
            "possible_duplicate_group": an.duplicates.possible_group_of.get(i, ""),
            "value_differs_in": LIST_SEP.join(an.duplicates.context_variants.get(i, ())),
            "xpath": r.xpath,
        })
    return rows


def duplicate_rows(an, status: str) -> list:
    inv, rows = an.inventory, []
    for i, r in enumerate(inv.records):
        n = an.duplicates.name_status[i]
        if n.status != status:
            continue
        rows.append({
            "name_group": n.group, "name_status": n.status, "value_cluster": n.cluster,
            "value_clusters": n.clusters, "scope": r.scope, "object_type": r.object_type,
            "name": r.name, "value": r.value, "usage": an.usage[i].status,
            "literal_equal": _yn(n.literal_equal), "tags_equal": _yn(n.tags_equal),
            "description_equal": _yn(n.description_equal), "tags": LIST_SEP.join(r.tags),
            "description": r.description, "xpath": r.xpath,
        })
    return sorted(rows, key=lambda x: (x["name_group"], x["value_cluster"], x["scope"]))


def possible_duplicate_rows(an) -> list:
    inv, rows = an.inventory, []
    for group_id, object_type, indexes in an.duplicates.possible_groups:
        for i in indexes:
            r = inv.records[i]
            rows.append({
                "possible_duplicate_group": group_id, "object_type": object_type,
                "scope": r.scope, "name": r.name, "value": r.value,
                "usage": an.usage[i].status, "xpath": r.xpath,
            })
    return rows


def dependency_rows(an) -> list:
    """Referências resolvidas, agregando contextos de avaliação na mesma linha."""
    inv, grouped = an.inventory, {}
    for ref in an.graph.references:
        if ref.target < 0:
            continue
        t = inv.records[ref.target]
        key = (ref.source_kind, ref.source_scope, ref.source_type, ref.source_name,
               ref.rule_disabled, ref.field, ref.value, ref.target, ref.confidence,
               ref.resolution, ref.source_xpath)
        grouped.setdefault(key, (t, []))[1].append(ref.context)
    rows = []
    for key, (t, contexts) in grouped.items():
        (kind, scope, stype, sname, disabled, field_, value, _, conf, res, sxpath) = key
        rows.append({
            "source_kind": kind, "source_scope": scope, "source_type": stype,
            "source_name": sname, "rule_disabled": _yn(disabled), "field": field_,
            "value": value, "target_scope": t.scope, "target_type": t.object_type,
            "target_name": t.name, "confidence": conf, "resolution": res,
            "contexts": LIST_SEP.join(dict.fromkeys(contexts)), "source_xpath": sxpath,
            "target_xpath": t.xpath,
        })
    return rows


def unresolved_rows(an) -> list:
    from panorama_cleanup.analysis import unresolved_hint

    grouped = {}
    for ref in an.graph.references:
        if ref.target >= 0:
            continue
        key = (ref.source_type, ref.field, ref.value, ref.resolution)
        entry = grouped.setdefault(key, {"count": 0, "scopes": {}, "hint": unresolved_hint(ref)})
        entry["count"] += 1
        entry["scopes"][ref.source_scope] = None
    return [
        {"source_type": k[0], "field": k[1], "value": k[2], "resolution": k[3],
         "hint": v["hint"], "occurrences": v["count"], "scopes": LIST_SEP.join(v["scopes"])}
        for k, v in sorted(grouped.items(), key=lambda kv: (-kv[1]["count"], kv[0]))
    ]


def analysis_summary(an) -> dict:
    from collections import Counter

    usage = Counter(u.status for u in an.usage)
    names = Counter(n.status for n in an.duplicates.name_status)
    refs = Counter((r.confidence) for r in an.graph.references)
    return {
        "objects": len(an.inventory.records),
        "used": usage.get("USED", 0), "unused": usage.get("UNUSED", 0),
        "unknown": usage.get("UNKNOWN", 0),
        "duplicate": names.get("DUPLICATE", 0), "conflict": names.get("CONFLICT", 0),
        "possible_duplicate_groups": len(an.duplicates.possible_groups),
        "possible_duplicate_objects": len(an.duplicates.possible_group_of),
        "context_dependent": len(an.duplicates.context_variants),
        "references_confirmed": refs.get("confirmed", 0),
        "references_possible": refs.get("possible", 0),
        "references_unresolved": refs.get("none", 0),
        "warnings": len(an.warnings),
    }


def write_analysis_reports(an, ctx: RunContext, settings: Settings, inventory_run: str) -> list:
    out = settings.reports_dir / ctx.run_id
    out.mkdir(parents=True, exist_ok=True)

    def target(name: str) -> Path:
        return ensure_writable_target(out / name, settings, ctx.input_path)

    classification = classification_rows(an)
    sets = {
        "object-classification.csv": (CLASSIFICATION_COLUMNS, classification),
        "used-objects.csv": (CLASSIFICATION_COLUMNS,
                             [r for r in classification if r["usage"] == "USED"]),
        "unused-objects.csv": (CLASSIFICATION_COLUMNS,
                               [r for r in classification if r["usage"] == "UNUSED"]),
        "unknown-objects.csv": (CLASSIFICATION_COLUMNS,
                                [r for r in classification if r["usage"] == "UNKNOWN"]),
        "context-dependent-objects.csv": (CLASSIFICATION_COLUMNS,
                                          [r for r in classification if r["value_differs_in"]]),
        "duplicate-objects.csv": (DUPLICATE_COLUMNS, duplicate_rows(an, "DUPLICATE")),
        "conflicting-objects.csv": (DUPLICATE_COLUMNS, duplicate_rows(an, "CONFLICT")),
        "possible-duplicates.csv": (POSSIBLE_DUPLICATE_COLUMNS, possible_duplicate_rows(an)),
        "dependencies.csv": (DEPENDENCY_COLUMNS, dependency_rows(an)),
        "unresolved-references.csv": (UNRESOLVED_COLUMNS, unresolved_rows(an)),
        "analysis-warnings.csv": (("warning",), [{"warning": w} for w in an.warnings]),
    }
    paths = [_write_csv(target(name), cols, rows) for name, (cols, rows) in sets.items()]

    summary = analysis_summary(an)
    wb = Workbook()
    ws = wb.active
    ws.title = "Execução"
    run = run_rows(an.inventory, ctx) + [("inventário de origem", inventory_run)]
    run += list(summary.items())
    _fill_sheet(ws, ("campo", "valor"), [dict(zip(("campo", "valor"), r)) for r in run])
    for sheet, name in (("Classificação", "object-classification.csv"),
                        ("Não utilizados", "unused-objects.csv"),
                        ("Desconhecidos", "unknown-objects.csv"),
                        ("Valor varia por DG", "context-dependent-objects.csv"),
                        ("Duplicados", "duplicate-objects.csv"),
                        ("Conflitos", "conflicting-objects.csv"),
                        ("Possíveis duplicados", "possible-duplicates.csv"),
                        ("Dependências", "dependencies.csv"),
                        ("Não resolvidas", "unresolved-references.csv"),
                        ("Avisos", "analysis-warnings.csv")):
        cols, rows = sets[name]
        _fill_sheet(wb.create_sheet(sheet), cols, rows)
    xlsx = target("analysis.xlsx")
    wb.save(xlsx)
    paths.append(xlsx)

    html_path = target("analysis.html")
    html_path.write_text(render_analysis_html(an, ctx, summary, inventory_run, sets),
                         encoding="utf-8")
    paths.append(html_path)
    return paths


def render_analysis_html(an, ctx, summary, inventory_run, sets) -> str:
    from collections import Counter

    e = html.escape
    usage_by_type = Counter((r.object_type, an.usage[i].status)
                            for i, r in enumerate(an.inventory.records))
    types = sorted({t for t, _ in usage_by_type})
    parts = [
        "<!doctype html><html lang='pt-BR'><head><meta charset='utf-8'>",
        "<meta name='viewport' content='width=device-width, initial-scale=1'>",
        f"<title>Análise {e(ctx.run_id)}</title><style>{_CSS}</style></head><body><main>",
        "<h1>Análise de dependências e classificação</h1>",
        f"<p class='sub'>Run <code>{e(ctx.run_id)}</code> sobre o inventário "
        f"<code>{e(inventory_run)}</code>. Detalhe por objeto em "
        "<code>object-classification.csv</code> e <code>analysis.xlsx</code>.</p>",
        "<h2>Resumo</h2><div class='table-wrap'><table>",
    ]
    for k, v in summary.items():
        parts.append(f"<tr><th>{e(k)}</th><td class='n'>{v}</td></tr>")
    parts.append("</table></div>")

    parts.append("<h2>Uso por tipo</h2><div class='table-wrap'><table><tr><th>Tipo</th>"
                 "<th class='n'>USED</th><th class='n'>UNUSED</th><th class='n'>UNKNOWN</th></tr>")
    for t in types:
        parts.append(f"<tr><td><code>{e(t)}</code></td>" + "".join(
            f"<td class='n'>{usage_by_type.get((t, s), 0) or '—'}</td>"
            for s in ("USED", "UNUSED", "UNKNOWN")) + "</tr>")
    parts.append("</table></div>")

    reasons = Counter(u.reason for u in an.usage if u.status != "USED")
    parts.append("<h2>Motivos de UNUSED e UNKNOWN</h2><div class='table-wrap'><table>")
    parts += [f"<tr><td>{e(k)}</td><td class='n'>{v}</td></tr>" for k, v in reasons.most_common()]
    parts.append("</table></div>")

    hints = Counter()
    for row in sets["unresolved-references.csv"][1]:
        hints[row["hint"]] += row["occurrences"]
    parts.append("<h2>Referências não resolvidas</h2><div class='table-wrap'><table>")
    parts += [f"<tr><td>{e(k)}</td><td class='n'>{v}</td></tr>" for k, v in hints.most_common()]
    parts.append("</table></div>")

    parts.append(f"<h2>Avisos ({len(an.warnings)})</h2>")
    parts += [f"<div class='warn'>{e(w)}</div>" for w in an.warnings] or ["<p>Nenhum aviso.</p>"]
    parts.append("</main></body></html>\n")
    return "".join(parts)


def _yn(value: bool) -> str:
    return "yes" if value else "no"


# --- Etapa 4: plano -------------------------------------------------------------

PLAN_OBJECT_COLUMNS = (
    "scope", "object_type", "name", "usage", "name_status", "action", "new_scope", "new_name",
    "target_scope", "target_name", "risk", "reason", "notes", "xpath",
)
OPERATION_COLUMNS = (
    "seq", "phase", "operation", "scope", "object_type", "name", "new_scope", "new_name",
    "source", "field", "risk", "impact", "validation", "xpath",
)
REFERENCE_UPDATE_COLUMNS = (
    "status", "kind", "scope", "source_type", "source_name", "rule_disabled", "field", "old",
    "new", "applies_to", "source_xpath",
)
DELETED_COLUMNS = ("status", "phase", "reason_kind", "scope", "object_type", "name", "value",
                   "replacement", "notes", "xpath")
RENAMED_COLUMNS = ("status", "scope", "object_type", "old_name", "new_name", "value",
                   "references_updated", "xpath")


def plan_object_rows(an, plan) -> list:
    inv, rows = an.inventory, []
    for i, r in enumerate(inv.records):
        d = plan.decisions[i]
        target = inv.records[d.target] if d.target >= 0 else None
        rows.append({
            "scope": r.scope, "object_type": r.object_type, "name": r.name,
            "usage": an.usage[i].status, "name_status": an.duplicates.name_status[i].status,
            "action": d.action, "new_scope": d.new_scope, "new_name": d.new_name,
            "target_scope": target.scope if target else "",
            "target_name": target.name if target else "",
            "risk": _plan_risk(d.action), "reason": d.reason,
            "notes": _plan_notes(an, i, d), "xpath": r.xpath,
        })
    return rows


def _plan_risk(action) -> str:
    from panorama_cleanup.planner import RISK
    return RISK[action]


def _plan_notes(an, i, d) -> str:
    from panorama_cleanup.planner import CONSOLIDATE
    r = an.inventory.records[i]
    if d.action == CONSOLIDATE:
        t = an.inventory.records[d.target]
        if r.description != t.description:
            return (f"descrição local {r.description!r} não será preservada; "
                    f"permanece {t.description!r}")
    return ""


def operation_rows(an, plan) -> list:
    from panorama_cleanup.planner import operation_sequence
    inv, rows = an.inventory, []
    counts = {}
    for u in plan.rule_updates:
        counts[u["target"]] = counts.get(u["target"], 0) + 1
    for seq, (phase, op, key, payload) in enumerate(operation_sequence(inv, plan), 1):
        r = inv.records[key]
        row = {"seq": seq, "phase": phase, "operation": op, "scope": r.scope,
               "object_type": r.object_type, "name": r.name, "new_scope": "", "new_name": "",
               "source": "", "field": "", "risk": "", "impact": "", "validation": "",
               "xpath": r.xpath}
        if op == "CREATE_SHARED":
            renamed = payload.new_name != r.name
            row.update(new_scope="shared", new_name=payload.new_name,
                       risk="médio" if renamed else "baixo",
                       impact=("cópia em Shared com novo nome; " if renamed else
                               "cópia em Shared com mesmo nome e valor; ")
                       + "objeto local continua prevalecendo no DG até a fase 3",
                       validation="objeto existe em Shared com valor e tags idênticos ao original")
        elif op == "UPDATE_RULE_REFERENCE":
            row.update(new_name=payload["new"], source=payload["source_name"],
                       field=payload["field"], risk="médio", xpath=payload["source_xpath"],
                       impact=f"regra do {payload['source_scope']}: membro {payload['old']!r} "
                              f"trocado por {payload['new']!r}, com o mesmo valor",
                       validation="valor efetivo da regra idêntico antes e depois (simulado)")
        elif op == "UPDATE_OBJECT_REFERENCE":
            src = inv.records[payload["source"]]
            row.update(new_name=payload["new"], source=f"{src.scope}:{src.name}",
                       field=payload["field"], risk="médio", xpath=src.xpath,
                       impact=f"membro {payload['old']!r} do objeto local trocado por {payload['new']!r}",
                       validation="valor efetivo do grupo idêntico (simulado)")
        elif op == "DELETE_LOCAL_COPY":
            target = (inv.records[payload.target] if payload.target >= 0 else None)
            replacement = (f"shared:{payload.new_name}" if target is None
                           else f"{target.scope}:{target.name}")
            row.update(new_scope="shared", new_name=payload.new_name, risk="baixo",
                       impact=f"DG passa a resolver {replacement} (mesmo valor)",
                       validation="nenhuma regra ou grupo muda de valor efetivo (simulado)")
        elif op == "DELETE_UNUSED":
            row.update(risk="médio", impact="objeto sem referência em nenhum contexto",
                       validation="ausência de referência comprovada; conferir configuração "
                                  "local dos firewalls antes de executar (Etapa 6)")
        rows.append(row)
    return rows


def reference_update_rows(an, plan) -> list:
    from panorama_cleanup.planner import KEEP_LOCAL, PENDING
    inv, rows = an.inventory, []
    for u in sorted(plan.rule_updates, key=lambda u: (u["source_scope"], u["source_name"], u["field"])):
        rows.append({"status": "planejado", "kind": "regra", "scope": u["source_scope"],
                     "source_type": u["source_type"], "source_name": u["source_name"],
                     "rule_disabled": _yn(u["rule_disabled"]), "field": u["field"],
                     "old": u["old"], "new": u["new"], "applies_to": "regra no Device Group",
                     "source_xpath": u["source_xpath"]})
    for u in plan.object_updates:
        src = inv.records[u["source"]]
        local = plan.decisions[u["source"]].action in (KEEP_LOCAL, PENDING)
        rows.append({"status": "planejado", "kind": "objeto", "scope": src.scope,
                     "source_type": src.object_type, "source_name": src.name,
                     "rule_disabled": "", "field": u["field"], "old": u["old"], "new": u["new"],
                     "applies_to": "objeto local" if local else "cópia criada em Shared",
                     "source_xpath": src.xpath})
    return rows


def deleted_rows(an, plan) -> list:
    from panorama_cleanup.planner import CONSOLIDATE, DELETE_UNUSED, MOVE, RENAME_MOVE
    inv, rows = an.inventory, []
    for i, r in enumerate(inv.records):
        d = plan.decisions[i]
        if d.action not in (MOVE, RENAME_MOVE, CONSOLIDATE, DELETE_UNUSED):
            continue
        if d.action == DELETE_UNUSED:
            kind, phase, replacement = "não utilizado", 4, ""
        elif d.action == CONSOLIDATE:
            t = inv.records[d.target]
            kind, phase = "cópia consolidada", 3
            replacement = f"{'shared' if t.scope == 'shared' else 'shared (via ' + t.scope + ')'}:{t.name}"
        else:
            kind, phase, replacement = "original movido para Shared", 3, f"shared:{d.new_name}"
        rows.append({"status": "planejado", "phase": phase, "reason_kind": kind, "scope": r.scope,
                     "object_type": r.object_type, "name": r.name, "value": r.value,
                     "replacement": replacement, "notes": _plan_notes(an, i, d), "xpath": r.xpath})
    return rows


def renamed_rows(an, plan) -> list:
    from panorama_cleanup.planner import RENAME_MOVE
    counts = {}
    for u in plan.rule_updates + plan.object_updates:
        counts[u["target"]] = counts.get(u["target"], 0) + 1
    return [
        {"status": "planejado", "scope": r.scope, "object_type": r.object_type,
         "old_name": r.name, "new_name": plan.decisions[i].new_name, "value": r.value,
         "references_updated": counts.get(i, 0), "xpath": r.xpath}
        for i, r in enumerate(an.inventory.records) if plan.decisions[i].action == RENAME_MOVE
    ]


def plan_summary(an, plan) -> dict:
    from collections import Counter
    from panorama_cleanup.planner import KEEP_LOCAL, PENDING

    actions = Counter(d.action for d in plan.decisions.values())
    dg = [i for i, r in enumerate(an.inventory.records) if r.scope != "shared"]
    rules = {u["source_xpath"] for u in plan.rule_updates}
    return {
        "dg_objects": len(dg),
        "move": actions.get("MOVE", 0),
        "rename_move": actions.get("RENAME_MOVE", 0),
        "consolidate": actions.get("CONSOLIDATE", 0),
        "delete_unused_dg": sum(1 for i in dg if plan.decisions[i].action == "DELETE_UNUSED"),
        "delete_unused_shared": sum(1 for i, r in enumerate(an.inventory.records)
                                    if r.scope == "shared" and plan.decisions[i].action == "DELETE_UNUSED"),
        "keep_local": actions.get(KEEP_LOCAL, 0),
        "pending_decision": actions.get(PENDING, 0),
        "rule_reference_updates": len(plan.rule_updates),
        "rules_changed": len(rules),
        "object_reference_updates": len(plan.object_updates),
        "demotions": len(plan.demotions),
        "iterations": plan.iterations,
        "verification_checks": sum(v[2] for v in plan.verification),
        "verification_differences": sum(v[3] for v in plan.verification),
    }


def write_plan_reports(an, plan, ctx: RunContext, settings: Settings, analysis_run: str) -> list:
    import json
    from panorama_cleanup.planner import PHASES

    out = settings.reports_dir / ctx.run_id
    out.mkdir(parents=True, exist_ok=True)

    def target(name: str) -> Path:
        return ensure_writable_target(out / name, settings, ctx.input_path)

    objects = plan_object_rows(an, plan)
    sets = {
        "plan-objects.csv": (PLAN_OBJECT_COLUMNS, objects),
        "plan-operations.csv": (OPERATION_COLUMNS, operation_rows(an, plan)),
        "renamed-objects.csv": (RENAMED_COLUMNS, renamed_rows(an, plan)),
        "reference-updates.csv": (REFERENCE_UPDATE_COLUMNS, reference_update_rows(an, plan)),
        "deleted-objects.csv": (DELETED_COLUMNS, deleted_rows(an, plan)),
        "kept-local-objects.csv": (PLAN_OBJECT_COLUMNS,
                                   [r for r in objects if r["action"] in ("KEEP_LOCAL", "PENDING_DECISION")]),
        "plan-verification.csv": (("phase", "phase_name", "checks", "differences"), [
            {"phase": p, "phase_name": dict(PHASES)[p], "checks": c, "differences": n}
            for p, _, c, n in plan.verification]),
        "plan-demotions.csv": (("scope", "object_type", "name", "previous_action", "reason"), [
            {"scope": an.inventory.records[k].scope, "object_type": an.inventory.records[k].object_type,
             "name": an.inventory.records[k].name, "previous_action": a, "reason": why}
            for k, a, why in plan.demotions]),
    }
    paths = [_write_csv(target(name), cols, rows) for name, (cols, rows) in sets.items()]

    summary = plan_summary(an, plan)
    plan_json = {
        "run_id": ctx.run_id, "analysis_run": analysis_run,
        "input": {"path": str(ctx.input_path), "sha256": ctx.input_sha256},
        "panos_version": an.inventory.metadata.config_version,
        "summary": summary,
        "phases": [{"phase": p, "name": n} for p, n in PHASES],
        "operations": sets["plan-operations.csv"][1],
        "decisions": objects,
    }
    json_path = target("plan.json")
    json_path.write_text(json.dumps(plan_json, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    paths.append(json_path)

    wb = Workbook()
    ws = wb.active
    ws.title = "Execução"
    run = run_rows(an.inventory, ctx) + [("análise de origem", analysis_run)] + list(summary.items())
    _fill_sheet(ws, ("campo", "valor"), [dict(zip(("campo", "valor"), r)) for r in run])
    for sheet, name in (("Operações", "plan-operations.csv"), ("Objetos", "plan-objects.csv"),
                        ("Renomeados", "renamed-objects.csv"),
                        ("Referências", "reference-updates.csv"),
                        ("Exclusões", "deleted-objects.csv"),
                        ("Mantidos no DG", "kept-local-objects.csv"),
                        ("Verificação", "plan-verification.csv"),
                        ("Rebaixados", "plan-demotions.csv")):
        cols, rows = sets[name]
        _fill_sheet(wb.create_sheet(sheet), cols, rows)
    xlsx = target("plan.xlsx")
    wb.save(xlsx)
    paths.append(xlsx)

    html_path = target("plan.html")
    html_path.write_text(render_plan_html(an, plan, ctx, summary, analysis_run, sets), encoding="utf-8")
    paths.append(html_path)
    return paths


def render_plan_html(an, plan, ctx, summary, analysis_run, sets) -> str:
    from collections import Counter
    from panorama_cleanup.planner import PHASES

    e = html.escape
    objects = sets["plan-objects.csv"][1]
    parts = [
        "<!doctype html><html lang='pt-BR'><head><meta charset='utf-8'>",
        "<meta name='viewport' content='width=device-width, initial-scale=1'>",
        f"<title>Plano {e(ctx.run_id)}</title><style>{_CSS}</style></head><body><main>",
        "<h1>Plano de centralização em Shared</h1>",
        f"<p class='sub'>Run <code>{e(ctx.run_id)}</code> sobre a análise <code>{e(analysis_run)}</code>. "
        "Somente leitura: nenhuma alteração foi executada.</p>",
        "<h2>Resumo</h2><div class='table-wrap'><table>",
    ]
    parts += [f"<tr><th>{e(k)}</th><td class='n'>{v}</td></tr>" for k, v in summary.items()]
    parts.append("</table></div>")

    parts.append("<h2>Verificação de transparência</h2>")
    diffs = summary["verification_differences"]
    parts.append(f"<p class='{'ok' if diffs == 0 else 'bad'}'>{diffs} diferença(s) de valor efetivo "
                 f"em {summary['verification_checks']} verificações (referência de regra × firewall × fase).</p>")
    parts.append("<div class='table-wrap'><table><tr><th>Fase</th><th class='n'>Verificações</th>"
                 "<th class='n'>Diferenças</th></tr>")
    for p, _, c, n in plan.verification:
        parts.append(f"<tr><td>{p}. {e(dict(PHASES)[p])}</td><td class='n'>{c}</td><td class='n'>{n}</td></tr>")
    parts.append("</table></div>")

    by_scope = Counter((r["scope"], r["action"]) for r in objects if r["scope"] != "shared")
    actions = ["MOVE", "RENAME_MOVE", "CONSOLIDATE", "DELETE_UNUSED", "KEEP_LOCAL", "PENDING_DECISION"]
    parts.append("<h2>Objetos de Device Group por decisão</h2><div class='table-wrap'><table><tr><th>DG</th>")
    parts += [f"<th class='n'>{a}</th>" for a in actions] + ["</tr>"]
    for dg in an.inventory.scopes()[1:]:
        parts.append(f"<tr><td>{e(dg)}</td>" + "".join(
            f"<td class='n'>{by_scope.get((dg, a), 0) or '—'}</td>" for a in actions) + "</tr>")
    parts.append("</table></div>")

    reasons = Counter(r["reason"].split(" (")[0].split(":")[0] for r in sets["kept-local-objects.csv"][1])
    parts.append("<h2>Motivos para manter no Device Group</h2><div class='table-wrap'><table>")
    parts += [f"<tr><td>{e(k)}</td><td class='n'>{v}</td></tr>" for k, v in reasons.most_common()]
    parts.append("</table></div>")

    parts.append("<h2>Riscos por operação</h2><div class='table-wrap'><table>"
                 "<tr><th>Operação</th><th>Risco</th><th>Impacto e mitigação</th></tr>")
    for op, risk, text in RISK_TEXT:
        parts.append(f"<tr><td><code>{e(op)}</code></td><td>{e(risk)}</td><td>{e(text)}</td></tr>")
    parts.append("</table></div></main></body></html>\n")
    return "".join(parts)


RISK_TEXT = (
    ("CREATE_SHARED", "baixo",
     "Cria a cópia em Shared; o objeto local de mesmo nome continua prevalecendo no DG até a fase 3. "
     "Novos nomes ficam visíveis em todos os DGs: colisões foram verificadas."),
    ("UPDATE_RULE_REFERENCE", "médio",
     "Edita o membro de uma regra do DG (único tipo de alteração em regra permitido). O valor efetivo "
     "é o mesmo; erro de digitação na execução quebraria a regra, por isso a Etapa 8 recompara."),
    ("UPDATE_OBJECT_REFERENCE", "médio", "Troca o nome de um membro em grupo local; mesmo valor efetivo."),
    ("DELETE_LOCAL_COPY", "baixo",
     "Remove a cópia local; o DG passa a resolver o objeto de Shared com valor idêntico. Descrições "
     "locais diferentes são perdidas (listadas em notes)."),
    ("DELETE_UNUSED", "médio",
     "Sem referência no backup. Não detecta uso por configuração local dos firewalls, scripts ou API; "
     "executar apenas na Etapa 6, após conferência."),
    ("KEEP_LOCAL", "nenhum", "Nenhuma alteração; objetivo de centralização não atingido para o objeto."),
)


# --- Etapa 5: aplicação ---------------------------------------------------------

APPLIED_COLUMNS = ("seq", "phase", "operation", "xpath", "detail", "before", "after")
POLICY_COLUMNS = ("scope", "rulebase", "rule", "position", "uuid", "uuid_unchanged", "status", "detail")
CHECK_COLUMNS = ("check", "result", "detail")


def write_apply_reports(an, result, checks, policy_rows, ctx, settings, plan_run, plan_dir,
                        xml_path, xml_sha) -> list:
    import csv as _csv

    out = settings.reports_dir / ctx.run_id
    out.mkdir(parents=True, exist_ok=True)

    def target(name: str) -> Path:
        return ensure_writable_target(out / name, settings, ctx.input_path)

    def plan_rows(name):
        with open(Path(plan_dir) / name, encoding="utf-8", newline="") as fh:
            return list(_csv.DictReader(fh))

    created = {src: dst for dst, src in result.created_xpaths}
    deleted = set(result.deleted_xpaths)
    renamed = [dict(r, status="aplicado" if r["xpath"] in created else "NÃO APLICADO")
               for r in plan_rows("renamed-objects.csv")]
    refs = []
    for r in plan_rows("reference-updates.csv"):
        if r["applies_to"] == "cópia criada em Shared":
            ok = r["source_xpath"] in result.copy_member_renames
        elif r["kind"] == "regra":
            ok = (r["source_xpath"], r["field"], r["old"]) in result.rule_renames
        else:
            ok = any(x[0] == r["source_xpath"] and x[2] == r["old"] for x in result.object_renames)
        refs.append(dict(r, status="aplicado" if ok else "NÃO APLICADO"))
    removed = [dict(r, status="aplicado" if r["xpath"] in deleted else "NÃO APLICADO")
               for r in plan_rows("deleted-objects.csv") if r["phase"] == "3"]
    created_rows = [{"source_xpath": src, "shared_xpath": dst} for dst, src in result.created_xpaths]

    sets = {
        "applied-changes.csv": (APPLIED_COLUMNS, [c.__dict__ for c in result.changes]),
        "created-objects.csv": (("source_xpath", "shared_xpath"), created_rows),
        "renamed-objects.csv": (RENAMED_COLUMNS, renamed),
        "reference-updates.csv": (REFERENCE_UPDATE_COLUMNS, refs),
        "deleted-objects.csv": (DELETED_COLUMNS, removed),
        "policy-comparison.csv": (POLICY_COLUMNS, policy_rows),
        "integrity-checks.csv": (CHECK_COLUMNS, [
            {"check": c.name, "result": "OK" if c.passed else "FALHA", "detail": c.detail}
            for c in checks]),
    }
    paths = [_write_csv(target(name), cols, rows) for name, (cols, rows) in sets.items()]

    e = html.escape
    passed = all(c.passed for c in checks)
    from collections import Counter
    ops = Counter(c.operation for c in result.changes)
    parts = [
        "<!doctype html><html lang='pt-BR'><head><meta charset='utf-8'>",
        "<meta name='viewport' content='width=device-width, initial-scale=1'>",
        f"<title>Conversão {e(ctx.run_id)}</title><style>{_CSS}</style></head><body><main>",
        "<h1>Conversão para Shared</h1>",
        f"<p class='sub'>Run <code>{e(ctx.run_id)}</code> aplicando o plano <code>{e(plan_run)}</code>. "
        "Original inalterado; XML transformado:</p>",
        f"<p><code>{e(str(xml_path))}</code><br>sha256 <code>{e(xml_sha)}</code></p>",
        f"<h2>Integridade</h2><p class='{'ok' if passed else 'bad'}'>"
        f"{sum(c.passed for c in checks)}/{len(checks)} verificações aprovadas.</p>",
        "<div class='table-wrap'><table><tr><th>Verificação</th><th>Resultado</th><th>Detalhe</th></tr>",
    ]
    for c in checks:
        parts.append(f"<tr><td>{e(c.name)}</td><td class='{'ok' if c.passed else 'bad'}'>"
                     f"{'OK' if c.passed else 'FALHA'}</td><td>{e(c.detail)}</td></tr>")
    parts.append("</table></div><h2>Alterações aplicadas</h2><div class='table-wrap'><table>")
    parts += [f"<tr><td><code>{e(k)}</code></td><td class='n'>{v}</td></tr>" for k, v in sorted(ops.items())]
    status = Counter(r["status"] for r in policy_rows)
    parts.append("</table></div><h2>Políticas</h2><div class='table-wrap'><table>")
    parts += [f"<tr><td>{e(k)}</td><td class='n'>{v}</td></tr>" for k, v in status.most_common()]
    parts.append("</table></div><p>A exclusão de objetos não utilizados (fase 4 do plano) não foi "
                 "executada: pertence à Etapa 6.</p></main></body></html>\n")
    html_path = target("apply.html")
    html_path.write_text("".join(parts), encoding="utf-8")
    paths.append(html_path)
    return paths


# --- Etapa 6: remoção de não utilizados ----------------------------------------

REVIEW_COLUMNS = ("status", "seq", "scope", "object_type", "name", "usage_now", "reason",
                  "inbound_refs", "inbound_sources", "text_hits", "value", "xpath")


def write_cleanup_reports(an_converted, candidates, result, checks, policy_rows, ctx, settings,
                          plan_run, convert_run, xml_path, xml_sha) -> list:
    from collections import Counter

    out = settings.reports_dir / ctx.run_id
    out.mkdir(parents=True, exist_ok=True)

    def target(name: str) -> Path:
        return ensure_writable_target(out / name, settings, ctx.input_path)

    inv = an_converted.inventory
    deleted = set(result.deleted_xpaths)
    rows = []
    for c in sorted(candidates, key=lambda c: c.seq):
        rec = inv.records[c.index] if c.index >= 0 else None
        rows.append({
            "status": "excluído" if c.xpath in deleted else "mantido",
            "seq": c.seq, "scope": c.scope, "object_type": c.object_type, "name": c.name,
            "usage_now": c.usage, "reason": c.reason, "inbound_refs": len(c.inbound),
            "inbound_sources": LIST_SEP.join(sorted({f"{r.source_scope}:{r.source_name}"
                                                     for r in c.inbound})),
            "text_hits": LIST_SEP.join(c.text_hits), "value": rec.value if rec else "",
            "xpath": c.xpath,
        })
    sets = {
        "unused-review.csv": (REVIEW_COLUMNS, rows),
        "deleted-objects.csv": (REVIEW_COLUMNS, [r for r in rows if r["status"] == "excluído"]),
        "kept-unused-objects.csv": (REVIEW_COLUMNS, [r for r in rows if r["status"] == "mantido"]),
        "applied-changes.csv": (APPLIED_COLUMNS, [c.__dict__ for c in result.changes]),
        "policy-comparison.csv": (POLICY_COLUMNS, policy_rows),
        "integrity-checks.csv": (CHECK_COLUMNS, [
            {"check": c.name, "result": "OK" if c.passed else "FALHA", "detail": c.detail}
            for c in checks]),
    }
    paths = [_write_csv(target(name), cols, rs) for name, (cols, rs) in sets.items()]

    e = html.escape
    passed = all(c.passed for c in checks)
    by_type = Counter((r["scope"] == "shared", r["object_type"], r["status"]) for r in rows)
    parts = [
        "<!doctype html><html lang='pt-BR'><head><meta charset='utf-8'>",
        "<meta name='viewport' content='width=device-width, initial-scale=1'>",
        f"<title>Remoção {e(ctx.run_id)}</title><style>{_CSS}</style></head><body><main>",
        "<h1>Remoção de objetos não utilizados</h1>",
        f"<p class='sub'>Run <code>{e(ctx.run_id)}</code>; plano <code>{e(plan_run)}</code>; "
        f"XML convertido de <code>{e(convert_run)}</code>.</p>",
        f"<p>XML final: <code>{e(str(xml_path))}</code><br>sha256 <code>{e(xml_sha)}</code></p>",
        f"<h2>Integridade</h2><p class='{'ok' if passed else 'bad'}'>"
        f"{sum(c.passed for c in checks)}/{len(checks)} verificações aprovadas.</p>",
        "<div class='table-wrap'><table><tr><th>Verificação</th><th>Resultado</th><th>Detalhe</th></tr>",
    ]
    for c in checks:
        parts.append(f"<tr><td>{e(c.name)}</td><td class='{'ok' if c.passed else 'bad'}'>"
                     f"{'OK' if c.passed else 'FALHA'}</td><td>{e(c.detail)}</td></tr>")
    parts.append("</table></div><h2>Candidatos por tipo</h2><div class='table-wrap'><table>"
                 "<tr><th>Escopo</th><th>Tipo</th><th>Status</th><th class='n'>Objetos</th></tr>")
    for (is_shared, t, status), n in sorted(by_type.items()):
        parts.append(f"<tr><td>{'Shared' if is_shared else 'Device Group'}</td><td><code>{e(t)}</code>"
                     f"</td><td>{e(status)}</td><td class='n'>{n}</td></tr>")
    reasons = Counter(r["reason"] for r in rows if r["status"] == "mantido")
    parts.append("</table></div><h2>Motivos para manter</h2><div class='table-wrap'><table>")
    parts += [f"<tr><td>{e(k)}</td><td class='n'>{v}</td></tr>" for k, v in reasons.most_common()]
    parts.append("</table></div></main></body></html>\n")
    html_path = target("cleanup.html")
    html_path.write_text("".join(parts), encoding="utf-8")
    paths.append(html_path)
    return paths


# --- Etapa 7: comandos ------------------------------------------------------------

def write_command_reports(checks, files, api_ops, ctx, settings, meta) -> list:
    from collections import Counter

    out = settings.reports_dir / ctx.run_id
    out.mkdir(parents=True, exist_ok=True)
    rows = [{"check": n, "result": "PENDENTE" if ok is None else ("OK" if ok else "FALHA"),
             "detail": d} for n, ok, d in checks]
    path = ensure_writable_target(out / "generation-checks.csv", settings, ctx.input_path)
    _write_csv(path, CHECK_COLUMNS, rows)
    e = html.escape
    by_phase = Counter((o.phase, o.operation, o.action) for o in api_ops)
    parts = [
        "<!doctype html><html lang='pt-BR'><head><meta charset='utf-8'>",
        "<meta name='viewport' content='width=device-width, initial-scale=1'>",
        f"<title>Comandos {e(ctx.run_id)}</title><style>{_CSS}</style></head><body><main>",
        "<h1>Comandos para o Panorama</h1>",
        f"<p class='sub'>Run <code>{e(ctx.run_id)}</code>. PAN-OS {e(meta['panos'])}. "
        f"Arquivos em <code>{e(str(settings.cli_dir / ctx.run_id))}</code>. Nada foi executado.</p>",
        "<h2>Verificações</h2><div class='table-wrap'><table>",
    ]
    parts += [f"<tr><td>{e(r['check'])}</td><td class='{'ok' if r['result'] == 'OK' else 'bad' if r['result'] == 'FALHA' else 'warn'}'>"
              f"{r['result']}</td><td>{e(r['detail'])}</td></tr>" for r in rows]
    parts.append("</table></div><h2>Operações</h2><div class='table-wrap'><table>"
                 "<tr><th>Fase</th><th>Operação</th><th>Ação XML API</th><th class='n'>Qtde</th></tr>")
    parts += [f"<tr><td>{p}</td><td>{e(o)}</td><td>{e(a)}</td><td class='n'>{n}</td></tr>"
              for (p, o, a), n in sorted(by_phase.items())]
    parts.append("</table></div><h2>Arquivos</h2><ul>")
    parts += [f"<li><code>{e(name)}</code></li>" for name in sorted(files)]
    parts.append("</ul></main></body></html>\n")
    html_path = ensure_writable_target(out / "commands.html", settings, ctx.input_path)
    html_path.write_text("".join(parts), encoding="utf-8")
    return [path, html_path]


# --- Etapa 8: validação final -------------------------------------------------------

def write_validation_reports(v, ctx, settings, meta) -> list:
    from collections import Counter

    out = settings.reports_dir / ctx.run_id
    out.mkdir(parents=True, exist_ok=True)

    def target(name: str) -> Path:
        return ensure_writable_target(out / name, settings, ctx.input_path)

    checks = [{"area": a, "check": n, "result": "OK" if ok else "FALHA", "detail": d}
              for a, n, ok, d in v.checks]
    sets = {
        "validation-checks.csv": (("area", "check", "result", "detail"), checks),
        "section-comparison.csv": (("section", "status", "detail"), v.sections),
        "policy-comparison.csv": (POLICY_COLUMNS, v.policies),
        "policy-field-changes.csv": (("scope", "rulebase", "rule", "uuid", "field", "status",
                                      "before", "after"), v.policy_changes),
        "object-comparison.csv": (("scope", "object_type", "name", "plan_action", "status",
                                   "detail", "xpath"), v.objects),
        "reference-validation.csv": (("source_kind", "source_xpath", "field", "value",
                                      "resolution", "status"), v.references),
        "tag-validation.csv": (("scope", "name", "value", "status", "xpath"), v.tags),
        "remaining-dg-objects.csv": (("scope", "object_type", "name", "reason", "xpath"),
                                     v.remaining),
    }
    paths = [_write_csv(target(name), cols, rows) for name, (cols, rows) in sets.items()]

    e = html.escape
    ok = v.result == "APROVADO"
    parts = [
        "<!doctype html><html lang='pt-BR'><head><meta charset='utf-8'>",
        "<meta name='viewport' content='width=device-width, initial-scale=1'>",
        f"<title>Validação {e(ctx.run_id)}</title><style>{_CSS}</style></head><body><main>",
        "<h1>Relatório de validação</h1>",
        f"<p class='sub'>Run <code>{e(ctx.run_id)}</code>. Original <code>{e(str(ctx.input_path))}</code> "
        f"(sha256 <code>{e(ctx.input_sha256)}</code>) x final <code>{e(meta['final_path'])}</code> "
        f"(sha256 <code>{e(meta['final_sha'])}</code>). Plano <code>{e(meta['plan_run'])}</code>, "
        f"conversão <code>{e(meta['convert_run'])}</code>, remoção <code>{e(meta['cleanup_run'])}</code>.</p>",
        f"<h2>Resultado: <span class='{'ok' if ok else 'bad'}'>{e(v.result)}</span></h2>",
        f"<p>{sum(c['result'] == 'OK' for c in checks)} de {len(checks)} verificações aprovadas.</p>",
        "<div class='table-wrap'><table><tr><th>Área</th><th>Verificação</th><th>Resultado</th>"
        "<th>Detalhe</th></tr>",
    ]
    for c in checks:
        parts.append(f"<tr><td>{e(c['area'])}</td><td>{e(c['check'])}</td>"
                     f"<td class='{'ok' if c['result'] == 'OK' else 'bad'}'>{c['result']}</td>"
                     f"<td>{e(c['detail'])}</td></tr>")
    parts.append("</table></div><h2>Resumo</h2><div class='table-wrap'><table>")
    parts += [f"<tr><th>{e(k)}</th><td class='n'>{val}</td></tr>" for k, val in v.summary.items()]
    rb = Counter((p["scope"], p["rulebase"], p["status"]) for p in v.policies)
    parts.append("</table></div><h2>Regras por rulebase</h2><div class='table-wrap'><table>"
                 "<tr><th>Escopo</th><th>Rulebase</th><th>Status</th><th class='n'>Regras</th></tr>")
    parts += [f"<tr><td>{e(s)}</td><td><code>{e(r)}</code></td><td>{e(st)}</td><td class='n'>{n}</td></tr>"
              for (s, r, st), n in sorted(rb.items())]
    fields = Counter((c["field"], c["status"]) for c in v.policy_changes)
    parts.append("</table></div><h2>Campos de regra alterados</h2><div class='table-wrap'><table>")
    parts += [f"<tr><td><code>{e(f)}</code></td><td>{e(st)}</td><td class='n'>{n}</td></tr>"
              for (f, st), n in sorted(fields.items())]
    objs = Counter((o["plan_action"], o["status"]) for o in v.objects)
    parts.append("</table></div><h2>Objetos do original por destino</h2><div class='table-wrap'><table>")
    parts += [f"<tr><td>{e(a)}</td><td>{e(st)}</td><td class='n'>{n}</td></tr>"
              for (a, st), n in sorted(objs.items())]
    reasons = Counter(r["reason"].split(" (")[0].split(":")[0] for r in v.remaining)
    parts.append("</table></div><h2>Objetos que permanecem nos Device Groups</h2>"
                 "<div class='table-wrap'><table>")
    parts += [f"<tr><td>{e(k)}</td><td class='n'>{n}</td></tr>" for k, n in reasons.most_common()]
    parts.append("</table></div></main></body></html>\n")
    html_path = target("validation-report.html")
    html_path.write_text("".join(parts), encoding="utf-8")
    paths.append(html_path)
    return paths
