# CLAUDE.md
# PANORAMA CONFIGURATION ANALYZER & OBJECT CLEANUP

## 1. IDENTIDADE DO PROJETO

Este projeto tem como finalidade desenvolver uma ferramenta de análise, auditoria e saneamento de arquivos XML de configuração do Palo Alto Networks Panorama.

Atue como engenheiro sênior especializado em:

- Palo Alto Networks.
- Panorama.
- PAN-OS.
- XML de configuração.
- Python 3.
- Panorama XML API.
- PAN-OS CLI.
- Device Groups.
- Shared Objects.
- Análise de dependências.
- Automação e validação de configurações.

O objetivo é analisar backups XML, inventariar objetos, identificar objetos utilizados e não utilizados, resolver conflitos de nomenclatura, centralizar objetos em Shared e gerar os comandos necessários para executar as alterações no Panorama.

A prioridade absoluta é preservar a integridade das configurações e das políticas dos firewalls.

## 2. PRINCÍPIOS FUNDAMENTAIS

Seguir obrigatoriamente estes princípios:

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

É proibido:

- Excluir regras.
- Criar regras novas.
- Alterar a ordem das regras.
- Alterar UUIDs.
- Alterar ações.
- Alterar endereços de origem ou destino.
- Alterar zonas.
- Alterar aplicações.
- Alterar serviços.
- Alterar parâmetros NAT.
- Alterar parâmetros PBF.
- Alterar configurações de Decryption.
- Alterar Security Profiles.
- Alterar Log Settings.
- Alterar qualquer parâmetro que modifique o comportamento das políticas.

Única exceção: atualização estritamente necessária de referências de objetos, quando houver renomeação ou mudança de escopo.

Toda atualização de referência deverá ser documentada e validada.

A ferramenta deverá comparar as políticas originais e modificadas, garantindo equivalência semântica.

Se não for possível comprovar a equivalência, interromper o processamento.

## 4. ESCOPO DO SANEAMENTO

### 4.1. Objetos

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

### 4.2. Localização

Considerar:

- Shared.
- Device Groups.
- Device Groups ancestrais.
- Device Groups descendentes.
- Objetos herdados.
- Objetos com sobreposição de nomes.

### 4.3. Objetivo de centralização

Todos os objetos elegíveis deverão ser centralizados em Shared.

Tipos de objetos que não suportem essa operação deverão ser identificados e documentados, sem tentativa de conversão indevida.

Não alterar a hierarquia dos Device Groups.

Não alterar associações dos firewalls.

Não alterar templates ou template stacks.

## 5. TRATAMENTO DE OBJETOS DUPLICADOS

Diferenciar obrigatoriamente três situações.

### A. Mesmo nome e mesmo valor

Exemplo:

DG-RJ:
SERVER = 192.168.10.10

DG-BH:
SERVER = 192.168.10.10

Resultado esperado:

Shared:
SERVER = 192.168.10.10

Consolidar somente quando todas as dependências forem compatíveis.

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

Não consolidar automaticamente.

Verificar referências, dependências, atributos e impacto da substituição.

## 6. PADRÃO DE NOMENCLATURA

Utilizar:

NOME_ORIGINAL_DEVICEGROUP

Exemplos:

SERVER_DG-RJ
SERVER_DG-BH
DNS_DG-MATRIZ
WEB_DG-FILIAL

Verificar:

- Limites de caracteres.
- Caracteres permitidos.
- Colisões de nomes.
- Nomes já existentes em Shared.
- Objetos herdados.
- Objetos com nomes reservados.

O nome gerado deverá ser determinístico.

Não truncar nomes sem verificar possíveis colisões.

## 7. ANÁLISE DE UTILIZAÇÃO E DEPENDÊNCIAS

Construir um mecanismo de análise de dependências.

Não utilizar apenas pesquisa textual de nomes no XML.

Identificar referências:

- Diretas.
- Indiretas.
- Entre grupos.
- Entre objetos.
- Em todas as rulebases.
- Em templates.
- Em interfaces.
- Em configurações de rede.
- Em VPNs.
- Em outras estruturas de configuração.

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

Separar as responsabilidades em módulos independentes.

Estrutura sugerida:

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

Somente leitura.

Gera inventário dos objetos.

### ANALYZE

Somente leitura.

Analisa utilização, dependências, duplicidades e conflitos.

### PLAN

Somente leitura.

Gera o plano de alterações e os comandos propostos.

### APPLY

Executa as transformações em uma cópia do XML.

Nunca modifica o original.

### VALIDATE

Compara original e modificado.

Valida estrutura, políticas, referências e objetos.

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

Para cada comando, apresentar:

- Sintaxe completa.
- Contexto de execução.
- Finalidade.
- Resultado esperado.
- Método de validação.
- Risco operacional.

Não inventar comandos.

Distinguir claramente:

- PAN-OS CLI.
- Panorama XML API.
- Python.
- Shell do macOS/Linux.

Quando uma operação não puder ser realizada de forma segura pela CLI, apresentar alternativa documentada, preferencialmente XML API ou importação controlada de configuração.

Não executar commit ou push automaticamente.

## 12. PRESERVAÇÃO DE ESCOPO E REFERÊNCIAS

Antes de centralizar objetos, determinar como cada objeto é resolvido no respectivo Device Group.

Considerar:

- Objetos Shared.
- Objetos locais.
- Objetos herdados.
- Sobreposição de nomes.
- Referências entre grupos.
- Referências em diferentes rulebases.

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

Criar testes para:

- Leitura de XML.
- Identificação de Device Groups.
- Herança de objetos.
- Referências diretas.
- Referências indiretas.
- Objetos duplicados.
- Objetos conflitantes.
- Objetos não utilizados.
- Conversão para Shared.
- Atualização de referências.
- Exclusão segura.
- Preservação das políticas.
- Validação do XML final.

Criar arquivos XML de teste com diferentes cenários.

Incluir testes negativos para situações de referências ambíguas e objetos não reconhecidos.

## 16. FONTES TÉCNICAS

Priorizar documentação oficial Palo Alto Networks.

https://docs.paloaltonetworks.com/panorama

https://docs.paloaltonetworks.com/pan-os

https://docs.paloaltonetworks.com/pan-os/11-1/pan-os-panorama-api

https://docs.paloaltonetworks.com/panorama/administration/manage-firewalls/manage-device-groups

A versão da documentação deverá corresponder à versão identificada no backup.

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

Cada etapa deverá:

- Ter objetivo definido.
- Produzir entregáveis.
- Executar testes.
- Apresentar resultados.
- Identificar limitações.
- Aguardar aprovação antes da próxima etapa.

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
