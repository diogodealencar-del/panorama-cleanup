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

Classificações:

-   `USED`: utilizado.
-   `UNUSED`: comprovadamente não utilizado.
-   `UNKNOWN`: uso não determinado ou referência não interpretada.
-   `DUPLICATE`: possível duplicidade identificada.
-   `CONFLICT`: mesmo nome com valores diferentes.

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

------------------------------------------------------------------------

# 2. Criar o projeto no macOS

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

``` markdown
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


    Conteúdo inicial de `requirements.txt`:

    ```text
    lxml>=5.0
    pandas>=2.0
    openpyxl>=3.1
    pytest>=8.0
    defusedxml>=0.7

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

------------------------------------------------------------------------

# 8. Estrutura final esperada

Ao concluir o desenvolvimento, a estrutura deverá se aproximar de:

``` text
panorama-cleanup/
├── CLAUDE.md
├── README.md
├── requirements.txt
├── config/
├── input/
│   └── panorama-backup.xml
├── output/
│   ├── panorama-shared-converted.xml
│   ├── panorama-cleaned.xml
│   └── reports/
│       ├── inventory.csv
│       ├── inventory.xlsx
│       ├── used-objects.csv
│       ├── unused-objects.csv
│       ├── duplicate-objects.csv
│       ├── conflicting-objects.csv
│       ├── dependencies.csv
│       ├── renamed-objects.csv
│       ├── deleted-objects.csv
│       ├── reference-updates.csv
│       ├── policy-comparison.csv
│       └── final-validation-report.html
├── scripts/
│   ├── inventory.py
│   ├── dependency_analyzer.py
│   ├── duplicate_analyzer.py
│   ├── unused_objects.py
│   ├── shared_converter.py
│   ├── policy_comparator.py
│   ├── xml_validator.py
│   └── generate_cli.py
├── cli/
│   ├── 01-backup.txt
│   ├── 02-create-shared.txt
│   ├── 03-resolve-conflicts.txt
│   ├── 04-update-references.txt
│   ├── 05-delete-local-objects.txt
│   ├── 06-delete-unused-objects.txt
│   ├── 07-validation.txt
│   └── 08-commit.txt
├── tests/
├── logs/
├── rollback/
└── docs/
    ├── deployment-procedure.md
    ├── rollback-procedure.md
    └── operational-checklist.md
```

A estrutura poderá ser ajustada durante o desenvolvimento, desde que as
responsabilidades continuem separadas.

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

O conteúdo a seguir deverá ser salvo integralmente no arquivo
`CLAUDE.md` na raiz do projeto.

``` markdown
# CLAUDE.md
# PANORAMA CONFIGURATION ANALYZER & OBJECT CLEANUP

## 1. IDENTIDADE DO PROJETO

Este projeto tem como finalidade desenvolver uma ferramenta de análise, auditoria e saneamento de arquivos XML de configuração do Palo Alto Networks Panorama.

Atue como engenheiro sênior especializado em Palo Alto Networks, Panorama, PAN-OS, XML de configuração, Python 3, Panorama XML API, PAN-OS CLI, Device Groups, Shared Objects, análise de dependências e automação de configurações.

O objetivo é analisar backups XML, inventariar objetos, identificar objetos utilizados e não utilizados, resolver conflitos de nomenclatura, centralizar objetos elegíveis em Shared e gerar os comandos necessários para executar as alterações no Panorama.

A prioridade absoluta é preservar a integridade das configurações e das políticas dos firewalls.

## 2. PRINCÍPIOS FUNDAMENTAIS

1. Segurança antes de velocidade.
2. Análise antes de alteração.
3. Inventário antes de limpeza.
4. Dependências antes de exclusão.
5. Planejamento antes de execução.
6. Validação antes de aprovação.
7. Rastreabilidade de todas as alterações.
8. Preservação integral do XML original.
9. Nenhuma alteração não documentada.
10. Nenhuma operação destrutiva sem aprovação.

Nunca assumir que uma configuração está correta apenas porque o XML é sintaticamente válido.

## 3. REGRAS DE PRESERVAÇÃO DAS POLÍTICAS

As seguintes configurações são protegidas:

- Security Policies.
- NAT Policies.
- Decryption Policies.
- Policy Based Forwarding.
- QoS Policies.
- Application Override.
- Authentication Policies.
- DoS Protection Policies.
- Captive Portal.
- SD-WAN Policies.
- Outras rulebases encontradas no XML.

É proibido excluir regras, criar regras novas, alterar ordem, UUIDs, ações, endereços, zonas, aplicações, serviços, parâmetros NAT, parâmetros PBF, configurações Decryption, Security Profiles, Log Settings ou qualquer parâmetro que modifique o comportamento das políticas.

Única exceção: atualização estritamente necessária de referências de objetos, quando houver renomeação ou mudança de escopo.

Toda atualização de referência deverá ser documentada e validada.

A ferramenta deverá comparar as políticas originais e modificadas, garantindo equivalência semântica.

Se não for possível comprovar a equivalência, interromper o processamento.

## 4. ESCOPO DO SANEAMENTO

Identificar e analisar, conforme os tipos suportados pelo Panorama:

- Address Objects.
- Address Groups.
- Service Objects.
- Service Groups.
- Application Objects.
- Application Groups.
- Application Filters.
- Tags.
- Schedules.
- Security Profiles.
- Security Profile Groups.
- Outros objetos existentes no backup.

Considerar Shared, Device Groups, grupos ancestrais, grupos descendentes, objetos herdados e sobreposição de nomes.

Todos os objetos elegíveis deverão ser centralizados em Shared.

Tipos de objetos que não suportem essa operação deverão ser identificados e documentados, sem tentativa de conversão indevida.

Não alterar a hierarquia dos Device Groups, associação dos firewalls, templates ou template stacks.

## 5. TRATAMENTO DE OBJETOS DUPLICADOS

Diferenciar três situações.

### A. Mesmo nome e mesmo valor

Exemplo:

DG-RJ:
SERVER = 192.168.10.10

DG-BH:
SERVER = 192.168.10.10

Pode ser consolidado em um objeto Shared, desde que todas as dependências e propriedades sejam compatíveis.

### B. Mesmo nome e valores diferentes

Exemplo:

DG-RJ:
SERVER = 192.168.10.10

DG-BH:
SERVER = 192.168.20.10

Resultado esperado:

Shared:
SERVER_DG-RJ = 192.168.10.10
SERVER_DG-BH = 192.168.20.10

Atualizar referências conforme a origem e o contexto de cada objeto.

Nunca utilizar o mesmo objeto para representar valores diferentes.

### C. Nomes diferentes e mesmo valor

Identificar como possível duplicidade.

Não consolidar automaticamente. Verificar referências, dependências e atributos.

## 6. PADRÃO DE NOMENCLATURA

Utilizar:

NOME_ORIGINAL_DEVICEGROUP

Exemplos:

SERVER_DG-RJ
SERVER_DG-BH
DNS_DG-MATRIZ
WEB_DG-FILIAL

Verificar limites de caracteres, caracteres permitidos, colisões, nomes já existentes em Shared e objetos herdados.

O nome gerado deverá ser determinístico.

Não truncar nomes sem verificar possíveis colisões.

## 7. ANÁLISE DE UTILIZAÇÃO E DEPENDÊNCIAS

Construir um mecanismo de análise de dependências.

Não utilizar apenas pesquisa textual de nomes no XML.

Identificar referências diretas, indiretas, entre grupos, entre objetos, em todas as rulebases, templates, interfaces, configurações de rede, VPNs e demais estruturas relevantes.

Considerar a hierarquia e a herança dos Device Groups.

Classificar cada objeto como:

- USED.
- UNUSED.
- UNKNOWN.
- DUPLICATE.
- CONFLICT.

Um objeto somente poderá ser removido quando sua ausência de utilização estiver comprovada.

Objetos UNKNOWN nunca poderão ser removidos automaticamente.

Antes de excluir um objeto, verificar se ele é membro de outro objeto ou grupo.

## 8. REGRAS DE MANIPULAÇÃO XML

- Nunca sobrescrever o XML original.
- Trabalhar sempre com cópias.
- Preservar namespaces.
- Preservar atributos desconhecidos.
- Preservar elementos não relacionados ao saneamento.
- Preservar a hierarquia XML.
- Não descartar configurações não reconhecidas.
- Implementar validação antes e depois de cada transformação.
- Registrar todas as alterações.

Utilizar bibliotecas Python apropriadas.

Preferir uma abordagem que preserve a estrutura XML original e minimize alterações desnecessárias.

## 9. ARQUITETURA DO SOFTWARE

Separar as responsabilidades em módulos independentes:

- inventory.py: inventário.
- dependency_analyzer.py: análise de dependências.
- duplicate_analyzer.py: duplicidades e conflitos.
- unused_objects.py: identificação de objetos não utilizados.
- shared_converter.py: conversão para Shared.
- xml_validator.py: validação estrutural e semântica.
- policy_comparator.py: comparação de políticas.
- generate_cli.py: geração dos comandos.
- report_generator.py: geração de relatórios.

Implementar testes automatizados para cada módulo.

Não concentrar toda a lógica em um único script.

## 10. MODOS DE EXECUÇÃO

A ferramenta deverá possuir cinco modos independentes.

### INVENTORY

Somente leitura. Gera inventário dos objetos.

### ANALYZE

Somente leitura. Analisa utilização, dependências, duplicidades e conflitos.

### PLAN

Somente leitura. Gera o plano de alterações e os comandos propostos.

### APPLY

Executa as transformações em uma cópia do XML. Nunca modifica o original.

### VALIDATE

Compara original e modificado. Valida estrutura, políticas, referências e objetos.

Nenhum modo poderá executar automaticamente o modo seguinte.

## 11. GERAÇÃO DOS COMANDOS CLI

Gerar comandos completos e específicos para a versão identificada do Panorama.

Os comandos deverão contemplar:

1. Backup.
2. Criação de objetos Shared.
3. Renomeação de conflitos.
4. Atualização de referências.
5. Exclusão de objetos locais redundantes.
6. Exclusão de objetos não utilizados.
7. Validação.
8. Commit controlado.

Para cada comando, apresentar sintaxe completa, contexto de execução, finalidade, resultado esperado, método de validação e risco operacional.

Não inventar comandos.

Distinguir claramente PAN-OS CLI, Panorama XML API, Python e Shell.

Quando uma operação não puder ser realizada de forma segura pela CLI, apresentar alternativa documentada, preferencialmente XML API ou importação controlada de configuração.

Não executar commit ou push automaticamente.

## 12. PRESERVAÇÃO DE ESCOPO E REFERÊNCIAS

Antes de centralizar objetos, determinar como cada objeto é resolvido no respectivo Device Group.

Considerar objetos Shared, locais, herdados, sobreposição de nomes, referências entre grupos e referências em diferentes rulebases.

Não assumir que dois objetos de mesmo nome são equivalentes.

Não atualizar referências sem identificar a origem e o contexto.

Toda substituição deverá possuir evidência de equivalência.

## 13. RELATÓRIOS OBRIGATÓRIOS

Gerar:

- inventory.csv.
- inventory.xlsx.
- used-objects.csv.
- unused-objects.csv.
- duplicate-objects.csv.
- conflicting-objects.csv.
- dependencies.csv.
- renamed-objects.csv.
- deleted-objects.csv.
- reference-updates.csv.
- policy-comparison.csv.
- validation-report.html.

Os relatórios deverão ser rastreáveis ao arquivo original e à execução correspondente.

## 14. SEGURANÇA OPERACIONAL

- Não acessar o Panorama automaticamente.
- Não executar alterações remotas sem autorização.
- Não executar commit automaticamente.
- Não executar push automaticamente.
- Não sobrescrever backups.
- Não executar operações destrutivas sem aprovação.
- Não considerar o XML final pronto para produção sem validação.
- Manter logs de todas as operações.

## 15. TESTES AUTOMATIZADOS

Criar testes para leitura de XML, identificação de Device Groups, herança de objetos, referências diretas e indiretas, objetos duplicados, objetos conflitantes, objetos não utilizados, conversão para Shared, atualização de referências, exclusão segura e preservação das políticas.

Criar arquivos XML de teste com diferentes cenários.

Incluir testes negativos para referências ambíguas e objetos não reconhecidos.

## 16. FONTES TÉCNICAS

Priorizar documentação oficial Palo Alto Networks:

https://docs.paloaltonetworks.com/panorama

https://docs.paloaltonetworks.com/pan-os

https://docs.paloaltonetworks.com/pan-os/11-1/pan-os-panorama-api

https://docs.paloaltonetworks.com/panorama/administration/manage-firewalls/manage-device-groups

A documentação deverá corresponder à versão identificada no backup.

Não presumir que comandos de uma versão funcionam em outra.

## 17. PROTOCOLO DE EXECUÇÃO

Executar o projeto na seguinte sequência:

1. Estruturação.
2. Leitura e inventário.
3. Análise de dependências.
4. Planejamento.
5. Conversão para Shared.
6. Remoção de objetos não utilizados.
7. Geração de comandos CLI.
8. Validação e comparação.
9. Homologação e rollback.
10. Documentação final.

Cada etapa deverá ter objetivo definido, produzir entregáveis, executar testes, apresentar resultados, identificar limitações e aguardar aprovação antes da próxima etapa.

Não pular etapas.

Não iniciar automaticamente uma etapa seguinte.

## 18. CRITÉRIO DE APROVAÇÃO

O projeto somente poderá ser considerado aprovado quando:

- Todos os objetos elegíveis estiverem em Shared.
- Todos os conflitos estiverem resolvidos.
- Nenhum objeto utilizado tiver sido excluído.
- Nenhuma referência estiver quebrada.
- Nenhuma política tiver sofrido alteração não autorizada.
- Todas as diferenças estiverem documentadas.
- Os comandos estiverem validados para a versão correspondente.
- Os testes automatizados estiverem aprovados.
- O rollback estiver documentado.

Qualquer inconsistência deverá resultar em NÃO APROVADO.

## 19. INSTRUÇÃO FINAL

Trabalhe como engenheiro responsável pela integridade da configuração.

Não priorize velocidade em detrimento de segurança.

Não faça suposições.

Não invente sintaxe.

Não ignore dependências.

Não modifique políticas para facilitar o processamento.

Se não houver evidência suficiente para uma operação, interrompa essa operação e documente a limitação.

O resultado deverá ser uma configuração Panorama íntegra, rastreável, validada e tecnicamente confiável.
```

------------------------------------------------------------------------

# Anexo B: lembrete operacional

A sequência resumida para utilização diária é:

``` text
1. Abrir o projeto no Claude Code.
2. Conferir o CLAUDE.md.
3. Colocar o backup original em input/.
4. Executar INVENTORY.
5. Conferir os relatórios.
6. Executar ANALYZE.
7. Conferir dependências e conflitos.
8. Executar PLAN.
9. Aprovar o plano.
10. Executar APPLY.
11. Revisar as transformações.
12. Executar VALIDATE.
13. Gerar e revisar os comandos CLI.
14. Homologar em ambiente separado.
15. Autorizar implantação somente após aprovação.
```

**Regra operacional final:** o Claude não deverá avançar automaticamente
entre etapas. A aprovação humana é obrigatória antes de qualquer
transformação ou operação destrutiva.
