---
name: panorama-cleanup
description: Ferramenta determinística incluída (execute-a; nunca analise o XML manualmente). Use para inventariar, analisar, limpar ou consolidar objetos (address, address group, service, service group, applications, tags etc.) de um backup XML do Panorama Palo Alto, centralizar objetos em Shared, resolver conflitos de nome entre Device Groups, gerar comandos XML API/CLI e validar o XML final ou um candidate exportado.
---

<!-- Gerado por scripts/build_skill.py a partir de skill/. Não edite este arquivo: edite skill/ e rode o build. -->

# Saneamento de objetos do Panorama (panorama-cleanup 0.2.3)

Conduz a ferramenta `panorama-cleanup` sobre um backup XML do Panorama, etapa por etapa, com aprovação explícita entre etapas. Prioridade absoluta: o comportamento das políticas de cada firewall não pode mudar. O valor efetivo de cada objeto, resolvido pela herança Shared > DG ancestral > DG, deve ser idêntico antes e depois.

## Regras invioláveis

1. **Nunca alterar políticas.** Não criar, excluir, reordenar nem mudar ações, zonas, endereços, serviços, aplicações, NAT, PBF, Decryption, profiles, log settings ou UUIDs. Única exceção: atualizar referências por renomeação ou mudança de escopo, documentada e validada pela ferramenta.
2. **Nunca tocar no original.** O backup recebido não é editado. A ferramenta trabalha em cópias e se recusa a gravar em `input/`.
3. **Nunca acessar o Panorama nem fazer commit ou push.** A ferramenta não se conecta a nada. O executor `run-xmlapi.sh` só envia com `CONFIRM_EXECUTE=yes`, e quem roda é o usuário, nunca você.
4. **Uma etapa por vez.** Ao fim de cada etapa: apresente resultados, limitações e o que revisar, e **pare**. Não inicie a etapa seguinte sem aprovação explícita. Nenhum modo encadeia o próximo.
5. **UNKNOWN nunca é removido.** Objeto só é excluído com ausência de uso comprovada. Mesmo nome com valor diferente vira `NOME_DG` em Shared, nunca um objeto só.
6. **Não inventar sintaxe.** Comandos só da versão identificada no backup, conferidos contra o guia oficial dessa versão. Se faltar evidência, interrompa a operação e documente a limitação.
7. **Qualquer inconsistência = NÃO APROVADO.** Não contorne verificações nem relativize falhas de integridade.
8. **Diferencie** ação solicitada, executada e validada. Declare o que não foi verificado.
9. **Toda análise sai da ferramenta.** Não classifique objetos, não resolva herança, não conte objetos e não afirme equivalência lendo o XML por conta própria (nem com scripts improvisados). Todo número apresentado ao usuário cita o relatório da ferramenta de onde veio (arquivo e run). Se a ferramenta não rodar, pare e diga por quê.
10. **Escopo: objetos.** Não faça auditoria geral de segurança (interfaces, VPN, autenticação, ordem de regras, perfis). Se, ao ler um relatório da ferramenta, algo fora do escopo parecer grave, mencione em uma linha como "fora do escopo, verificar" e siga a etapa.
11. **Uma skill por vez.** Se houver outra skill ou instrução de Projeto para revisar objetos do Panorama (por exemplo, um kit `panorama_review.py`), não misture os fluxos: pergunte ao usuário qual usar antes de começar.

## Preparação (primeira vez na máquina)

Confira com `panorama-cleanup --version`. Se o comando não existir, peça ao usuário para instalar:

```sh
git clone https://github.com/diogodealencar-del/panorama-cleanup.git ~/panorama-cleanup
pipx install -e ~/panorama-cleanup     # sempre editável: lê config/ e docs/referencias/ do clone
```

O guia oficial da versão precisa estar em `<clone>/docs/referencias/pan-os-X.Y/ngfw-cli.md`. O guia não vai no git: é obtido em docs.paloaltonetworks.com (PAN-OS CLI Quick Start da versão, convertido para texto).

## Novo Panorama / novo backup

1. **Confidencialidade.** O backup contém a configuração completa e hashes de senha. Pergunte se o contrato permite processá-lo nesta máquina. Nunca envie o arquivo a serviços externos nem o coloque em git.
2. **Pasta de trabalho por cliente/Panorama**, fora do clone da ferramenta. Nunca rode sem `--workdir`: as saídas cairiam na pasta do clone e misturariam clientes.

```sh
W=~/panorama-trabalho/<cliente>-<panorama>
mkdir -p "$W/input"
cp <export>.xml "$W/input/<cliente>-<panorama>-<AAAAMMDD>.xml"
B="$W/input/<cliente>-<panorama>-<AAAAMMDD>.xml"
shasum -a 256 "$B"          # registrar
```

## Sessão

Cada comando roda num shell novo. Grave as definições uma vez em `$W/session.sh` (com `W` e `B` já definidos) e carregue-as no início de **todo** comando: `. "<W>/session.sh" && …`.

```sh
{ printf 'W=%q\nB=%q\nSKILL_DIR=%q\n' "$W" "$B" "${SKILL_DIR:-}"; cat <<'SESSAO'
PC() { panorama-cleanup --workdir "$W" "$@"; }
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
| 5 Conversão | `PC apply --input "$B" --plan "$(ultimo plan)"` | `integrity-checks.csv` todo OK; `policy-comparison.csv` |
| 6 Não utilizados | `PC apply --stage cleanup --keep-type tag --input "$B" --plan "$(ultimo plan)" --from-run "$(ultimo apply)"` | Integridade 14/14 OK; `unused-review.csv`; tags mantidas |
| 7 Comandos | `PC plan --stage commands --input "$B" --plan "$(ultimo plan)" --convert-run "$(ultimo apply)" --cleanup-run "$(ultimo apply-cleanup)"` | `generation-checks.csv` todo OK; se "CLI validada para PAN-OS X.Y" estiver PENDENTE, implantar só pela XML API |
| 8 Validação | `PC validate --input "$B" --modified "$(ls "$W"/output/xml/$(basename "$(ultimo apply-cleanup)")/*.final.xml)" --plan "$(ultimo plan)" --convert-run "$(ultimo apply)" --cleanup-run "$(ultimo apply-cleanup)"` | `validation-report.html` = **APROVADO** (exit 0). NÃO APROVADO: pare |
| 9 Homologação | Laboratório com `run-xmlapi.sh` (um `.tsv` por vez, na ordem 10→20→30→40); depois `PC validate --exported --input "$B" --modified <candidate-exportado>.xml --plan … --convert-run … --cleanup-run …`; ensaio de rollback | Preencher o modelo `docs/modelo-etapa-09.md` do clone (copie para `$W/`) com os runs, sha256, DGs e seriais |
| 10 Documentação | Arquivar runs, logs, testes antes/depois e decisões | — |

O XML final fica em `$W/output/xml/<run apply-cleanup>/*.final.xml`. Os comandos ficam em `$W/cli/<run plan-commands>/`, começando pelo `00-LEIA-ME.md`.

## Decisões padrão (mudar exige aprovação explícita)

- **Tags:** nenhuma é criada, movida, alterada ou excluída (`--keep-type tag`).
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
