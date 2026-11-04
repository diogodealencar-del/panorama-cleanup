## Sessão

Cada comando roda num shell novo. Grave as definições uma vez em `$W/session.sh` (com {{SESSION_VARS}} já definidos) e carregue-as no início de **todo** comando: `. "<W>/session.sh" && …`.

```sh
{ printf 'W=%q\nB=%q\nSKILL_DIR=%q\n' "$W" "$B" "${SKILL_DIR:-}"; cat <<'SESSAO'
PC() { {{CMD}} --workdir "$W" "$@"; }
H=$( (shasum -a 256 "$B" 2>/dev/null || sha256sum "$B") | cut -c1-8)
ultimo() {
  for d in $(ls -1t "$W/output/reports" | grep -E -- "-$1-$H\$"); do
    m="$W/output/reports/$d/manifest.json"
    grep -q '"status": "ok"' "$m" 2>/dev/null || continue
    [ "$1" = apply ] && grep -q '"stage": "cleanup"' "$m" && continue
    echo "$W/output/reports/$d"; return
  done
}
SESSAO
} > "$W/session.sh"
```

`ultimo <tipo>` devolve o run mais recente, com status ok, daquele tipo (`inventory`, `analyze`, `plan`, `apply`, `apply-cleanup`, `plan-commands`, `validate`) deste backup. Mostre ao usuário qual run foi escolhido antes de usá-lo.

## Etapas

| Etapa | Comando | Revisar antes de aprovar |
|---|---|---|
| 2 Inventário | `PC inventory --input "$B"` | Versão do PAN-OS; hierarquia de DGs (pai `UNKNOWN` = alerta); reconciliação toda `yes`; contêineres não reconhecidos |
| 3 Análise | `PC analyze --input "$B" --inventory "$(ultimo inventory)"` | `unresolved-references.csv` (todo "verificar" exige análise manual); UNKNOWN e motivos; `context-dependent-objects.csv` |
| 4 Plano | `PC plan --input "$B" --analysis "$(ultimo analyze)"` | `plan-verification.csv` com **0 diferenças**; nomes `NOME_DG`; descrições perdidas (`notes` em `deleted-objects.csv`); `kept-local-objects.csv`; exclusões da fase 4 |
| 5 Conversão | `PC apply --input "$B" --plan "$(ultimo plan)"` | `integrity-checks.csv` todo OK (inclui "todas as atualizações de referência do plano aplicadas" e "grupos remanescentes"); leia `reference-updates.csv` inteiro: nenhuma linha "NÃO APLICADO"; `policy-comparison.csv` |
| 6 Não utilizados | `PC apply --stage cleanup --keep-type tag --input "$B" --plan "$(ultimo plan)" --from-run "$(ultimo apply)"` | `integrity-checks.csv` todo OK; `unused-review.csv`; tags mantidas |
| 7 Comandos | `PC plan --stage commands --input "$B" --plan "$(ultimo plan)" --convert-run "$(ultimo apply)" --cleanup-run "$(ultimo apply-cleanup)"` | `generation-checks.csv` todo OK; se "CLI validada para PAN-OS X.Y" estiver PENDENTE, implantar só pela XML API |
| 8 Validação | `PC validate --input "$B" --modified "$(ls "$W"/output/xml/$(basename "$(ultimo apply-cleanup)")/*.final.xml)" --plan "$(ultimo plan)" --convert-run "$(ultimo apply)" --cleanup-run "$(ultimo apply-cleanup)"` | `validation-report.html` = **APROVADO** (exit 0). NÃO APROVADO: pare |
| 9 Homologação | Laboratório com `run-xmlapi.sh` (um `.tsv` por vez, na ordem 10→20→30→40); depois `PC validate --exported --input "$B" --modified <candidate-exportado>.xml --plan … --convert-run … --cleanup-run …`; ensaio de rollback | Preencher o modelo {{MODELO_ETAPA9}} com os runs, sha256, DGs e seriais |
| 10 Documentação | Arquivar runs, logs, testes antes/depois e decisões | — |

O XML final fica em `$W/output/xml/<run apply-cleanup>/*.final.xml`. Os comandos ficam em `$W/cli/<run plan-commands>/`, começando pelo `00-LEIA-ME.md`.

## Decisões padrão (mudar exige aprovação explícita)

- **Tags:** nenhuma é criada, movida, alterada ou excluída. O plano mantém toda tag de DG no próprio DG, e objetos que dependem dela também ficam (motivo em `kept-local-objects.csv`); na Etapa 6, `--keep-type tag`. Não há opção para mover tags: a Etapa 8 reprova qualquer tag fora do escopo original.
- **Objetos de DG que sobrepõem Shared e são usados por definições de Shared:** ficam no DG (KEEP_LOCAL).
- **Descrições em consolidação:** vale a do objeto mantido em Shared; as perdidas são listadas para aceite.
- **Implantação:** XML API. A CLI só entra se a sintaxe estiver confirmada para a versão.
- **Produção:** janela aprovada, commit só no Panorama, push por DG do menor para o maior impacto, testes de política (`80-testes-de-politica.cli`) após cada push.

## Cuidados

- **Versões:** testado com PAN-OS 11.1. Para outra versão, avise o usuário logo na Etapa 2. A Etapa 7 só roda com o guia oficial da versão; sem ele, para de propósito.
- **Multi-vsys:** se o inventário avisar "Firewall … em N Device Groups (multi-vsys)", destaque o aviso ao usuário com o vsys de cada DG. A ferramenta modela por DG (cada vsys pertence a um DG), mas esse cenário não foi validado em equipamento real: na homologação, os testes de política antes e depois são feitos **por vsys** em cada firewall afetado. Aviso de "vsys repetido" indica associação inconsistente: pare e peça confirmação no Panorama.
- **Precedência:** se a análise parar por "Objects defined in ancestors will take higher precedence", não contorne. Explique ao usuário e pare.
- **Fora do backup:** configuração local dos firewalls, scripts e integrações não aparecem no backup. Exclusões precisam ser confirmadas com o usuário.
- **Não importe o XML final inteiro** no Panorama: a seção `readonly` não é ajustada. Implante pela XML API.
- Antes de afirmar algo sobre o comportamento do Panorama, consulte docs.paloaltonetworks.com da versão do backup.
