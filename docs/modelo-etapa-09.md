# Etapa 9 — Procedimento operacional de homologação, implantação e rollback (MODELO)

> **Modelo.** Copie para a pasta de trabalho do cliente (`$W/docs/etapa-09.md`) e preencha todos os
> campos `<…>` com os runs, sha256, quantidades, DGs e seriais da execução. Remova as linhas que não
> se aplicam. Os comandos citam PAN-OS 11.1, a versão validada; para outra versão, confirme cada
> passo na documentação oficial dela.

Panorama PAN-OS <versão> (detail-version <detail-version>) — limpeza e centralização de objetos em Shared.

> **Nenhuma operação deste documento foi executada.** Toda execução em laboratório ou produção
> depende de aprovação explícita e de janela de mudança. Nenhum passo faz commit ou push
> automaticamente.

## 1. Artefatos aprovados

| Item | Referência | sha256 |
|---|---|---|
| Backup original | `$W/input/<backup>.xml` | `<sha256>` |
| Plano aprovado | `$W/output/reports/<run plan>/` | ver `manifest.json` |
| XML convertido (fases 1-3) | `$W/output/xml/<run apply>/<backup>.shared.xml` | `<sha256>` |
| XML final (fases 1-4, tags preservadas) | `$W/output/xml/<run apply-cleanup>/<backup>.final.xml` | `<sha256>` |
| Validação (Etapa 8) | `$W/output/reports/<run validate>/` | resultado **APROVADO** (<n>/<n>) |
| Comandos | `$W/cli/<run plan-commands>/` | `$W/output/reports/<run plan-commands>/manifest.json` |

Runs substituídos, que não devem ser usados: `<listar>`.

**Volume de operações (XML API):**

| Fase | Arquivo | Operações |
|---|---|---|
| 1 | `10-fase1-criar-shared.tsv` | <N1> criações em Shared |
| 2 | `20-fase2-atualizar-referencias.tsv` | <N2> edições (<n> referências em <n> regras e <n> grupos locais) |
| 3 | `30-fase3-excluir-copias-locais.tsv` | <N3> exclusões de cópias locais |
| 4 | `40-fase4-excluir-nao-utilizados.tsv` | <N4> exclusões de não utilizados (sem tags) |

Device Groups e firewalls (ordenar do menor para o maior impacto; é a ordem do push gradual):

| DG | Firewalls | Objetos locais (antes → depois) | Renomeações |
|---|---|---|---|
| <DG-1> | <seriais> | <n> → <n> | <n> |
| <DG-2> | <seriais> | <n> → <n> | <n> |

## 2. Interface de implantação

**Usar a Panorama XML API** (`run-xmlapi.sh` com os arquivos `.tsv`):

- As ações `set`, `edit` e `delete` (`type=config`) estão documentadas na API.
- Os XPaths e elementos saem literalmente do backup PAN-OS 11.1.
- A sequência foi provada por simulação: reproduz exatamente o XML final validado.

**A CLI do Panorama não está validada para 11.1.** O guia fornecido (PAN-OS CLI Quick Start 11.1 & later) só traz a hierarquia de Panorama (`device-group`) para 12.1 e 12.2. Os arquivos `.cli` só podem ser usados depois do passo A6 (seção 4).

Os testes de política (`80-testes-de-politica.cli`) usam a CLI **do firewall**, modo operacional. Os <n> comandos conferem com a hierarquia operacional PAN-OS 11.1 do guia.

## 3. Papéis e pré-requisitos

| Papel | Responsabilidade |
|---|---|
| Executor | Roda os passos e registra a saída de cada um |
| Revisor | Confere cada resultado esperado antes do próximo passo e autoriza a continuidade |
| Aprovador da mudança | Autoriza a janela e decide sobre rollback |

- **Acesso:** conta de administrador com permissão de configuração e commit no Panorama, e chave de API gerada para essa conta. A chave só é usada no cabeçalho `X-PAN-KEY`.
- **Estação:** a ferramenta `panorama-cleanup` instalada e acesso HTTPS ao Panorama. Para validar o certificado, use `PANORAMA_CA_CERT=<pem>`.
- **Backup:** servidor SCP para os backups.
- **Controle de concorrência:** nenhum outro administrador pode alterar o Panorama durante a janela. Se necessário, faça um bloqueio de configuração combinado com a equipe.

## 4. Fase A — Homologação em laboratório (obrigatória)

Ambiente: Panorama de laboratório com PAN-OS **<detail-version>**, a mesma detail-version do backup. Se possível, deve gerenciar um firewall de laboratório.

| # | Ação | Comando / caminho | Resultado esperado |
|---|---|---|---|
| A1 | Carregar o backup original no laboratório | Interface web: Panorama > Setup > Operations > **Import named Panorama configuration snapshot** (arquivo `<backup>.xml`), depois **Load named Panorama configuration snapshot**, depois **Commit to Panorama** | Commit concluído sem erro; candidate = running |
| A2 | Linha de base de conferência | Rodar `01-precheck.md` contra o laboratório | Plano recalculado idêntico ao aprovado |
| A3 | Aplicar as fases 1 a 4 | `CONFIRM_EXECUTE=yes PANORAMA_HOST=<lab> PANORAMA_API_KEY=<chave> ./run-xmlapi.sh 10-fase1-criar-shared.tsv` e o mesmo para `20-…`, `30-…`, `40-…`, na ordem | `Concluído: N de N operações` em cada arquivo, sem `FALHA`. Log `.log` gerado ao lado de cada `.tsv` |
| A4 | Validar no equipamento | Commit > **Preview Changes**, **Change Summary**, **Validate Changes**. XML API: `type=commit&action=all&cmd=<commit-all><shared-policy><validate-only></validate-only></shared-policy></commit-all>` | Nenhum erro de validação; o Preview mostra apenas objetos e as <n> regras com membros trocados |
| A5 | Validar com a ferramenta | Save named Panorama configuration snapshot e Export named Panorama configuration snapshot (candidate). Depois, na estação: `panorama-cleanup --workdir "$W" validate --exported --input $W/input/<backup>.xml --modified <exportado>.xml --plan $W/output/reports/<run plan> --convert-run $W/output/reports/<run apply> --cleanup-run $W/output/reports/<run apply-cleanup>` | **APROVADO** (`--exported` ignora apenas `/config/readonly`, cujos IDs o Panorama mantém) |
| A6 | (Só se a CLI for usada) Validar a sintaxe CLI 11.1 | No laboratório, com o XML final carregado: `set cli config-output-format set` (operacional), `configure`, `show shared address`, `show device-group <DG-n> address-group` etc. | Linhas exibidas iguais às dos `.cli` |
| A7 | Commit no laboratório | **Commit to Panorama** | Commit concluído; `readonly` atualizado pelo próprio Panorama |
| A8 | Push de teste (se houver firewall de laboratório) | Push to Devices para o DG de laboratório. No firewall: `show config pushed-shared-policy` e os comandos de `80-testes-de-politica.cli`, comparando com a linha de base tirada antes do push | Saídas idênticas |
| A9 | **Ensaio de rollback** | Executar R2 (seção 6) e, se possível, R3 | Configuração volta ao original: `plan` sobre o export idêntico ao aprovado |

A homologação só é aprovada com A3, A4, A5, A7 e A9 concluídos sem divergência (ver critérios na seção 8).

## 5. Fase B — Implantação em produção (janela aprovada)

### B0. Pré-verificação (sem alteração)

| # | Ação | Comando / caminho | Resultado esperado |
|---|---|---|---|
| B0.1 | Sem alterações pendentes | Commit > **Preview Changes** | Nada pendente |
| B0.2 | Estado igual ao do plano | `01-precheck.md` (exportar, rodar `inventory`/`analyze`/`plan` e comparar `operations` com o plano aprovado) | Idêntico. Se diferente: **parar** e regenerar o plano |
| B0.3 | Linha de base dos testes | Em cada firewall: `set cli scripting-mode on` e os comandos do bloco do seu DG em `80-testes-de-politica.cli`. Salvar a saída como `baseline-<serial>.txt` | Uma saída por comando, guardada |
| B0.4 | Sincronismo | Panorama > Managed Devices: todos os firewalls conectados e "In Sync" | Sem pendências de push anteriores |

### B1. Backup

| # | Ação | Comando / caminho | Resultado esperado |
|---|---|---|---|
| B1.1 | Snapshot nomeado | Interface web: Panorama > Setup > Operations > **Save named Panorama configuration snapshot** `pre-limpeza-shared`. Equivalente CLI em `02-backup.cli` (`save config to pre-limpeza-shared`) | Snapshot salvo |
| B1.2 | Exportar | **Export named Panorama configuration snapshot** (running), ou `scp export configuration from pre-limpeza-shared.xml to <usuario>@<host>:<caminho>` | Arquivo no servidor; registrar o sha256 |
| B1.3 | Bundle Panorama + firewalls | Panorama > Setup > Operations > **Export Panorama and devices config bundle** | Bundle salvo (rollback de firewall por configuração) |

### B2. Alterações no candidate config (sem commit)

| # | Ação | Comando | Resultado esperado |
|---|---|---|---|
| B2.1 | Fase 1 | `CONFIRM_EXECUTE=yes PANORAMA_HOST=<prod> PANORAMA_API_KEY=<chave> PANORAMA_CA_CERT=<pem> ./run-xmlapi.sh 10-fase1-criar-shared.tsv` | `Concluído: <N1> de <N1>` |
| B2.2 | Fase 2 | `./run-xmlapi.sh 20-fase2-atualizar-referencias.tsv` (mesmas variáveis) | `Concluído: <N2> de <N2>` |
| B2.3 | Fase 3 | `./run-xmlapi.sh 30-fase3-excluir-copias-locais.tsv` | `Concluído: <N3> de <N3>` |
| B2.4 | Fase 4 (opcional nesta janela) | `./run-xmlapi.sh 40-fase4-excluir-nao-utilizados.tsv` | `Concluído: <N4> de <N4>` |

Qualquer `FALHA` interrompe o script. Não reexecute o arquivo nem pule linhas: vá para R1 (seção 6).

Se a fase 4 ficar para outra janela, a validação B3.3 deve usar o run da Etapa 5 (XML convertido). Peça a geração desse caso antes da janela.

### B3. Validação antes do commit

| # | Ação | Comando / caminho | Resultado esperado |
|---|---|---|---|
| B3.1 | Prévia | Commit > **Preview Changes** e **Change Summary** | Só objetos e as <n> regras com troca de membro; nenhuma regra criada, removida ou movida |
| B3.2 | Validação do Panorama | **Validate Changes** e **Validate Device Group Push** (<todos os DGs afetados>). API: `commit-all` `validate-only` (ver `50-validacao.md`) | Sem erros |
| B3.3 | Validação da ferramenta | Exportar o candidate (Save + Export named snapshot) e rodar `validate --exported` como em A5 | **APROVADO** |
| B3.4 | **Go / No-go** | Revisor e aprovador conferem B3.1 a B3.3 | Go registrado. No-go: R1 |

### B4. Commit e push gradual

| # | Ação | Comando / caminho | Resultado esperado |
|---|---|---|---|
| B4.1 | Commit só no Panorama | **Commit to Panorama** (descrição com a referência da mudança). API: `type=commit&cmd=<commit></commit>`, acompanhando com `type=op&cmd=<show><jobs><id>JOB</id></jobs></show>` | Job `FIN` / `OK` |
| B4.2 | Push piloto: **<DG-1>** | Push to Devices só do <DG-1>. API: `<commit-all><shared-policy><device-group><entry name="<DG-1>"/></device-group></shared-policy></commit-all>` | Push concluído nos firewalls do DG |
| B4.3 | Verificar <DG-1> | Em cada firewall: `show config pushed-shared-policy` e os testes do bloco <DG-1> em `80-…`, comparando com `baseline-<serial>.txt`. Monitorar o tráfego negado inesperado nos logs | Saídas **idênticas**; nenhum aumento de negação |
| B4.4 | Observação | Janela de observação combinada (sugestão: 30 a 60 min) | Sem incidente |
| B4.5 | **<DG-2>** | Push só do <DG-2> e verificação como B4.3 e B4.4 | Idêntico |
| B4.6 | **<DG-3>** | Push só do <DG-3> e verificação como B4.3 e B4.4 | Idêntico |
| B4.7 | **<DG-n>** | Push só do <DG-n> e verificação como B4.3 e B4.4. <observações do DG de maior impacto> | Idêntico |

A ordem vai do menor para o maior impacto: <justificar a ordem; ex.: o DG piloto só recebe alterações de Shared>. Se os dois firewalls de um DG forem um par HA, faça o push do DG inteiro, como na tabela. Caso contrário, é possível começar por um firewall do DG (`commit-all` com `<devices>`, documentado na API).

### B5. Pós-implantação

1. Exportar a running config do Panorama e rodar `validate --exported` sobre ela. Resultado esperado: **APROVADO**.
2. Guardar os logs do `run-xmlapi.sh`, as saídas dos testes antes e depois e o relatório da validação na pasta da mudança.
3. Seguir para a Etapa 10 (documentação final).

## 6. Rollback

| Nível | Quando | Procedimento | Resultado esperado |
|---|---|---|---|
| **R1** — antes do commit | Falha no `run-xmlapi.sh`, No-go em B3, ou validação reprovada | Interface web: **Config > Revert Changes** (Revert All Changes) ou Panorama > Setup > Operations > **Revert to running Panorama configuration**. CLI: `revert config` (consta nas hierarquias do guia; validar a forma em Panorama 11.1 no passo A9) | Preview Changes vazio; candidate = running = original |
| **R2** — após o commit, antes de qualquer push | Problema detectado após B4.1 | Panorama > Setup > Operations > **Load named Panorama configuration snapshot** `pre-limpeza-shared`, depois **Commit to Panorama**. Não use opções de regeneração de UUID (`regenerate-rule-uuid-all`) | Running = backup; `plan` sobre o export igual ao aprovado |
| **R3** — após push de um ou mais DGs | Teste B4.3 divergente, aumento de negação ou incidente | R2 e depois **Push to Devices** dos DGs já enviados, na ordem inversa | Testes de política idênticos à linha de base |
| **R4** — Panorama indisponível | Panorama fora do ar durante a janela | Não altere os firewalls localmente. Aguarde o Panorama ou acione o suporte. O bundle B1.3 guarda a configuração dos firewalls | Firewalls mantêm a última configuração enviada |

**Gatilhos de rollback:**
- qualquer `FALHA` em `run-xmlapi.sh`;
- erro em Validate Changes ou Validate Device Group Push;
- `validate --exported` NÃO APROVADO;
- qualquer diferença entre a saída de um teste de política e a linha de base;
- aumento de tráfego negado;
- incidente reportado nos serviços dos DGs afetados;
- decisão do aprovador.

Fontes da interface web, PAN-OS 11.1 e Panorama:
- [Save and Export Panorama and Firewall Configurations](https://docs.paloaltonetworks.com/panorama/administration/administer-panorama/manage-panorama-and-firewall-configuration-backups/save-and-export-panorama-and-firewall-configurations)
- [Revert Panorama Configuration Changes](https://docs.paloaltonetworks.com/panorama/administration/administer-panorama/manage-panorama-and-firewall-configuration-backups/revert-panorama-configuration-changes)
- [Revert Changes (11.1)](https://docs.paloaltonetworks.com/ngfw/help/11-1/web-interface-basics/revert-changes)
- [Preview, Validate, or Commit Configuration Changes](https://docs.paloaltonetworks.com/panorama/administration/administer-panorama/preview-validate-or-commit-configuration-changes)
- [Panorama Commit, Validation, and Preview Operations (11.1)](https://docs.paloaltonetworks.com/panorama/11-1/panorama-admin/panorama-overview/panorama-commit-validation-and-preview-operations)
- [Commit](https://docs.paloaltonetworks.com/ngfw/api/pan-os-xml-api-request-types-and-actions/commit)
- [Commit-All](https://docs.paloaltonetworks.com/ngfw/api/pan-os-xml-api-request-types-and-actions/commit-all)
- [CLI Cheat Sheet: Panorama](https://docs.paloaltonetworks.com/ngfw/pan-os-cli-quick-start/cli-cheat-sheet-panorama)

CLI de firewall (`test security-policy-match`, `test nat-policy-match`, `set cli scripting-mode on`) e backup (`save config to`, `scp export configuration`): PAN-OS CLI Quick Start 11.1 (`docs/referencias/pan-os-11.1/ngfw-cli.md`).

## 7. Checklist final de homologação

Laboratório:
- [ ] Panorama de laboratório em PAN-OS <detail-version> com o backup original carregado (A1)
- [ ] Plano recalculado no laboratório idêntico ao aprovado (A2)
- [ ] As 4 fases aplicadas pela XML API sem nenhuma `FALHA`, com os logs guardados (A3)
- [ ] Preview, Validate Changes e Validate Device Group Push sem erros (A4)
- [ ] `validate --exported` sobre o candidate do laboratório: **APROVADO** (A5)
- [ ] Commit no laboratório concluído (A7)
- [ ] Teste de push e políticas em firewall de laboratório idênticos à linha de base (A8, se houver firewall)
- [ ] Ensaio de rollback R2 (e R3, se possível) concluído e conferido (A9)
- [ ] Sintaxe CLI confirmada em 11.1 (A6), **somente se a CLI for usada**

Documentação e decisões:
- [ ] <n> descrições locais substituídas pelas de Shared aceitas
- [ ] <n> objetos mantidos nos DGs aceitos, com motivo
- [ ] <N4> exclusões de não utilizados conferidas contra configuração local dos firewalls, scripts e integrações (`40-…`)
- [ ] Tags: nenhuma alteração (confirmado: <n>/<n> idênticas na Etapa 8)
- [ ] Janela de mudança, executor, revisor e aprovador definidos
- [ ] Servidor SCP e chave de API prontos; certificado do Panorama disponível

Produção (no dia):
- [ ] B0.1 a B0.4 concluídos (sem pendências, plano idêntico, linha de base salva, firewalls em sincronia)
- [ ] B1 backup e bundle exportados, com sha256 registrado

## 8. Critérios para autorizar a implantação

A implantação em produção só pode ser autorizada quando **todos** forem verdadeiros:

1. Etapa 8 com resultado **APROVADO** (<n>/<n>) sobre o XML final `<sha256>`.
2. Homologação em laboratório concluída: checklist da seção 7 completo, com `validate --exported` APROVADO no laboratório e ensaio de rollback bem-sucedido.
3. Pré-verificação do dia (B0.2): o plano recalculado sobre a configuração de produção é **idêntico** ao aprovado. Qualquer diferença exige regenerar plano, comandos e validação.
4. Backups B1 concluídos e verificáveis.
5. Implantação pela **XML API** (`.tsv`). A CLI só pode ser usada se A6 confirmar a sintaxe em Panorama 11.1.
6. Decisões pendentes registradas: descrições, objetos mantidos nos DGs, conferência das exclusões e janela.
7. Equipe disponível durante todo o push gradual e o período de observação, com autoridade para acionar R1 a R3.

Situação frente ao critério da seção 18 do CLAUDE.md:

| Critério | Situação |
|---|---|
| Objetos elegíveis em Shared | <Sim / Não / Parcial — evidência> |
| Conflitos resolvidos | <Sim / Não / Parcial — evidência> |
| Nenhum objeto utilizado excluído | <Sim / Não / Parcial — evidência> |
| Nenhuma referência quebrada | <Sim / Não / Parcial — evidência> |
| Nenhuma alteração não autorizada em políticas | <Sim / Não / Parcial — evidência> |
| Diferenças documentadas | <Sim / Não / Parcial — evidência> |
| Comandos validados para a versão | <Sim / Não / Parcial — evidência> |
| Testes automatizados aprovados | <Sim / Não / Parcial — evidência> |
| Rollback documentado | <Sim / Não / Parcial — evidência> |

**Conclusão: <apto / não apto> para homologação em laboratório.** A autorização para produção depende dos itens 2 a 7 acima.

## 9. Riscos residuais

| Risco | Mitigação |
|---|---|
| Configuração de produção mudou desde o backup | B0.2: o plano recalculado tem de ser idêntico |
| `delete` sem checagem de dependência (CLI) | Usar XML API; a ordem das fases garante as dependências; validações B3 |
| Uso de objetos fora do Panorama (configuração local, scripts) | Conferência das exclusões; a fase 4 pode ir para outra janela |
| Premissa (grupos de Shared resolvem para o objeto do DG no firewall) | Testes de política antes e depois em cada firewall (B4.3), começando pelo piloto |
| `readonly` (IDs internos) | Alterações via API: o Panorama mantém os IDs; `validate --exported` ignora apenas essa seção |
| Regras sem teste automático (<n> das <n> com referência renomeada) | Revisão manual no Preview Changes (B3.1) e monitoração de logs |
