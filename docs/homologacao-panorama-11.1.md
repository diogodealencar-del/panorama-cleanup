# Homologação em Panorama 11.1: comportamentos observados

Registro do primeiro teste das fases 0 a 4 em um Panorama real de laboratório. O laboratório nem
sempre estará disponível: este documento serve de referência para corrigir a ferramenta e os
procedimentos (`docs/modelo-etapa-09.md`) quando não houver onde testar.

Dados do ambiente (endereços, nomes, seriais, Device Groups) foram omitidos de propósito.

## 1. Contexto

| Item | Valor |
|---|---|
| Data | 13/11/2026 |
| Panorama de laboratório | PAN-OS 11.1.6-h7 (`detail-version` da configuração: 11.1.10) |
| Backup de origem | Panorama de produção, PAN-OS 11.1.13 |
| Versão da ferramenta | 0.3.0 + correções desta homologação |
| Plano | 4.822 operações; 4 Device Groups filhos diretos de Shared |
| Firewalls conectados ao laboratório | nenhum |

A diferença de versão (11.1.6-h7 × 11.1.13) significa que o teste comprova o procedimento, não o
comportamento exato da 11.1.13.

## 2. Resultado

| Fase | Arquivo | Operações | Resultado |
|---|---|---:|---|
| 0 | `05-fase0-copias-locais.tsv` | 94 | todas aceitas |
| 1 | `10-fase1-criar-shared.tsv` | 1.030 | todas aceitas |
| 2 | `20-fase2-atualizar-referencias.tsv` | 577 | todas aceitas |
| 3 | `30-fase3-excluir-copias-locais.tsv` | 1.951 | todas aceitas |
| 4 | `40-fase4-excluir-nao-utilizados.tsv` | 795 | todas aceitas |

- Tempo: cerca de 27 minutos para 4.447 operações (0,4 a 0,5 s por operação; uma conexão HTTPS por
  operação).
- **Validate Changes** do Panorama: "Configuration is valid".
- `validate --exported` sobre o candidate real: **APROVADO**.
- Candidate real comparado ao XML final simulado pela ferramenta: `shared` e todos os
  `device-group` idênticos (C14N). Só diferem `/config/readonly` e `devices/entry/platform`, mantidos
  pelo próprio Panorama.
- Commit no Panorama de laboratório concluído; push não executado (sem firewalls conectados).

## 3. Chamadas da XML API confirmadas

| Finalidade | Chamada |
|---|---|
| Versão e modelo | `type=op&cmd=<show><system><info></info></system></show>` |
| Alterações pendentes | `type=op&cmd=<check><pending-changes></pending-changes></check>` (resultado `yes`/`no`) |
| Descartar o candidate | `type=op&cmd=<revert><config></config></revert>` |
| Snapshot nomeado | `type=op&cmd=<save><config><to>NOME.xml</to></config></save>` |
| Exportar a running | `type=export&category=configuration` |
| Importar arquivo de configuração | `type=import&category=configuration`, upload multipart no campo `file` |
| Carga parcial | `type=op&cmd=<load><config><partial><from>ARQ.xml</from><from-xpath>shared</from-xpath><to-xpath>/config/shared</to-xpath><mode>replace</mode></partial></config></load>` (`from-xpath` relativo, sem `/config`) |
| Ler o candidate | `type=config&action=get&xpath=/config` |
| Validate Changes | `type=op&cmd=<validate><full></full></validate>` (gera job do tipo `Validate`) |
| Validate Device Group Push | `type=commit&action=all&cmd=<commit-all><shared-policy><validate-only>yes</validate-only><device-group><entry name="DG"/></device-group></shared-policy></commit-all>` |
| Commit no Panorama | `type=commit&cmd=<commit><description>…</description></commit>` |
| Acompanhar job | `type=op&cmd=<show><jobs><id>N</id></jobs></show>` |

## 4. Armadilhas encontradas

1. **`<validate-only></validate-only>` vazio é recusado** (erro 17, "validate-only is invalid"). O
   guia da CLI 11.1 documenta `validate-only <yes|no>`. Corrigido no gerador (`50-validacao.md`).
2. **Sem firewall conectado, a validação de push não conclui nada**: cada firewall aparece como
   `not connected` e o job termina em `FAIL`.
3. **`type=config&action=show` devolve a running**, não o candidate. O candidate é lido com
   `action=get`.
4. **A leitura por `action=get` inclui `/config/predefined`** (conteúdo embutido do Panorama), que o
   export de configuração não traz. `validate --exported` passou a ignorar essa seção quando ela não
   existe no original.
5. **`type=op&cmd=<show><config><candidate>…`** foi recusado ("Request is not a valid XML").
6. **`type=export&category=configuration&from=<snapshot>`** devolveu a running, não o snapshot
   nomeado. A forma de exportar um snapshot nomeado pela API não foi confirmada; usar a interface
   web (Export named Panorama configuration snapshot) ou exportar a running logo depois de salvar.
7. **O backup completo traz a gerência da produção** (IP de gerência, gateway, HA, administradores,
   log collector). Carregá-lo inteiro no laboratório faria o laboratório assumir o IP da produção
   no commit e perder o acesso. Carregar só `shared`, `template`, `template-stack` e `device-group`.
8. **A carga parcial seção a seção emite avisos "Discarding"**: ao carregar `shared` antes de
   `device-group`, a validação intermediária reclama de regras dos DGs que referenciam objetos ainda
   ausentes. Depois de carregar todas as seções, o estado final ficou idêntico ao backup.
9. **A carga parcial gera UUIDs novos para todas as regras.** Por isso a linha de base do
   laboratório é a running exportada depois da carga (não o backup de produção), e o plano é
   recalculado sobre ela: a lista `operations` saiu idêntica à do plano aprovado.
10. **Laço de execução com `| grep` mascara falhas**: o código de saída passa a ser o do `grep`, e o
    laço seguiria para a fase seguinte depois de uma falha. Testar o código de saída do próprio
    `run-xmlapi.sh`.
11. **zsh interativo**: sem `setopt interactivecomments`, um comentário com parênteses na mesma
    linha de um comando gera "number expected". Instruções para o operador não devem ter
    comentários na linha dos comandos.

## 5. O que este teste não cobriu

- Push para firewalls e testes de política antes e depois (`80-testes-de-politica.cli`).
- Ensaio de rollback depois do commit (R2 e R3 do modelo da Etapa 9).
- Arquivos `.cli` (CLI do Panorama 11.1 continua não validada; usar a XML API).
- Comportamento exato do PAN-OS 11.1.13.
- Ganho de desempenho com conexão HTTPS reutilizada ou `action=multi-config` (não implementados).
