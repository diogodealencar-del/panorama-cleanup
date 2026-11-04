# Procedimento de uso — Panorama Configuration Analyzer & Object Cleanup

Como analisar e sanear um novo backup de Panorama com esta ferramenta, do inventário à validação final.

| | |
|---|---|
| **Projeto** | `~/Claude/Projects/panorama-cleanup` |
| **Público** | Engenheiros responsáveis pela configuração do Panorama |
| **Versão do procedimento** | 1.0 — 03/11/2026 |
| **Validado com** | Panorama PAN-OS 11.1.10 |

---

## 1. O que a ferramenta faz (e o que não faz)

A ferramenta lê um backup XML do Panorama e, em etapas separadas e aprovadas uma a uma:

1. inventaria os objetos de Shared e de cada Device Group;
2. analisa dependências, uso, duplicidades e conflitos;
3. planeja a centralização em Shared, com prova de que nenhum firewall muda de comportamento;
4. gera um XML transformado (cópia) e o XML final após a remoção de objetos não utilizados;
5. gera os comandos para reproduzir as alterações no Panorama;
6. valida o resultado contra o original e classifica como **APROVADO** ou **NÃO APROVADO**.

**A ferramenta nunca:**

- altera o backup original (o sha256 é conferido antes e depois de cada etapa);
- acessa o Panorama ou os firewalls;
- executa commit ou push;
- avança sozinha para a etapa seguinte.

**Regra central (transparência):** para cada firewall, o valor efetivo de cada referência de regra (endereços, serviços, aplicações) deve ser idêntico antes e depois. Regras não são criadas, removidas, reordenadas nem alteradas, exceto pela troca do nome de um membro quando um objeto é renomeado. Tags não são tocadas.

---

## 2. Pré-requisitos

| Item | Como conferir |
|---|---|
| macOS ou Linux com Python 3.9+ (recomendado 3.12+) | `python3 --version` |
| Ambiente virtual do projeto | `ls .venv/bin/python` |
| Dependências instaladas | `.venv/bin/python scripts/check_env.py` deve terminar com **Ambiente válido** |
| Guia oficial da versão do backup | `docs/referencias/pan-os-X.Y/ngfw-cli.md` (ver 2.1) |

Primeira instalação (uma vez só):

```sh
cd ~/Claude/Projects/panorama-cleanup
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
pip install -r requirements.txt
python scripts/check_env.py
.venv/bin/pytest -q          # todos os testes devem passar
```

### 2.1 Guia de referência por versão

A geração de comandos (Etapa 7) confere a sintaxe contra o **PAN-OS CLI Quick Start** da mesma versão do backup. Para cada versão nova:

1. Baixe o PDF em [docs.paloaltonetworks.com](https://docs.paloaltonetworks.com) (PAN-OS CLI Quick Start da versão).
2. Converta para texto e salve como `docs/referencias/pan-os-X.Y/ngfw-cli.md`, onde X.Y é a versão principal do backup (por exemplo, `11.1`).
3. Registre a origem e a data em `docs/referencias/README.md`.

Sem o guia, a geração de comandos falha de propósito.

---

## 3. Preparar um novo backup

1. **Exportar do Panorama** (interface web): Panorama > Setup > Operations > **Export named Panorama configuration snapshot** (running-config). Exportar com usuário **superuser**; sem esse perfil, campos sensíveis são ocultados.
2. **Copiar para `input/`** com um nome que identifique o ambiente e a data:

```sh
cp ~/Downloads/running-config.xml input/config-panorama-dc-20261103.xml
shasum -a 256 input/config-panorama-dc-20261103.xml   # registrar
```

3. **Nunca editar** arquivos dentro de `input/`. A ferramenta se recusa a gravar ali.

> Os backups contêm a configuração completa dos firewalls. Mantenha `input/`, `output/`, `cli/` e `logs/` fora de controle de versão e de compartilhamentos (o `.gitignore` do projeto já faz isso).

---

## 4. Variáveis de apoio para a sessão

Cada execução gera um identificador de run no formato `AAAAMMDDTHHMMSSZ-<modo>-<8 primeiros hex do sha256>`. Estas variáveis encontram o run mais recente de cada etapa do backup atual:

```sh
cd ~/Claude/Projects/panorama-cleanup
source .venv/bin/activate

B=input/config-panorama-dc-20261103.xml                 # backup em análise
H=$(shasum -a 256 "$B" | cut -c1-8)                      # identifica o backup nos runs

# Diretório do run mais recente, com status ok, de um tipo de etapa deste backup.
ultimo() {
  for d in $(ls -1t output/reports | grep -E -- "-$1-$H\$"); do
    m="output/reports/$d/manifest.json"
    grep -q '"status": "ok"' "$m" 2>/dev/null || continue
    [ "$1" = apply ] && grep -q '"stage": "cleanup"' "$m" && continue
    echo "output/reports/$d"; return
  done
}
```

`ultimo inventory`, `ultimo analyze`, `ultimo plan`, `ultimo apply`, `ultimo apply-cleanup`, `ultimo plan-commands` e `ultimo validate` devolvem o diretório do run mais recente daquele tipo, ignorando runs com erro. Os tipos não se confundem: `ultimo plan` não pega um run `plan-commands`, e `ultimo apply` não pega um run de remoção. A função funciona em zsh (padrão do macOS) e bash.

Confira sempre qual run foi escolhido antes de seguir:

```sh
echo "$(ultimo plan)"
```

A ferramenta também recusa runs com erro, de outro backup ou com relatório adulterado.

---

## 5. Fluxo completo por etapa

Cada etapa termina com relatórios e **aguarda aprovação**. Não avance sem revisar o que está indicado em "Revisar antes de aprovar".

### Etapa 2 — Inventário

```sh
python -m panorama_cleanup inventory --input "$B"
```

| Saída | Onde |
|---|---|
| `inventory.csv`, `inventory.xlsx`, `inventory.html` | `$(ultimo inventory)/` |
| `device-groups.csv`, `inventory-summary.csv`, `inventory-warnings.csv` | idem |

**Revisar antes de aprovar:**

- a versão do PAN-OS;
- a hierarquia dos Device Groups (pai `UNKNOWN` é um alerta);
- a reconciliação de contagem (tudo `yes`);
- os avisos, especialmente contêineres não reconhecidos.

### Etapa 3 — Análise de dependências

```sh
python -m panorama_cleanup analyze --input "$B" --inventory "$(ultimo inventory)"
```

| Saída | Conteúdo |
|---|---|
| `object-classification.csv` | Classificação de cada objeto: USED, UNUSED ou UNKNOWN, e DUPLICATE, CONFLICT ou POSSIBLE_DUPLICATE |
| `used-objects.csv`, `unused-objects.csv`, `unknown-objects.csv` | Recortes por classificação |
| `duplicate-objects.csv`, `conflicting-objects.csv`, `possible-duplicates.csv` | Casos A, B e C |
| `context-dependent-objects.csv` | Grupos de Shared cujo valor muda conforme o DG (maior risco) |
| `dependencies.csv`, `unresolved-references.csv` | Referências resolvidas e não resolvidas |
| `analysis.xlsx`, `analysis.html` | Consolidado |

**Revisar antes de aprovar:**

- `unresolved-references.csv`: a coluna `hint` deve explicar cada caso (App-ID predefinida, usuário de diretório, IP literal). Linhas com "verificar" exigem análise manual.
- Objetos UNKNOWN e o motivo de cada um.
- Avisos de campos de regra não mapeados.

Se a análise parar com a mensagem de precedência de ancestrais ("Objects defined in ancestors will take higher precedence"), veja a seção 8.

### Etapa 4 — Plano

```sh
python -m panorama_cleanup plan --input "$B" --analysis "$(ultimo analyze)"
```

| Saída | Conteúdo |
|---|---|
| `plan.html`, `plan.xlsx` | Resumo, decisões por DG, riscos por operação |
| `plan-operations.csv` | Operações na ordem de execução (fases 1 a 4) |
| `renamed-objects.csv`, `reference-updates.csv`, `deleted-objects.csv` | Renomeações `NOME_DG`, referências a atualizar, exclusões |
| `kept-local-objects.csv` | Objetos que ficam no DG e o motivo |
| `plan-verification.csv` | Verificações de transparência por fase (precisam ter **0 diferenças**) |

**Revisar antes de aprovar:**

- `plan-verification.csv` com 0 diferenças;
- os nomes gerados;
- as descrições locais que serão perdidas nas consolidações (coluna `notes` de `deleted-objects.csv`);
- os objetos mantidos nos DGs;
- as exclusões de não utilizados (fase 4).

### Etapa 5 — Conversão para Shared (gera XML novo)

```sh
python -m panorama_cleanup apply --input "$B" --plan "$(ultimo plan)"
```

- **XML convertido:** `output/xml/<run apply>/<backup>.shared.xml`.
- **Relatórios:** `applied-changes.csv`, `policy-comparison.csv`, `integrity-checks.csv` (precisa ter todas as verificações **OK**) e `apply.html`.
- **Fases aplicadas:** só as fases 1 a 3. A ferramenta recalcula o plano e exige que seja idêntico ao aprovado.

### Etapa 6 — Remoção de não utilizados (gera XML final)

```sh
python -m panorama_cleanup apply --stage cleanup --keep-type tag \
  --input "$B" --plan "$(ultimo plan)" --from-run "$(ultimo apply)"
```

- `--keep-type tag` mantém todas as tags. É a decisão padrão deste projeto (seção 6).
- **XML final:** `output/xml/<run apply-cleanup>/<backup>.final.xml`.
- **Relatórios:**
  - `unused-review.csv`: todos os candidatos, com a prova de cada um;
  - `deleted-objects.csv` e `kept-unused-objects.csv`;
  - `integrity-checks.csv`: precisa ter 14/14 **OK**.

### Etapa 7 — Comandos para o Panorama

```sh
python -m panorama_cleanup plan --stage commands --input "$B" --plan "$(ultimo plan)" \
  --convert-run "$(ultimo apply)" --cleanup-run "$(ultimo apply-cleanup)"
```

Saída em `cli/<run plan-commands>/`:

| Arquivo | Uso |
|---|---|
| `00-LEIA-ME.md` | Ordem de execução, fontes e status de validação |
| `01-precheck.md`, `02-backup.cli` | Pré-verificação e backup |
| `10-…`, `20-…`, `30-…`, `40-….tsv` | Operações da **XML API** por fase (forma recomendada) |
| `….cli` | Mesmas operações em CLI. Usar só se `cli-syntax-check.csv` confirmar a versão ou após validação em laboratório |
| `run-xmlapi.sh` | Executor da XML API: um `.tsv` por vez, para no primeiro erro, sem commit |
| `80-testes-de-politica.cli` | Testes antes e depois para os firewalls |
| `50-validacao.md`, `60-commit-e-push.md`, `70-alternativa-load-config-partial.md` | Validação, commit e alternativa |
| `commands-index.csv` | Sintaxe, contexto, finalidade, resultado esperado, validação e risco de cada comando |

**Revisar antes de aprovar:**

- `output/reports/<run plan-commands>/generation-checks.csv`: todas as verificações **OK**;
- a linha "CLI validada para PAN-OS X.Y": se estiver **PENDENTE**, a implantação deve ser pela XML API.

### Etapa 8 — Validação final

```sh
FINAL=$(ls "$(ultimo apply-cleanup | sed 's|output/reports|output/xml|')"/*.final.xml)
python -m panorama_cleanup validate --input "$B" --modified "$FINAL" \
  --plan "$(ultimo plan)" --convert-run "$(ultimo apply)" --cleanup-run "$(ultimo apply-cleanup)"
```

- **Resultado:** `validation-report.html` com **APROVADO** ou **NÃO APROVADO**. O código de saída é 0 ou 1.
- **Detalhe:**
  - `policy-comparison.csv` e `policy-field-changes.csv`: cada regra e cada campo alterado, com a autorização;
  - `object-comparison.csv`, `reference-validation.csv` e `tag-validation.csv`;
  - `remaining-dg-objects.csv`: cada objeto que ficou no DG, com o motivo.

Qualquer inconsistência resulta em **NÃO APROVADO**. Não prossiga nesse caso.

### Etapa 9 — Homologação, implantação e rollback

Copie `docs/modelo-etapa-09.md` para a pasta de trabalho e preencha:

- os runs, os sha256 e as quantidades da seção 1;
- os DGs e seriais;
- a ordem de push gradual.

Sequência resumida:

1. **Laboratório** (Panorama da mesma versão): aplicar os `.tsv` com `run-xmlapi.sh`, rodar Validate Changes e validar o candidate exportado:

```sh
python -m panorama_cleanup validate --exported --input "$B" --modified <candidate-exportado>.xml \
  --plan "$(ultimo plan)" --convert-run "$(ultimo apply)" --cleanup-run "$(ultimo apply-cleanup)"
```

2. **Ensaio de rollback** no laboratório.
3. **Produção:**
   - pré-verificação: o plano recalculado sobre um export atual tem de ser idêntico ao aprovado;
   - linha de base dos testes de política e backup;
   - fases 1 a 4 e validação;
   - commit só no Panorama;
   - push por DG, do menor para o maior impacto, com testes de política após cada push.
4. **Pós:** `validate --exported` sobre a running config exportada.

Uso do executor da XML API:

```sh
cd cli/<run plan-commands>
./run-xmlapi.sh 10-fase1-criar-shared.tsv            # só lista (nada é enviado)
CONFIRM_EXECUTE=yes PANORAMA_HOST=<host> PANORAMA_API_KEY=<chave> \
  PANORAMA_CA_CERT=<cert.pem> ./run-xmlapi.sh 10-fase1-criar-shared.tsv
```

Repita para `20-…`, `30-…` e `40-…`, nessa ordem. **Nenhum arquivo faz commit ou push.**

### Etapa 10 — Documentação final

Arquive na pasta da mudança:

- os diretórios de todos os runs usados;
- os logs do `run-xmlapi.sh`;
- as saídas de testes antes e depois;
- o relatório da validação pós-implantação.

Registre as decisões tomadas.

---

## 6. Decisões padrão do projeto

Estas decisões foram tomadas na primeira análise e valem como padrão. Mudar qualquer uma exige aprovação explícita.

| Tema | Padrão | Como é aplicado |
|---|---|---|
| Tags | Nenhuma tag é criada, alterada, movida ou excluída, nem adicionada a objetos ou regras | Plano nunca renomeia tags; `--keep-type tag` na Etapa 6; verificação na Etapa 8 |
| Transparência | Valor efetivo idêntico por firewall | Simulação no plano, validação nas Etapas 5, 6 e 8 |
| Objetos que sobrepõem Shared usados por definições de Shared | Ficam no DG | Decisão KEEP_LOCAL com motivo |
| Descrições em consolidação | Vale a descrição do objeto mantido em Shared; a local é listada | Coluna `notes` em `deleted-objects.csv` — aceitar ou recusar no plano |
| Interface de implantação | XML API | `run-xmlapi.sh`; CLI só com validação da versão |
| Commit e push | Nunca automáticos | Apenas documentados em `60-commit-e-push.md` |

---

## 7. Trabalhando com o Claude Code

Abra o Claude Code na pasta do projeto. O `CLAUDE.md` define o protocolo de etapas e as regras de segurança. Pedidos típicos:

| Objetivo | Exemplo de pedido |
|---|---|
| Começar um backup novo | "Novo backup em `input/config-panorama-dc-20261103.xml`. Execute a Etapa 2 e aguarde aprovação." |
| Avançar | "Aprovado. Execute a Etapa 3 conforme o CLAUDE.md." |
| Registrar decisão | "Não mexa nas tags." / "Aceito a perda de descrições." |
| Conferir algo | "Por que o objeto X ficou no DG?" / "Quais regras mudam no DG-X?" |
| Validar implantação | "Valide o export `<arquivo>` com `validate --exported`." |

Mantenha uma sessão por backup e cite o nome do arquivo no primeiro pedido.

---

## 8. Códigos de saída e problemas comuns

| Código | Significado |
|---|---|
| 0 | Concluído (validação APROVADO) |
| 1 | Erro, verificação reprovada ou NÃO APROVADO (ver log e `manifest.json`) |
| 2 | Uso inválido (argumento faltando) |
| 3 | Modo não implementado (não deve ocorrer na versão atual) |

| Mensagem | Causa e ação |
|---|---|
| `Configuração <...precedence...> encontrada` | O Panorama usa "Objects defined in ancestors will take higher precedence". A regra de resolução muda e a análise para por segurança. A ferramenta precisa ser adaptada antes de continuar |
| `Esperado exatamente um /config/devices/entry` | O arquivo não é um backup de Panorama (por exemplo, é de firewall) |
| `XML recusado por construção insegura` / `XML malformado` | Arquivo corrompido ou com DTD; exportar de novo |
| `... foi gerado a partir de outro arquivo (sha256 diferente)` | O run informado é de outro backup; use `ultimo` com o `H` correto |
| `... foi alterado após a geração` | Um relatório ou XML de um run anterior foi editado; gere o run de novo |
| `O plano recalculado ... difere do plano aprovado` | O código ou o backup mudou desde a aprovação; refaça o plano e reaprove |
| `Escrita recusada: arquivo já existe` | Dois runs do mesmo modo no mesmo segundo; rode de novo |
| `guia ausente: docs/referencias/pan-os-X.Y/ngfw-cli.md` | Falta o guia da versão (seção 2.1) |
| Device Group com pai `UNKNOWN` | Hierarquia não encontrada em `readonly`; os objetos desse DG ficam como UNKNOWN e não são alterados |

O log completo de cada run está em `logs/<run_id>.log`.

---

## 9. Organização e retenção dos arquivos

| Pasta | Conteúdo | Observação |
|---|---|---|
| `input/` | Backups originais | Somente leitura para a ferramenta |
| `output/reports/<run_id>/` | Relatórios e `manifest.json` (sha256 de entrada e saídas) | Rastreabilidade de cada execução |
| `output/xml/<run_id>/` | XML convertido e final | Modelo validado; não importar diretamente no Panorama |
| `cli/<run_id>/` | Comandos gerados | Executar só com aprovação |
| `logs/` | Um log por run | |
| `docs/` | Arquitetura (`arquitetura.md`), andamento (`etapas.md`), procedimentos | Atualizar ao fim de cada backup |

Ao terminar um backup, arquive os diretórios usados num local controlado e remova de `input/` o que não for mais necessário. Os runs substituídos devem ficar identificados em `docs/etapas.md`.

---

## 10. Limitações conhecidas

- **Versões testadas:** só PAN-OS 11.1 com Device Groups filhos diretos de Shared. Hierarquias aninhadas são tratadas e testadas com dados sintéticos, mas um backup real com hierarquia aninhada deve ser revisado com atenção redobrada.
- **Firewalls multi-vsys:** a ferramenta modela herança e valor efetivo por Device Group. Em multi-vsys, cada vsys pertence a um DG, então o modelo vale por vsys, mas esse cenário não foi validado em equipamento real. O inventário avisa quando um serial aparece em mais de um DG, com o vsys de cada associação; nesse caso, faça os testes de política por vsys na homologação. Mesmo vsys em dois DGs é apontado como inconsistente.
- **CLI do Panorama:** depende do guia trazer a hierarquia de Panorama da versão. No 11.1, o guia só traz a de firewall, por isso a implantação é pela XML API.
- **Objetos de templates:** não são inventariados; templates entram só como fonte de referências.
- **Uso fora do Panorama:** configuração local dos firewalls, scripts e integrações não são visíveis no backup. As exclusões precisam ser conferidas.
- **Grupos de Shared com membro sobreposto no DG:** a regra de resolução vem da documentação oficial. É confirmada na implantação pelos testes de política antes e depois.
- **Seção `readonly`:** o XML gerado não ajusta os IDs internos, que o Panorama mantém. Por isso a implantação é feita por API, não por importação do XML.

---

## 11. Checklist rápido por backup

- [ ] Backup exportado como superuser, copiado para `input/`, sha256 registrado
- [ ] Guia da versão em `docs/referencias/pan-os-X.Y/`
- [ ] `check_env.py` válido e `pytest` passando
- [ ] Etapa 2 aprovada (versão, hierarquia, reconciliação)
- [ ] Etapa 3 aprovada (não resolvidos explicados, UNKNOWN revisados)
- [ ] Etapa 4 aprovada (0 diferenças, nomes, descrições, mantidos, exclusões)
- [ ] Etapa 5: integridade toda OK
- [ ] Etapa 6: integridade 14/14 OK, tags mantidas
- [ ] Etapa 7: verificações OK; interface definida (XML API)
- [ ] Etapa 8: **APROVADO**
- [ ] Etapa 9: laboratório, ensaio de rollback, critérios de autorização atendidos
- [ ] Etapa 10: arquivamento e registro das decisões
