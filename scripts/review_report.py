#!/usr/bin/env python3
"""Revisão Shared x Device Groups (somente leitura).

Consome os CSVs de uma execução `analyze` e produz o relatório técnico
(Markdown + XLSX + CSVs) com classificação DUPLICIDADE REAL / FUNCIONAL /
CONFLITO / SEM EQUIVALÊNCIA / NÃO UTILIZADO / INDETERMINADO.
Não lê nem altera o XML original e não gera comandos.

Uso: review_report.py --analyze-dir DIR --out-dir DIR --device-groups device-groups.csv
"""
import argparse
import csv
import json
from collections import Counter, defaultdict
from pathlib import Path

TYPES = ("address", "address-group", "service", "service-group")
FAMILY = {"address": ("address", "address-group"), "address-group": ("address", "address-group"),
          "service": ("service", "service-group"), "service-group": ("service", "service-group")}
TYPE_LABEL = {"address": "Address", "address-group": "Address Group",
              "service": "Service", "service-group": "Service Group"}


MATRIX_COLS = ["objeto_atual", "escopo_atual", "objeto_shared_destino", "device_group_afetado", "tipo", "refs_security", "refs_nat", "refs_outras", "refs_via_grupos", "total_refs", "justificativa", "riscos", "status"]
MATRIX_REFS_COLS = ["objeto", "device_group", "tipo", "origem_tipo", "origem_escopo", "rulebase_ou_grupo", "regra_ou_grupo", "campo", "resolucao", "confianca"]


def rd(p):
    with open(p, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def wr(p, rows, cols):
    with open(p, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--analyze-dir", required=True)
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--device-groups", required=True, help="device-groups.csv do inventário (hierarquia)")
    a = ap.parse_args()
    A, O = Path(a.analyze_dir), Path(a.out_dir)
    O.mkdir(parents=True, exist_ok=True)

    nonflat = set()
    if a.device_groups:
        nonflat = {r["name"] for r in rd(a.device_groups) if r["parent"] != "shared"}
    objs = [r for r in rd(A / "object-classification.csv") if r["object_type"] in TYPES]
    # tags/descrição vêm dos CSVs de duplicidade e conflito
    meta = {}
    for fn in ("duplicate-objects.csv", "conflicting-objects.csv"):
        for r in rd(A / fn):
            meta[(r["scope"], r["object_type"], r["name"])] = r
    pdup = defaultdict(list)
    for r in rd(A / "possible-duplicates.csv"):
        if r["object_type"] in TYPES:
            pdup[r["possible_duplicate_group"]].append(r)
    ctx_dep = defaultdict(set)
    for r in rd(A / "context-dependent-objects.csv"):
        ctx_dep[(r["object_type"], r["name"])].update(x.strip() for x in r["value_differs_in"].split(";") if x.strip())

    by_key = defaultdict(dict)  # (type,name) -> scope -> obj
    for o in objs:
        by_key[(o["object_type"], o["name"])][o["scope"]] = o

    # referências a objetos locais (regras Security/NAT = diretas; grupos = indiretas)
    refs = defaultdict(list)
    for d in rd(A / "dependencies.csv"):
        if d["target_type"] not in TYPES or d["target_scope"] == "shared":
            continue
        refs[(d["target_scope"], d["target_type"], d["target_name"])].append(d)
    # também referências que resolvem para o objeto Shared (para contexto)
    shared_refs = Counter()
    for d in rd(A / "dependencies.csv"):
        if d["target_type"] in TYPES and d["target_scope"] == "shared":
            shared_refs[(d["target_type"], d["target_name"])] += 1

    def ref_summary(k):
        rows = [r for r in refs.get(k, []) if r["confidence"] == "confirmed"]
        sec = sum(1 for r in rows if r["source_kind"] == "rule" and "security" in r["source_type"])
        nat = sum(1 for r in rows if r["source_kind"] == "rule" and "nat" in r["source_type"])
        other_rule = sum(1 for r in rows if r["source_kind"] == "rule" and not ("security" in r["source_type"] or "nat" in r["source_type"]))
        grp = sum(1 for r in rows if r["source_kind"] == "object")
        return sec, nat, other_rule, grp, rows

    detail, matrix, matrix_refs, conflicts, unused = [], [], [], [], []
    local_no_shared_same_name = []
    for (typ, name), scopes in sorted(by_key.items()):
        sh = scopes.get("shared")
        for scope, o in sorted(scopes.items()):
            if scope == "shared":
                continue
            k = (scope, typ, name)
            sec, nat, other, grp, rows = ref_summary(k)
            risks, info = [], []
            m = meta.get(k, {})
            cls, just, prop, status = "", "", "", "Pendente"
            eq_sh = None
            if not sh:
                fam_peers = [x for t2 in FAMILY[typ] for x in [by_key.get((t2, name), {}).get("shared")] if x and t2 != typ]
                if fam_peers:
                    cls = "CONFLITO"
                    just = "Mesmo nome de objeto de outro tipo em Shared (%s); namespace compartilhado." % fam_peers[0]["object_type"]
                    prop = "Não consolidar. Registrar."
                    status = "Não recomendado"
            local_fam = [t2 for t2 in FAMILY[typ] if t2 != typ and scope in by_key.get((t2, name), {})]
            if not cls and local_fam:
                cls = "CONFLITO"
                just = "Mesmo nome de objeto de outro tipo no mesmo escopo (%s); namespace compartilhado." % local_fam[0]
                prop = "Não consolidar. Registrar."
                status = "Não recomendado"
            if cls:
                pass
            elif sh:
                eq_sh = (sh["value_cluster"] == o["value_cluster"] and sh["name_group"] == o["name_group"])
                if eq_sh:
                    cls = "DUPLICIDADE REAL"
                    just = "Mesmo tipo e nome; definição equivalente à do Shared."
                    prop = "Avaliar uso do objeto Shared como referência única (remover a cópia local após validar herança e dependências)."
                    if m and m.get("tags_equal") == "no":
                        risks.append("tags diferentes entre local e Shared")
                    if m and m.get("description_equal") == "no":
                        info.append("descrição diferente")
                else:
                    cls = "CONFLITO"
                    just = "Mesmo tipo e nome; definição diferente do Shared (%s x %s)." % (sh["value"][:80], o["value"][:80])
                    prop = "Não consolidar. Registrar. Referências locais permanecem."
                    status = "Não recomendado"
            if not cls:
                grp_id = o.get("possible_duplicate_group", "")
                peers = [p for p in pdup.get(grp_id, []) if p["scope"] == "shared"] if grp_id else []
                if peers:
                    cls = "DUPLICIDADE FUNCIONAL"
                    just = "Nome diferente; definição equivalente a Shared: " + ", ".join(sorted({p['name'] for p in peers})[:3])
                    prop = "CANDIDATO À CONSOLIDAÇÃO: exige troca de nome nas referências; sem substituição automática."
                else:
                    cls = "SEM EQUIVALÊNCIA"
                    just = "Nenhum objeto Shared de mesmo nome ou valor."
                    prop = "Nenhuma ação."
                    local_no_shared_same_name.append(o)
            if not a.device_groups and cls == "DUPLICIDADE REAL":
                risks.append("hierarquia de DGs não verificada (--device-groups ausente)")
            if cls == "DUPLICIDADE REAL" and scope in nonflat:
                cls = "INDETERMINADO"
                just += " DG com ancestral intermediário: override não verificado."
                prop = "Não alterar até verificar ancestrais."
            if o["usage"] == "UNKNOWN" or scope in ctx_dep.get((typ, name), ()):
                if cls == "DUPLICIDADE REAL":
                    risks.append("objeto dependente de contexto/uso desconhecido")
                    cls_out = "INDETERMINADO"
                    just += " Uso/contexto não conclusivo."
                    prop = "Não alterar até esclarecer."
                    cls = cls_out
            if cls == "DUPLICIDADE REAL" and other:
                risks.append("referenciado por rulebase fora de Security/NAT (%d)" % other)
            if cls == "DUPLICIDADE REAL" and grp:
                risks.append("membro de grupos (%d referências indiretas)" % grp)
            row = {
                "tipo": TYPE_LABEL[typ], "objeto_shared": sh["name"] if sh else "",
                "device_group": scope, "objeto_local": name,
                "valor_shared": sh["value"] if sh else "", "valor_local": o["value"],
                "classificacao": cls, "uso_local": o["usage"],
                "refs_security": sec, "refs_nat": nat, "refs_outras_rulebases": other, "refs_via_grupos": grp,
                "justificativa": just, "proposta": prop,
                "impacto": ("Troca de resolução de nome: de local para Shared (valor idêntico)" if cls == "DUPLICIDADE REAL" else "Nenhum (sem alteração proposta)"),
                "riscos": "; ".join(risks), "informativo": "; ".join(info), "status": status,
            }
            detail.append(row)
            if cls == "CONFLITO":
                conflicts.append(row)
            if o["usage"] == "UNUSED":
                unused.append({"escopo": scope, "tipo": TYPE_LABEL[typ], "nome": name, "valor": o["value"], "classificacao": cls, "motivo": o["usage_reason"]})
            if cls == "DUPLICIDADE REAL":
                matrix.append({"objeto_atual": name, "escopo_atual": scope, "objeto_shared_destino": sh["name"],
                               "device_group_afetado": scope, "tipo": TYPE_LABEL[typ],
                               "refs_security": sec, "refs_nat": nat, "refs_outras": other, "refs_via_grupos": grp,
                               "total_refs": sec + nat + other + grp,
                               "justificativa": "Definição equivalente em Shared",
                               "riscos": "; ".join(risks), "status": "Pendente"})
                for r in rows:
                    matrix_refs.append({"objeto": name, "device_group": scope, "tipo": TYPE_LABEL[typ],
                                        "origem_tipo": r["source_kind"], "origem_escopo": r["source_scope"],
                                        "rulebase_ou_grupo": r["source_type"], "regra_ou_grupo": r["source_name"],
                                        "campo": r["field"], "resolucao": r["resolution"], "confianca": r["confidence"]})
    # Shared sem uso
    for o in objs:
        if o["scope"] == "shared" and o["usage"] == "UNUSED":
            unused.append({"escopo": "shared", "tipo": TYPE_LABEL[o["object_type"]], "nome": o["name"], "valor": o["value"],
                           "classificacao": "", "motivo": o["usage_reason"]})

    # DG x DG sem Shared (fora do foco do brief)
    dgdg = set()
    for (typ, name), scopes in by_key.items():
        if "shared" not in scopes and len(scopes) > 1:
            dgdg.add((typ, name))

    cols = ["tipo", "objeto_shared", "device_group", "objeto_local", "valor_shared", "valor_local", "classificacao",
            "uso_local", "refs_security", "refs_nat", "refs_outras_rulebases", "refs_via_grupos", "justificativa",
            "proposta", "impacto", "riscos", "informativo", "status"]
    wr(O / "detalhado.csv", detail, cols)
    wr(O / "matriz-substituicao.csv", matrix, MATRIX_COLS)
    wr(O / "matriz-substituicao-referencias.csv", matrix_refs, MATRIX_REFS_COLS)
    wr(O / "conflitos-nao-modificar.csv", conflicts, cols)
    wr(O / "objetos-nao-utilizados.csv", unused, ["escopo", "tipo", "nome", "valor", "classificacao", "motivo"])

    cnt = Counter(r["classificacao"] for r in detail)
    cnt_type = Counter((r["tipo"], r["classificacao"]) for r in detail)
    tot_type = Counter(TYPE_LABEL[o["object_type"]] for o in objs)
    per_dg = Counter((r["device_group"], r["classificacao"]) for r in detail)
    refs_real = sum(m["total_refs"] for m in matrix)
    refs_real_rules = sum(m["refs_security"] + m["refs_nat"] for m in matrix)
    risky = [m for m in matrix if m["riscos"]]
    unused_t = Counter(u["tipo"] for u in unused)

    summary = {
        "objetos_analisados": len(objs), "por_tipo": dict(tot_type),
        "classificacao_locais_vs_shared": dict(cnt),
        "refs_potencialmente_modificadas_total": refs_real,
        "refs_potencialmente_modificadas_security_nat": refs_real_rules,
        "nao_utilizados": len(unused), "pares_dg_x_dg_sem_shared": len(dgdg),
    }
    (O / "resumo.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    L = []
    L.append("# Revisão de objetos Shared x Device Groups\n")
    L.append("Modo: somente leitura. Nenhuma alteração aplicada ou aprovada. Origem: `%s`.\n" % A.name)
    L.append("Device Groups com objetos locais nos 4 tipos: %s. Hierarquia: ver device-groups.csv do inventário.\n" % ", ".join(sorted({r["device_group"] for r in detail})))
    L.append("## 1. Resumo executivo\n")
    L.append("Objetos analisados (Address, Address Group, Service, Service Group): **%d**\n" % len(objs))
    L.append("| Tipo | Total |\n|---|---|")
    for t in TYPE_LABEL.values():
        L.append("| %s | %d |" % (t, tot_type[t]))
    L.append("\nOs 4 tipos acima, em Shared e nos DGs. As classificações abaixo se referem aos objetos **locais de DG** comparados a Shared.\n")
    L.append("| Classificação | Qtde |\n|---|---|")
    for c in ("DUPLICIDADE REAL", "DUPLICIDADE FUNCIONAL", "CONFLITO", "SEM EQUIVALÊNCIA", "INDETERMINADO"):
        L.append("| %s | %d |" % (c, cnt[c]))
    L.append("| CANDIDATO À CONSOLIDAÇÃO (= duplicidade funcional) | %d |" % cnt["DUPLICIDADE FUNCIONAL"])
    L.append("| NÃO UTILIZADO (Shared + DGs, 4 tipos) | %d |" % len(unused))
    L.append("\nReferências potencialmente afetadas pelas duplicidades reais: **%d** (Security+NAT: %d; as demais são via grupos ou outras rulebases)." % (refs_real, refs_real_rules))
    L.append("Duplicidades reais com algum risco registrado: %d de %d.\n" % (len(risky), len(matrix)))
    L.append("### Por tipo e classificação\n")
    cl = ["DUPLICIDADE REAL", "DUPLICIDADE FUNCIONAL", "CONFLITO", "SEM EQUIVALÊNCIA", "INDETERMINADO"]
    L.append("| Tipo | " + " | ".join(cl) + " |\n|---|" + "---|" * len(cl))
    for t in TYPE_LABEL.values():
        L.append("| %s | " % t + " | ".join(str(cnt_type[(t, c)]) for c in cl) + " |")
    L.append("\n### Por Device Group\n")
    dgs = sorted({r["device_group"] for r in detail})
    L.append("| DG | " + " | ".join(cl) + " |\n|---|" + "---|" * len(cl))
    for d in dgs:
        L.append("| %s | " % d + " | ".join(str(per_dg[(d, c)]) for c in cl) + " |")
    L.append("\n## 2. Observações metodológicas e limitações\n")
    L.append("- **Substituir referência = mudar a resolução do nome.** Em duplicidade real o nome é idêntico; remover a cópia local faz a regra resolver para Shared com o mesmo valor. O texto das regras não muda. Mesmo assim, nada é aprovado automaticamente.")
    L.append("- **Fora do foco do brief:** %d pares de nomes repetidos apenas entre DGs, sem objeto em Shared (cobertos pelo modo `plan` da ferramenta, não por este relatório)." % len(dgdg))
    L.append("- Referências a objetos locais contadas a partir de `dependencies.csv` (regras Security/NAT, grupos e outras rulebases, separadas nas colunas).")
    warns = [w["warning"] for w in rd(A / "analysis-warnings.csv")]
    unres = sum(int(u["occurrences"] or 0) for u in rd(A / "unresolved-references.csv"))
    L.append("- Avisos do `analyze` (%d): %s. Referências não resolvidas: %d ocorrências (ver `unresolved-references.csv`); a causa não foi auditada item a item." % (len(warns), " | ".join(warns), unres))
    L.append("- Não verificada a equivalência semântica por firewall (usar `plan`/`validate` da ferramenta antes de qualquer execução). Não foi avaliada a precedência de override para regras Shared lidas em cada DG além do que `dependencies.csv` registra.")
    L.append("- Este relatório não é homologação operacional.\n")
    L.append("## 3. Detalhe\n")
    L.append("Todas as %d ocorrências em `detalhado.csv` / `revisao.xlsx`." % len(detail))
    L.append("\n## 4. Matriz de alterações propostas (duplicidades reais)\n")
    L.append("%d objetos locais; resumo em `matriz-substituicao.csv`, uma linha por referência em `matriz-substituicao-referencias.csv` (%d linhas). Status: **Pendente** em todos.\n" % (len(matrix), len(matrix_refs)))
    top = sorted(matrix, key=lambda m: -m["total_refs"])[:15]
    L.append("| Objeto | DG | Tipo | Sec | NAT | Outras | Grupos | Riscos |\n|---|---|---|---|---|---|---|---|")
    for m in top:
        L.append("| %s | %s | %s | %d | %d | %d | %d | %s |" % (m["objeto_atual"], m["escopo_atual"], m["tipo"], m["refs_security"], m["refs_nat"], m["refs_outras"], m["refs_via_grupos"], m["riscos"] or "-"))
    L.append("\n(15 maiores por quantidade de referências.)\n")
    L.append("## 5. Conflitos que não devem ser modificados\n")
    L.append("%d objetos locais em `conflitos-nao-modificar.csv`. Mesmo nome e valor diferente do Shared; nenhuma consolidação.\n" % len(conflicts))
    L.append("## 6. Objetos sem utilização identificada\n")
    L.append("%d objetos (%s) em `objetos-nao-utilizados.csv`. Ausência de referência no escopo analisado; exclusão exige verificação adicional (membros de grupos, dependências e regra de transparência) e aprovação.\n" % (len(unused), ", ".join("%s: %d" % kv for kv in sorted(unused_t.items()))))
    (O / "relatorio.md").write_text("\n".join(L), encoding="utf-8")

    try:
        from openpyxl import Workbook
        wb = Workbook()
        ws = wb.active
        ws.title = "Resumo"
        ws.append(["Indicador", "Valor"])
        for k, v in summary.items():
            ws.append([k, json.dumps(v, ensure_ascii=False) if isinstance(v, dict) else v])
        for title, rows, cs in (("Detalhado", detail, cols),
                                ("Matriz", matrix, MATRIX_COLS),
                                ("Matriz-refs", matrix_refs, MATRIX_REFS_COLS),
                                ("Conflitos", conflicts, cols),
                                ("Nao-utilizados", unused, ["escopo", "tipo", "nome", "valor", "classificacao", "motivo"])):
            w = wb.create_sheet(title)
            w.append(cs)
            for r in rows:
                w.append([r.get(c, "") for c in cs])
                for cell in w[w.max_row]:
                    if isinstance(cell.value, str):
                        cell.data_type = "s"
        wb.save(O / "revisao.xlsx")
    except ImportError:
        print("openpyxl ausente: XLSX não gerado")
    print(json.dumps(summary, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
