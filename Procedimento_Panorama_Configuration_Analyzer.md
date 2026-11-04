# Procedimento de criação e execução

## Panorama Configuration Analyzer & Object Cleanup

**Objetivo:** estabelecer, em ordem cronológica, a criação de um projeto
no Claude Code para analisar backups XML do Palo Alto Networks Panorama,
inventariar objetos, identificar dependências, planejar a centralização
de objetos em Shared, remover objetos comprovadamente não utilizados,
gerar comandos de implantação e validar a integridade da configuração.

**Princípio central:** preservar a lógica e os parâmetros das políticas.
Nenhuma transformação poderá ser considerada segura sem análise de
dependências, rastreabilidade e validação comparativa.

> **Sobre este documento.** Este é o procedimento usado para **construir**
> a ferramenta com o Claude Code, etapa por etapa. A ferramenta já está
> implementada (versão 0.2.4) e foi validada com commit e push em Panorama
> PAN-OS 11.1. Para **usar** a ferramenta, não é preciso recriar o projeto:
>
> - instalação e atualização: [`README.md`](README.md);
> - uso etapa por etapa: [`docs/procedimento-uso-da-ferramenta.md`](docs/procedimento-uso-da-ferramenta.md);
> - homologação, implantação e rollback: [`docs/modelo-etapa-09.md`](docs/modelo-etapa-09.md);
> - regras permanentes: [`CLAUDE.md`](CLAUDE.md).
>
> Os prompts da seção 7 estão preservados como foram usados. Onde a
> implementação divergiu do planejado, há uma nota **Como ficou** e a
> correspondência completa está na seção 8.

------------------------------------------------------------------------

# 1. Escopo e regras fundamentais

O projeto deverá:

1.  Ler backups XML exportados do Panorama.
2.  Identificar a versão do Panorama/PAN-OS, quando disponível.
3.  Inventariar objetos Shared e objetos locais dos Device Groups.
4.  Identificar utilização direta e indireta dos objetos.
5.  Considerar a hierarquia e a herança dos Device Groups.
6.  Identificar objetos duplicados e conflitos de nomenclatura.
7.  Planejar a centralização dos objetos elegíveis em Shared.
8.  Planejar a remoção de objetos comprovadamente não utilizados.
9.  Gerar um XML transformado, sem sobrescrever o original.
10. Gerar comandos CLI e, quando necessário, alternativas via XML API ou
    importação controlada.
11. Validar estrutura, referências e preservação das políticas.
12. Documentar homologação, implantação e rollback.

## 1.1 Políticas e configurações protegidas

Não modificar a lógica, os parâmetros, a ordem ou os UUIDs de:

-   Security Policies.
-   NAT Policies.
-   Decryption Policies.
-   Policy Based Forwarding (PBF).
-   QoS Policies.
-   Application Override Policies.
-   Authentication Policies.
-   DoS Protection Policies.
-   Captive Portal Policies.
-   SD-WAN Policies.
-   Outras rulebases encontradas no backup.

A única alteração admissível dentro das políticas é a atualização
estritamente necessária de uma referência a objeto, quando houver
mudança de nome ou escopo. Essa atualização deverá estar prevista no
plano aprovado e comprovar equivalência semântica do objeto.

Não alterar zonas, endereços, aplicações, serviços, ações, perfis,
parâmetros NAT, parâmetros PBF, logging ou qualquer outra propriedade da
regra.

## 1.2 Segurança dos objetos

Um objeto só poderá ser excluído quando estiver comprovadamente sem
referências ou dependências.

A análise deverá incluir referências diretas e indiretas, grupos,
rulebases, templates, interfaces, configurações de rede, VPNs e demais
estruturas relevantes.

Classificações, em duas dimensões:

| Dimensão | Valor | Significado |
|---|---|---|
| Uso | `USED` | Referência direta ou indireta comprovada |
| Uso | `UNUSED` | Comprovadamente não utilizado |
| Uso | `UNKNOWN` | Uso não determinado, referência apenas possível ou tipo não reconhecido |
| Nome | `DUPLICATE` | Mesmo nome e mesmo valor em mais de um escopo (caso A) |
| Nome | `CONFLICT` | Mesmo nome e valores diferentes (caso B) |
| Valor | `POSSIBLE_DUPLICATE` | Nomes diferentes e mesmo valor (caso C); nunca consolidado automaticamente |

Objetos `UNKNOWN` nunca poderão ser excluídos automaticamente.

Não basta procurar o nome do objeto no XML. A ferramenta deverá
interpretar a estrutura XML e construir as relações de dependência.

## 1.3 Centralização em Shared

Todos os objetos elegíveis deverão ser centralizados em Shared,
respeitando as limitações do tipo de objeto, a hierarquia e a herança
dos Device Groups.

Não alterar:

-   Hierarquia dos Device Groups.
-   Associação dos firewalls aos Device Groups.
-   Templates ou Template Stacks.
-   Configurações não relacionadas à operação.

Se um tipo de objeto não puder ser movido para Shared de forma suportada
e segura, documentar a limitação e não improvisar uma conversão.

## 1.4 Conflitos e duplicidades

Distinguir os seguintes casos.

### Mesmo nome e mesmo valor

Exemplo:

-   `DG-RJ`: `SERVER = 192.168.10.10`
-   `DG-BH`: `SERVER = 192.168.10.10`

Pode ser consolidado em um objeto Shared, desde que todas as
dependências e propriedades sejam compatíveis.

### Mesmo nome e valores diferentes

Exemplo:

-   `DG-RJ`: `SERVER = 192.168.10.10`
-   `DG-BH`: `SERVER = 192.168.20.10`

Resultado esperado:

-   `SERVER_DG-RJ = 192.168.10.10`
-   `SERVER_DG-BH = 192.168.20.10`

As referências deverão ser atualizadas de acordo com o objeto
originalmente resolvido em cada contexto. Nunca apontar uma regra para o
valor pertencente a outro Device Group.

### Nomes diferentes e mesmo valor

Tratar como possível duplicidade. Não consolidar automaticamente.
Verificar propriedades, dependências e referências antes de propor uma
consolidação.

### Padrão de nomenclatura

Utilizar:

`NOME_ORIGINAL_DEVICEGROUP`

Exemplos:

-   `SERVER_DG-RJ`
-   `SERVER_DG-BH`
-   `DNS_DG-MATRIZ`
-   `WEB_DG-FILIAL`

Verificar limites de tamanho, caracteres permitidos, nomes já
existentes, objetos herdados e colisões. O nome gerado deverá ser
determinístico. Não truncar nomes sem verificar colisões.

## 1.5 Decisões adotadas na implementação

Estas decisões surgiram durante a construção e a homologação. Valem como
padrão; mudar qualquer uma exige aprovação explícita.

| Tema | Decisão |
|---|---|
| Transparência | O valor efetivo de cada objeto, resolvido pela herança Shared > DG ancestral > DG, deve ser idêntico antes e depois para cada firewall. Comprovado por simulação no plano e revalidado nas Etapas 5, 6 e 8 |
| Tags | Nenhuma tag é criada, movida, alterada ou excluída (`--keep-type tag` na Etapa 6) |
| Objetos de DG que sobrepõem Shared e são usados por definições de Shared | Ficam no DG (KEEP_LOCAL), com motivo documentado |
| Descrições em consolidação | Vale a do objeto mantido em Shared; as descrições locais perdidas são listadas para aceite |
| Implantação | Panorama XML API (`set`, `edit`, `delete`). A CLI só é usada se a sintaxe estiver confirmada para a versão. O XML final **não** é importado inteiro: a seção `readonly` não é ajustada pela ferramenta |
| Precedência | Se o Panorama estiver com "Objects defined in ancestors will take higher precedence", a análise para: a ferramenta assume o padrão, em que o descendente prevalece |
| Multi-vsys | A ferramenta modela por Device Group. Quando um firewall aparece em mais de um DG, o inventário avisa com o vsys de cada associação, e a homologação faz os testes de política por vsys |
| Commit e push | Nunca automáticos; apenas documentados |
| Isolamento | Uma pasta de trabalho por cliente/Panorama (`--workdir`) |

------------------------------------------------------------------------

# 2. Criar o projeto no macOS

> Para usar a ferramenta pronta, clone o repositório em vez de recriá-lo
> (ver `README.md`). As seções 2 a 6 descrevem a criação original.

Abrir o Terminal e executar:

``` bash
mkdir -p ~/Claude/Projects/panorama-cleanup
cd ~/Claude/Projects/panorama-cleanup

mkdir -p input output/reports scripts cli tests logs rollback docs config
```

Estrutura inicial:

``` text
panorama-cleanup/
├── CLAUDE.md
├── README.md
├── requirements.txt
├── config/
├── input/
├── output/
│   └── reports/
├── scripts/
├── cli/
├── tests/
├── logs/
├── rollback/
└── docs/
```

## 2.1 Abrir o projeto no Claude Code

``` bash
cd ~/Claude/Projects/panorama-cleanup
claude
```

Opcionalmente, abrir no VS Code:

``` bash
code ~/Claude/Projects/panorama-cleanup
```

O Claude Code deverá ser iniciado dentro do diretório do projeto para
que o `CLAUDE.md` seja aplicado como instrução do projeto.

------------------------------------------------------------------------

# 3. Criar o CLAUDE.md

Criar o arquivo:

``` bash
nano CLAUDE.md
```

Inserir o conteúdo integral da seção [Anexo A:
CLAUDE.md](#anexo-a-claudemd), salvar e fechar.

O `CLAUDE.md` contém as regras permanentes de governança, segurança,
arquitetura, execução e aprovação. Os prompts das etapas são instruções
operacionais temporárias e devem ser enviados separadamente.

------------------------------------------------------------------------

# 4. Criar README.md e requirements.txt

Criar os arquivos:

``` bash
touch README.md requirements.txt
```

Conteúdo inicial de `README.md`:

~~~markdown
# Panorama Configuration Analyzer & Object Cleanup

Ferramenta para análise, auditoria e saneamento de configurações XML do Palo Alto Networks Panorama.

## Objetivos

- Inventariar objetos Shared e Device Groups.
- Identificar objetos utilizados e não utilizados.
- Analisar dependências.
- Identificar objetos duplicados e conflitantes.
- Centralizar objetos elegíveis em Shared.
- Remover objetos comprovadamente não utilizados.
- Gerar comandos CLI e alternativas via XML API.
- Validar a integridade das políticas.
- Gerar relatórios comparativos.

## Requisitos

- Python 3.
- Claude Code.
- Backup XML do Panorama.
- Dependências descritas em requirements.txt.

## Ambiente

Ativar o ambiente virtual:

```bash
source .venv/bin/activate
```

## Segurança

O XML original nunca deverá ser modificado.

Nenhuma alteração deverá ser aplicada em produção sem aprovação.

Nenhum commit ou push deverá ser executado automaticamente.

Consultar CLAUDE.md para as instruções permanentes.
~~~

Conteúdo inicial de `requirements.txt`:

```text
lxml>=5.0
pandas>=2.0
openpyxl>=3.1
pytest>=8.0
defusedxml>=0.7
```

As dependências deverão ser revistas pelo Claude durante a Etapa 1. Não
instalar bibliotecas sem necessidade técnica justificada.

## 4.1 Criar ambiente virtual

``` bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
pip install -r requirements.txt
```

Verificar:

``` bash
python --version
pip list
```

------------------------------------------------------------------------

# 5. Inicializar e estruturar o projeto com Claude Code

Com o Claude Code aberto no diretório do projeto, enviar o prompt:

``` text
Leia integralmente o CLAUDE.md e o README.md.

Estamos iniciando o projeto Panorama Configuration Analyzer & Object Cleanup.

Sua primeira tarefa é executar somente a Etapa 1, estruturação do projeto.

Inspecione o ambiente, valide as dependências, organize os diretórios e crie os arquivos iniciais necessários.

Não desenvolva ainda os mecanismos de inventário, análise de dependências, conversão de objetos ou limpeza de XML.

Apresente a estrutura criada, as decisões de arquitetura e os testes executados.

Não avance para a Etapa 2 sem minha aprovação.
```

O Claude deverá concluir a estruturação e apresentar os arquivos
criados, decisões e testes. Revisar o resultado antes de aprovar.

------------------------------------------------------------------------

# 6. Preparar o backup XML

Somente após concluir e aprovar a Etapa 1, copiar o backup original
para:

``` text
panorama-cleanup/input/
```

Exemplo:

``` text
input/
└── panorama-backup.xml
```

Regras:

-   `input/` deverá conter os arquivos originais recebidos.
-   Não editar ou sobrescrever o backup original.
-   Arquivos transformados deverão ser gravados em `output/`.
-   Registrar nome, tamanho e hash SHA-256 do arquivo original.
-   Não utilizar um XML previamente modificado como se fosse o backup
    original.
-   Se houver mais de um backup, identificar claramente qual arquivo
    será processado.

Gerar hash do arquivo:

``` bash
shasum -a 256 input/panorama-backup.xml
```

------------------------------------------------------------------------

# 7. Execução cronológica das etapas

As etapas deverão ser executadas individualmente. Ao final de cada
etapa, revisar os resultados e aprovar explicitamente antes de avançar.

Não enviar todos os prompts de uma só vez.

> Estes são os prompts usados para **construir** cada módulo. Para
> **executar** a ferramenta já pronta sobre um backup, use os comandos de
> [`docs/procedimento-uso-da-ferramenta.md`](docs/procedimento-uso-da-ferramenta.md)
> ou a skill `panorama-cleanup` (Claude Code ou claude.ai).

## Etapa 1: estruturação do projeto

**Objetivo:** preparar diretórios, ambiente, documentação, módulos e
testes iniciais.

**Prompt:**

``` text
Leia o CLAUDE.md e execute somente a Etapa 1 do projeto.

Inspecione o ambiente, valide a versão do Python e as dependências.

Crie a estrutura de diretórios, os arquivos iniciais, a documentação e a estrutura de testes.

Não implemente ainda leitura, inventário, transformação ou limpeza de XML.

Apresente os arquivos criados, as decisões de arquitetura e os testes executados.

Não avance para a Etapa 2 sem minha aprovação.
```

**Aprovação:** estrutura criada, ambiente funcional e documentação
inicial revisada.

## Etapa 2: leitura do XML e inventário

**Objetivo:** interpretar o backup e inventariar objetos Shared e
locais.

**Prompt:**

``` text
Leia o CLAUDE.md e execute a Etapa 2.

Desenvolva o módulo de leitura XML e inventário de objetos.

Utilize o backup XML fornecido em input/.

Identifique a versão, os Device Groups, sua hierarquia e todos os objetos Shared e locais.

Considere Address, Address Groups, Service, Service Groups, Application, Application Groups, Application Filters, Tags, Schedules, Security Profiles, Security Profile Groups e demais tipos encontrados.

Registre nome, tipo, valor, escopo, Device Group de origem e referências identificadas.

Gere inventory.csv, inventory.xlsx e inventory-summary.html.

Crie testes automatizados para leitura e contagem dos objetos.

Não modifique o XML original. Não classifique objetos como não utilizados nesta etapa.

Apresente os resultados, limitações e testes. Aguarde aprovação para a Etapa 3.
```

**Aprovação:** inventário consistente com a estrutura XML, quantidades
conferidas e limitações documentadas.

**Como ficou:** `panorama-cleanup inventory`. Relatórios `inventory.csv`,
`inventory.xlsx`, `inventory.html`, `device-groups.csv`,
`inventory-summary.csv` (reconciliação de contagem) e
`inventory-warnings.csv` (contêineres não reconhecidos, hierarquia
desconhecida, firewalls em mais de um DG).

## Etapa 3: análise de dependências e utilização

**Objetivo:** identificar objetos usados, não usados, duplicados e
conflitantes.

**Prompt:**

``` text
Leia o CLAUDE.md e execute a Etapa 3.

Utilize o inventário da Etapa 2.

Desenvolva a análise de dependências diretas e indiretas, considerando a hierarquia e a herança dos Device Groups.

Analise referências em todas as rulebases e nas demais estruturas relevantes da configuração, incluindo grupos, templates, interfaces, configurações de rede e VPNs.

Construa um grafo de dependências.

Classifique cada objeto como USED, UNUSED, UNKNOWN, DUPLICATE ou CONFLICT.

Não classifique como UNUSED um objeto cuja utilização não tenha sido completamente determinada.

Gere:
- used-objects.csv
- unused-objects.csv
- duplicate-objects.csv
- conflicting-objects.csv
- dependencies.csv
- dependency-analysis.html

Não modifique o XML e não exclua objetos.

Crie testes para referências diretas, indiretas, herança, dependências circulares, objetos não reconhecidos e referências ambíguas.

Apresente os resultados e aguarde aprovação para a Etapa 4.
```

**Aprovação:** rastreabilidade das referências demonstrada; objetos
desconhecidos e ambiguidades identificados.

**Como ficou:** `panorama-cleanup analyze`. Além dos relatórios pedidos,
gera `object-classification.csv`, `unknown-objects.csv`,
`possible-duplicates.csv`, `context-dependent-objects.csv` (grupos de
Shared cujo valor muda conforme o DG, o caso de maior risco) e
`unresolved-references.csv`. O consolidado fica em `analysis.xlsx` e
`analysis.html`.

## Etapa 4: planejamento das alterações

**Objetivo:** produzir um plano completo antes de transformar o XML.

**Prompt:**

``` text
Leia o CLAUDE.md e execute a Etapa 4.

Utilize os inventários e relatórios de dependências das etapas anteriores.

Gere o planejamento detalhado das alterações necessárias para centralizar os objetos elegíveis em Shared.

Identifique:
- Objetos locais a converter.
- Objetos com mesmo nome e mesmo valor.
- Objetos com mesmo nome e valores diferentes.
- Objetos com nomes diferentes e mesmo valor.
- Objetos que podem ser consolidados.
- Objetos que podem ser excluídos.
- Referências que precisarão ser atualizadas.
- Dependências e riscos não resolvidos.

Utilize o padrão NOME_ORIGINAL_DEVICEGROUP para resolver conflitos.

Não faça merge de valores diferentes.

Não modifique o XML.

Gere:
- change-plan.csv
- rename-plan.csv
- merge-plan.csv
- deletion-plan.csv
- reference-update-plan.csv
- change-plan.html

Para cada operação, apresente justificativa, origem, destino, referências afetadas, risco e método de validação.

Aguarde minha aprovação explícita do plano antes de iniciar a Etapa 5.
```

**Aprovação:** revisar individualmente as operações de renomeação,
consolidação, atualização de referências e exclusão.

**Como ficou:** `panorama-cleanup plan`. Os arquivos são
`plan-operations.csv` (operações em 4 fases, na ordem de execução),
`renamed-objects.csv`, `reference-updates.csv`, `deleted-objects.csv`,
`kept-local-objects.csv`, `plan-verification.csv` (simulação de
transparência: precisa ter **0 diferenças**), `plan.xlsx`, `plan.html` e
`plan.json` (plano aprovado, usado pelas etapas seguintes).

## Etapa 5: conversão dos objetos para Shared

**Objetivo:** implementar somente as operações aprovadas no plano.

**Prompt:**

``` text
Leia o CLAUDE.md e execute a Etapa 5.

Utilize exclusivamente o plano de alterações aprovado na Etapa 4.

Implemente a conversão dos objetos elegíveis para Shared.

Resolva conflitos de nomenclatura e atualize as referências necessárias, considerando o contexto de resolução de objetos em cada Device Group.

Consolide objetos idênticos somente quando propriedades e dependências forem compatíveis.

Preserve integralmente as políticas e demais configurações não relacionadas à transformação.

Não altere o XML original.

Gere um novo arquivo XML em output/ e os relatórios:
- shared-conversion-report.csv
- renamed-objects.csv
- reference-updates.csv

Execute testes de integridade e demonstre que as referências continuam resolvidas.

Não inicie a Etapa 6 automaticamente.
```

**Aprovação:** objetos elegíveis centralizados e referências
comprovadamente preservadas.

**Como ficou:** `panorama-cleanup apply` aplica as fases 1 a 3 e recalcula
o plano, exigindo que seja idêntico ao aprovado. XML em
`output/xml/<run>/<backup>.shared.xml`; verificações em
`integrity-checks.csv`.

## Etapa 6: remoção de objetos não utilizados

**Objetivo:** excluir apenas objetos cuja ausência de utilização esteja
comprovada.

**Prompt:**

``` text
Leia o CLAUDE.md e execute a Etapa 6.

Utilize o XML gerado na Etapa 5 e os relatórios da Etapa 3.

Revise a classificação UNUSED e confirme novamente a ausência de referências e dependências.

Remova somente objetos comprovadamente não utilizados e previstos no plano aprovado.

Preserve todos os objetos USED e UNKNOWN.

Verifique novamente grupos, dependências indiretas, rulebases, templates, interfaces, configurações de rede e demais estruturas relevantes.

Respeite a ordem de dependência durante as exclusões.

Gere:
- panorama-cleaned.xml
- deleted-objects.csv
- cleanup-report.html

Não modifique o XML original.

Execute testes de integridade e aguarde aprovação para a Etapa 7.
```

**Aprovação:** cada exclusão justificada e ausência de referências
quebradas comprovada.

**Como ficou:** `panorama-cleanup apply --stage cleanup --keep-type tag`
aplica a fase 4 sobre o XML convertido. XML final em
`output/xml/<run>/<backup>.final.xml`; prova de cada candidato em
`unused-review.csv`.

## Etapa 7: geração dos comandos CLI

**Objetivo:** gerar os comandos para reproduzir as operações no
Panorama.

**Prompt:**

``` text
Leia o CLAUDE.md e execute a Etapa 7.

Analise o XML original, o XML final e todos os relatórios de alterações.

Identifique a versão do Panorama e valide os comandos na documentação oficial correspondente.

Gere comandos CLI completos, específicos e ordenados para reproduzir as operações.

Separar em:
- 01-backup.txt
- 02-create-shared.txt
- 03-resolve-conflicts.txt
- 04-update-references.txt
- 05-delete-local-objects.txt
- 06-delete-unused-objects.txt
- 07-validation.txt
- 08-commit.txt

Para cada comando, documente contexto, finalidade, resultado esperado, validação e risco.

Diferencie PAN-OS CLI, Panorama XML API, Python e Shell.

Não invente sintaxe e não apresente comandos hipotéticos como funcionais.

Quando a CLI não suportar adequadamente uma operação, documente a limitação e apresente uma alternativa suportada, como XML API ou importação controlada.

Não execute comandos remotamente. Não execute commit ou push.

Aguarde aprovação para a Etapa 8.
```

**Aprovação:** sintaxe, contexto, sequência e efeitos dos comandos
revisados para a versão identificada.

**Como ficou:** `panorama-cleanup plan --stage commands`. A forma
recomendada é a **XML API**: um `.tsv` por fase (`10-…` a `40-…`),
executado pelo `run-xmlapi.sh`, que para no primeiro erro e não faz
commit. Os `.cli` equivalentes só são usados se `cli-syntax-check.csv`
confirmar a sintaxe na versão. A conferência usa o PAN-OS CLI Quick Start
da versão do backup; sem ele, a etapa para de propósito. Arquivos:
`00-LEIA-ME.md`, `01-precheck.md`, `02-backup.cli`, `50-validacao.md`,
`60-commit-e-push.md`, `70-alternativa-load-config-partial.md`,
`80-testes-de-politica.cli` e `commands-index.csv`.

## Etapa 8: validação e comparação

**Objetivo:** comprovar a integridade do XML transformado.

**Prompt:**

``` text
Leia o CLAUDE.md e execute a Etapa 8.

Compare o XML original com o XML final.

Valide:
- Sintaxe e estrutura XML.
- Device Groups e hierarquia.
- Objetos Shared e locais.
- Referências e dependências.
- Todas as rulebases.
- Quantidade e ordem das regras.
- Nomes e UUIDs.
- Actions, Source, Destination, Zones, Applications e Services.
- NAT Translation e demais parâmetros NAT.
- PBF.
- Decryption.
- Security Profiles.
- Log Settings.
- Templates, interfaces, VPNs e demais configurações não relacionadas.

As únicas diferenças admissíveis nas políticas são atualizações de referências expressamente aprovadas e comprovadamente equivalentes.

Gere:
- policy-comparison.csv
- object-comparison.csv
- reference-validation.csv
- xml-validation-report.html
- final-validation-report.html

Se encontrar qualquer alteração inesperada, referência quebrada ou inconsistência, classifique como NÃO APROVADO.

Não avance automaticamente.
```

**Aprovação:** nenhuma alteração inesperada; todas as referências
resolvidas; políticas semanticamente preservadas.

**Como ficou:** `panorama-cleanup validate` gera `validation-report.html`
com **APROVADO** ou **NÃO APROVADO** (código de saída 0 ou 1), mais
`policy-comparison.csv`, `policy-field-changes.csv`,
`object-comparison.csv`, `reference-validation.csv`, `tag-validation.csv`
e `remaining-dg-objects.csv`. Com `--exported`, valida um candidate ou
running exportado do Panorama após a implantação.

## Etapa 9: homologação e rollback

**Objetivo:** preparar a implantação controlada, sem executar alterações
em produção.

**Prompt:**

``` text
Leia o CLAUDE.md e execute a Etapa 9.

Prepare o procedimento operacional para homologação e implantação no Panorama.

Documente:
1. Backup da configuração atual.
2. Preparação do ambiente de homologação.
3. Importação controlada do XML.
4. Validação da configuração na versão correspondente.
5. Commit no Panorama, somente após aprovação.
6. Planejamento de push gradual.
7. Verificações após implantação.
8. Critérios de interrupção.
9. Procedimento de rollback.

Gere:
- deployment-procedure.md
- rollback-procedure.md
- operational-checklist.md

Inclua os comandos necessários, o contexto de execução e os resultados esperados.

Não execute nenhuma operação em produção.
Não execute commit ou push.
Apresente o checklist final e aguarde aprovação.
```

> **Correção importante.** O item 3 do prompt ("importação controlada do
> XML") não se confirmou como caminho seguro. A ferramenta não ajusta a
> seção `readonly` (IDs internos mantidos pelo Panorama), então o XML final
> **não deve ser importado inteiro** em produção. A implantação validada é
> pela XML API, com os `.tsv` da Etapa 7. A importação do backup **original**
> continua válida para montar o laboratório.

**Como ficou:** o procedimento está no modelo
[`docs/modelo-etapa-09.md`](docs/modelo-etapa-09.md): laboratório
(aplicar os `.tsv`, Validate Changes, `validate --exported` sobre o
candidate e ensaio de rollback), produção com pré-verificação, backup,
commit só no Panorama e push gradual por DG com testes de política, e
rollback em quatro níveis. Copie o modelo para a pasta de trabalho do
cliente e preencha.

------------------------------------------------------------------------

# 8. Estrutura final

O planejado (sugestão do `CLAUDE.md`) e o implementado:

| Planejado | Implementado |
|---|---|
| `scripts/inventory.py` e demais módulos | Pacote `panorama_cleanup/` (um módulo por responsabilidade) e comando `panorama-cleanup` |
| `input/panorama-backup.xml` | `<workdir>/input/` ou qualquer caminho em `--input`; o original nunca é alterado |
| `output/reports/*.csv` | `<workdir>/output/reports/<run_id>/`, uma pasta por execução, com `manifest.json` |
| `output/panorama-shared-converted.xml` | `<workdir>/output/xml/<run apply>/<backup>.shared.xml` |
| `output/panorama-cleaned.xml` | `<workdir>/output/xml/<run apply-cleanup>/<backup>.final.xml` |
| `cli/01-backup.txt` … `08-commit.txt` | `<workdir>/cli/<run plan-commands>/`: `00-LEIA-ME.md`, `02-backup.cli`, `10-…` a `40-….tsv`/`.cli`, `run-xmlapi.sh`, `50-…`, `60-…`, `70-…`, `80-…` |
| `docs/deployment-procedure.md`, `rollback-procedure.md`, `operational-checklist.md` | `docs/modelo-etapa-09.md` (um único modelo, preenchido por cliente) |
| `final-validation-report.html` | `validation-report.html` |

O `run_id` tem o formato `AAAAMMDDTHHMMSSZ-<modo>-<8 primeiros hex do sha256 do backup>`,
o que liga cada relatório ao arquivo original.

Repositório:

``` text
panorama-cleanup/
├── CLAUDE.md                     regras permanentes
├── README.md                     instalação, uso e atualização
├── pyproject.toml                pacote e comando panorama-cleanup
├── panorama_cleanup/             código-fonte
├── tests/                        testes automatizados e fixtures XML sintéticas
├── config/settings.json          caminhos padrão
├── docs/
│   ├── arquitetura.md
│   ├── procedimento-uso-da-ferramenta.md
│   ├── modelo-etapa-09.md
│   └── referencias/              guias oficiais por versão (fora do git)
├── skill/                        fonte única das skills (plugin e claude.ai)
├── claude-plugin/                plugin do Claude Code (gerado de skill/)
├── .claude-plugin/               marketplace panorama-tools
└── scripts/                      check_env.py, build_skill.py
```

Por cliente (`--workdir`), fora do repositório:

``` text
<workdir>/
├── input/      backup original
├── output/     reports/<run_id>/ e xml/<run_id>/
├── cli/        comandos por run
├── rollback/
└── logs/       um log por execução
```

------------------------------------------------------------------------

# 9. Regras de controle entre etapas

Ao final de cada etapa, exigir do Claude:

1.  Resumo do que foi desenvolvido.
2.  Arquivos criados e modificados.
3.  Testes executados.
4.  Resultado dos testes.
5.  Problemas encontrados.
6.  Limitações técnicas.
7.  Critério de aprovação da etapa.
8.  Confirmação de que o XML original permaneceu intacto, quando
    aplicável.

Não aprovar uma etapa apenas porque o Claude informou que foi concluída.
Conferir os arquivos, relatórios e evidências.

Se houver falha, corrigir a etapa atual antes de avançar.

Não permitir que o Claude execute automaticamente a etapa seguinte.

------------------------------------------------------------------------

# 10. Critérios finais de aprovação

O projeto somente poderá ser considerado aprovado quando:

-   Todos os objetos elegíveis estiverem em Shared.
-   Objetos com valores diferentes estiverem preservados separadamente.
-   Conflitos de nomenclatura estiverem resolvidos.
-   Objetos não utilizados tiverem sido removidos com comprovação de
    ausência de dependências.
-   Nenhum objeto utilizado tiver sido excluído.
-   Nenhuma referência estiver quebrada.
-   Nenhuma política tiver sofrido alteração não autorizada.
-   Todas as diferenças estiverem documentadas.
-   Os comandos estiverem validados para a versão correspondente.
-   Os testes automatizados estiverem aprovados.
-   O procedimento de homologação estiver documentado.
-   O rollback estiver documentado.

Qualquer inconsistência relevante deverá resultar em `NÃO APROVADO`.

**Importante:** XML sintaticamente válido não significa configuração
válida para o Panorama. A validação final deverá ocorrer em ambiente de
homologação compatível com a versão de destino. Não realizar push para
firewalls antes da aprovação formal.

------------------------------------------------------------------------

# Anexo A: CLAUDE.md

O `CLAUDE.md` vigente está na raiz do repositório: [`CLAUDE.md`](CLAUDE.md).
Para criar um projeto novo, copie esse arquivo integralmente.

Este anexo trazia uma cópia condensada de uma versão anterior. Ela foi
removida para haver uma única fonte; o histórico do git preserva o texto
original.

------------------------------------------------------------------------

# Anexo B: lembrete operacional

A sequência resumida para utilização diária, com a ferramenta pronta:

``` text
1.  Confirmar que o contrato do cliente permite processar o backup no ambiente escolhido.
2.  Criar a pasta de trabalho do cliente e copiar o export completo do Panorama para <workdir>/input/.
3.  Registrar o sha256 do backup.
4.  INVENTORY: panorama-cleanup --workdir W inventory --input B. Conferir versão, hierarquia, reconciliação e avisos.
5.  ANALYZE: conferir não resolvidos, UNKNOWN e objetos dependentes de contexto.
6.  PLAN: plan-verification.csv com 0 diferenças; revisar nomes, descrições, mantidos e exclusões. Aprovar.
7.  APPLY (convert): integridade toda OK.
8.  APPLY --stage cleanup --keep-type tag: integridade 14/14 OK.
9.  PLAN --stage commands: generation-checks.csv OK; definir XML API como interface.
10. VALIDATE: APROVADO.
11. Homologar em laboratório (run-xmlapi.sh, validate --exported, ensaio de rollback).
12. Implantar em produção somente em janela aprovada, com push gradual por DG.
13. Arquivar runs, logs e decisões.
```

O detalhe de cada passo está em
[`docs/procedimento-uso-da-ferramenta.md`](docs/procedimento-uso-da-ferramenta.md).

**Regra operacional final:** o Claude não deverá avançar automaticamente
entre etapas. A aprovação humana é obrigatória antes de qualquer
transformação ou operação destrutiva.
