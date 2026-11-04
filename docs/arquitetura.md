# Decisões de arquitetura

Registro das decisões tomadas a partir da Etapa 1. Cada decisão referencia a seção do `CLAUDE.md` que a motiva.

## D1. Pacote `panorama_cleanup/` com módulos independentes (§9)

Os nove módulos da §9 existem como arquivos separados dentro de um pacote importável. Na Etapa 1 eles são apenas placeholders documentados, sem lógica. O pacote é executado com `python -m panorama_cleanup`, sem instalação, e o código é compatível com Python 3.9+ porque o ambiente atual é 3.9.6.

Módulos de infraestrutura criados na Etapa 1:

| Módulo | Responsabilidade |
|---|---|
| `config.py` | Lê `config/settings.json` e resolve os diretórios do projeto. Chaves ausentes ou desconhecidas geram erro, nunca um valor padrão silencioso. |
| `safety.py` | Guarda de escrita. Recusa gravar no arquivo de entrada, em `input/`, fora dos diretórios permitidos ou por cima de arquivo existente. Também confere o hash da entrada ao fim do run. |
| `run_context.py` | Identifica cada execução (`run_id`) e grava `manifest.json` com o SHA-256 da entrada. |
| `logging_setup.py` | Um log por execução em `logs/<run_id>.log`. |
| `environment.py` | Valida a versão do Python e as dependências do `requirements.txt`. |
| `errors.py` | Exceções da ferramenta (`ConfigError`, `SafetyViolation`). |
| `cli.py` | Interface dos cinco modos. |

## D2. `input/` é somente leitura para a ferramenta (§8, §14)

Toda escrita passa por `safety.ensure_writable_target`. Caminhos são resolvidos (`resolve()`) antes da checagem, o que bloqueia `..` e links simbólicos que apontem para `input/`. A configuração também é recusada se algum diretório de saída ficar dentro de `input/` ou o contiver.

## D3. Rastreabilidade por `run_id` (§13)

Formato: `AAAAMMDDTHHMMSSZ-<modo>-<8 hex do SHA-256 da entrada>`, sempre em UTC. Cada execução grava artefatos apenas em pastas próprias:

| Destino | Conteúdo |
|---|---|
| `output/reports/<run_id>/` | relatórios e `manifest.json` |
| `output/xml/<run_id>/` | XML transformado (somente modo APPLY) |
| `cli/<run_id>/` | comandos PAN-OS CLI / XML API gerados |
| `rollback/<run_id>/` | material de rollback |
| `logs/<run_id>.log` | log da execução |

Arquivos existentes nunca são sobrescritos.

## D4. Cinco modos sem encadeamento (§10)

Cada modo é um subcomando (`inventory`, `analyze`, `plan`, `apply`, `validate`) e nenhum chama o seguinte. Enquanto não implementados, encerram com código 3 sem gravar nada.

Códigos de saída: `0` ok, `1` erro, `2` uso inválido (argparse), `3` não implementado.

## D5. Configuração em JSON

O Python 3.9 não traz `tomllib`, e o JSON evita depender do PyYAML. Parâmetros específicos de versão do PAN-OS (por exemplo, limite de tamanho de nome) **não** foram colocados na configuração: serão definidos nas etapas seguintes, com base na documentação da versão identificada no backup (§16).

## D6. Leitura segura de XML (preparação para a Etapa 2)

`defusedxml` não protege o `lxml` (o módulo `defusedxml.lxml` está descontinuado). A leitura com `lxml` deverá usar um parser configurado explicitamente: `resolve_entities=False`, `no_network=True`, sem remover espaços em branco.

## D7. Backups fora do controle de versão

O `.gitignore` exclui `input/*` e todos os artefatos gerados, porque contêm configuração sensível dos firewalls. O projeto ainda não é um repositório git.

## D8. Leitura em duas passagens (Etapa 2)

O backup passa primeiro pelo `defusedxml` com `forbid_dtd`, `forbid_entities` e `forbid_external`. Só então é lido pelo `lxml` com parser restrito (D6). Qualquer DOCTYPE é recusado: backups do Panorama não têm DTD.

## D9. Estrutura inesperada interrompe a leitura

Mais de um `/config/devices/entry`, ausência de `version` ou raiz diferente de `<config>` geram `XmlReadError`. Problemas de hierarquia (DG sem entrada em `readonly`, pai inexistente, ciclo) não interrompem: o DG recebe pai `UNKNOWN` e um aviso, e as etapas seguintes não poderão tratá-lo automaticamente.

## D10. Registro de objeto

Cada objeto guarda escopo, tipo (caminho XML do contêiner, ex.: `profiles/virus`), nome, `value` (folhas `caminho=texto` na ordem do XML, sem `description`, `tag` e `disable-override`), `value_sha256`, membros, descrição, tags, XPath absoluto (no formato da XML API) e linha de origem. Atributos de elementos internos entram no caminho, para que entradas aninhadas não percam o nome.

## D11. Nada fica invisível

Contêineres com `<entry>` que não estão na lista de tipos conhecidos são inventariados com `recognized=no` e geram aviso. A contagem por contêiner é conferida contra um `count()` XPath direto. Qualquer divergência faz o modo terminar com erro.

## D12. Relatórios de inventário

Em `output/reports/<run_id>/`: `inventory.csv`, `inventory.xlsx` (abas Execução, Resumo, Device Groups, Inventário, Avisos), `inventory.html` (resumo; o detalhe fica no CSV/XLSX), `device-groups.csv`, `inventory-summary.csv`, `inventory-warnings.csv` e `manifest.json`, com o SHA-256 de cada saída. No XLSX, texto vindo do XML é sempre gravado como texto, nunca como fórmula.

## D13. Resolução de nomes por contexto (Etapa 3, revisada)

Evidências na documentação oficial (pesquisa de 03/11/2026):

- [Manage Precedence of Inherited Objects (Panorama 11.1)](https://docs.paloaltonetworks.com/panorama/11-1/panorama-admin/manage-firewalls/manage-device-groups/manage-precedence-of-inherited-objects): por padrão, as regras de um DG descendente usam os valores dos objetos desse DG, não os herdados de ancestrais ou de Shared.
- [KB kA10g000000ClvoCAC](https://knowledgebase.paloaltonetworks.com/KCSArticleDetail?id=kA10g000000ClvoCAC): com o mesmo nome em Shared e no DG, só a cópia do DG é enviada ao firewall. A opção "Objects defined in ancestors will take higher precedence" vem desativada.
- [Device Group Push to a Multi-VSYS Firewall](https://docs.paloaltonetworks.com/panorama/administration/manage-firewalls/manage-device-groups/device-group-push-to-a-multi-vsys-firewall): se existem as duas cópias, vale a do DG ("Panorama location").

Regra aplicada: toda definição (regra ou objeto) no escopo S vale em S e em cada DG descendente. Em cada contexto, o nome é resolvido do contexto para cima, e o mais próximo vence. Isso inclui os membros de grupos de Shared: nos firewalls de um DG que tenha um objeto de mesmo nome, o grupo de Shared contém o valor do DG (`resolution=override`, confirmada).

**Inferência:** nenhuma página descreve membros de grupo explicitamente. A regra decorre de só existir a cópia do DG no firewall. Recomenda-se confirmar em um firewall de um DG afetado antes da execução (Etapa 9).

Se o backup contiver um elemento de precedência com valor diferente de `no`, a análise é interrompida (`AnalysisError`).

## D17. Critério de transparência (requisito do usuário, 03/11/2026)

A limpeza deve ser transparente para os firewalls. Não se alteram regras nem os valores de address, service etc. que elas usam. Para cada contexto de DG (ou seja, para cada firewall), o conteúdo efetivo, totalmente expandido, de cada referência de regra antes e depois tem de ser idêntico. Esse é o critério de equivalência das Etapas 4, 5 e 8.

O relatório `context-dependent-objects.csv` lista os grupos cujo valor efetivo já muda conforme o DG. Eles são o principal risco de uma centralização ingênua: renomear e mover o membro sobreposto mudaria o conteúdo do grupo nos firewalls daquele DG.

## D14. Mapeamento de campos de regra

Os campos de regra que referenciam objetos estão em `RULE_FIELD_TYPES`, e os que comprovadamente não referenciam (zonas, interfaces, ações, portas, flags, `target`) estão em `RULE_NON_REFERENCE`. Um campo fora das duas listas é tratado por coincidência de nome, como referência possível, e gera aviso. No backup, o único campo nessa situação é `pbf: action/forward/nexthop/ip-address`.

Também usam coincidência de nome, sempre como referência possível: o conteúdo de Security Profiles, Log Forwarding Profiles, aplicações customizadas, EDLs, tipos não reconhecidos e templates.

## D15. Classificação

- **USED:** alcançável a partir de regra por referências confirmadas. Regra desabilitada conta como referência e é sinalizada em `rule_disabled`.
- **UNKNOWN:** alcançável só por algum caminho com referência possível, tipo não reconhecido ou hierarquia desconhecida.
- **UNUSED:** nenhum caminho a partir de regras ou templates. `usage_reason` diferencia "nenhuma referência" de "referenciado apenas por objetos não utilizados", e `member_of` lista os grupos que o contêm.
- **DUPLICATE / CONFLICT:** mesmo nome na mesma família de tipos (address+address-group, service+service-group, application/-group/-filter) em mais de um lugar. É DUPLICATE quando todos têm o mesmo tipo e o mesmo valor efetivo. Valores ou tipos diferentes resultam em CONFLICT, com `value_cluster` agrupando os iguais.
- **POSSIBLE_DUPLICATE:** nomes diferentes com o mesmo valor efetivo (address, address-group, service, service-group, application-group). Nunca é consolidado automaticamente.

O valor efetivo de grupos resolve cada membro no escopo do grupo, de forma recursiva e sem considerar a ordem dos membros. A normalização é mínima: IP sem máscara = /32 ou /128, FQDN sem diferença de caixa. Qualquer outra diferença literal é tratada como valor diferente, porque o erro seguro é renomear demais, nunca consolidar o que é diferente.

## D16. ANALYZE exige o inventário da Etapa 2

`analyze --inventory output/reports/<run_id>` verifica que:
- o run é um INVENTORY concluído com sucesso;
- foi gerado a partir do mesmo arquivo (sha256);
- o `inventory.csv` não foi alterado (hash do manifest);
- o inventário recalculado é idêntico, com os mesmos XPath e valores na mesma ordem.

Qualquer divergência interrompe a análise.

## D18. Nomes gerados (Etapa 4)

Formato `NOME_ORIGINAL_DEVICEGROUP` (CLAUDE.md §6). Limite de 63 caracteres, conforme [Create an Address Object (PAN-OS 11.1)](https://docs.paloaltonetworks.com/pan-os/11-1/pan-os-admin/policy/use-address-object-to-represent-ip-addresses/create-an-address-object) e [Objects > Address Groups](https://docs.paloaltonetworks.com/ngfw/help/11-1/objects/objects-address-groups). O limite é aplicado a todos os tipos renomeáveis. O sufixo usa só caracteres permitidos (letras, números, `_`, `-`). O nome original é mantido como está, já que o PAN-OS o aceitou.

O nome é recusado (PENDING_DECISION) se passar do limite ou se já existir na mesma família de nomes (address/address-group, service/service-group, application/-group/-filter) em qualquer escopo, ou no próprio plano. Nada é truncado. No backup, o maior nome gerado tem 53 caracteres.

## D19. Plano provado por simulação

O plano (`planner.py`) propõe uma decisão por objeto. `effective_model.py` simula 4 estados intermediários, na ordem de execução:

1. cópias criadas em Shared;
2. referências atualizadas;
3. cópias locais excluídas;
4. objetos não usados excluídos.

Em cada estado, cada referência de regra é expandida até os valores finais em cada contexto de DG (cada firewall) e comparada com o original. Qualquer diferença rebaixa as decisões envolvidas para KEEP_LOCAL e repete o processo até não sobrar diferença. Também se exige que todo objeto continue resolvendo seus membros e tags no próprio escopo: um objeto em Shared só pode referenciar objetos de Shared.

Regras de decisão:
- **CONSOLIDATE:** só quando a cópia e o objeto mantido têm o mesmo tipo, as mesmas tags e a mesma expansão em cada contexto onde a cópia vale. A comparação é feita no contexto do consumidor, não no escopo de definição.
- **RENAME_MOVE:** só quando toda referência ao objeto é confirmada e vem do próprio DG ou de descendentes. Se uma regra ou objeto de Shared usa o objeto por sobreposição, ele fica no DG (KEEP_LOCAL), porque renomeá-lo mudaria o valor nos firewalls.
- **Tags:** nunca são renomeadas, alteradas, adicionadas ou removidas em objetos ou regras (requisito do usuário). Tags em conflito ficam no DG. `check_tag_invariant` recusa o plano se alguma operação tocar em tag.
- **Exclusão:** um objeto de Shared marcado como não usado que passa a representar cópias unificadas não é excluído.

## D20. Descrições em consolidações

Uma cópia unificada perde a descrição local; vale a do objeto mantido. Descrição não altera comportamento do firewall, mas é perda de informação. Cada caso está em `deleted-objects.csv` (coluna `notes`) e depende de aceite explícito.

## D21. Aplicação do plano (Etapa 5)

- **Plano aceito:** `apply --plan` só aceita um run PLAN com status ok, gerado sobre o mesmo arquivo (sha256), com os hashes de `plan.json`, `reference-updates.csv`, `renamed-objects.csv` e `deleted-objects.csv` iguais aos do manifest e com 0 diferenças na verificação. Além disso, o plano recalculado sobre o XML atual tem de ser idêntico ao aprovado.
- **Fases aplicadas:** só as fases 1 a 3. A fase 4 (exclusão de não utilizados) é da Etapa 6.
- **Pré-condições:** cada operação verifica a sua (elemento único no XPath, nome esperado, membro presente, nome livre em Shared) e interrompe com `ApplyError` se ela falhar.
- **Gravação:** sai byte a byte igual ao original quando não há alteração (mesma declaração, ASCII com referências `&#xHH;`). Assim, o `diff` do sistema mostra apenas as mudanças planejadas. Entradas copiadas para Shared são reindentadas no nível de destino.
- **Validação:** o arquivo gravado é relido do disco e passa por 11 verificações (`xml_validator.py`, `policy_comparator.py`). Uma falha marca o run como error e o XML não deve ser usado.

## D22. Remoção de não utilizados (Etapa 6)

`apply --stage cleanup --plan <plano> --from-run <run APPLY convert>` aplica só a fase 4 sobre o XML convertido.

**Pré-condições do run de conversão:** mesmo backup e mesmo plano, 11/11 verificações aprovadas e XML convertido íntegro (sha256 do manifest).

**Prova exigida para cada candidato** (`cleanup.py`):
1. existe no mesmo XPath, com o mesmo valor do original;
2. a reanálise do XML convertido o classifica como UNUSED, com tipo reconhecido;
3. toda referência de entrada vem de objeto também removido (reaplicado até estabilizar);
4. o nome não aparece como texto fora de Shared e dos Device Groups (templates, configuração do Panorama, mgt-config; `readonly` não entra porque só tem IDs internos).

**Validações:** são 14 verificações. As 11 da Etapa 5 (convertido x final) mais 3 de ponta a ponta (original x final): políticas, valor efetivo e nenhum objeto USED ou UNKNOWN excluído.

A análise de uso não separa o uso por DG. Um objeto alcançado só por um grupo de Shared usado em outro DG fica USED e é mantido. É uma limitação conservadora: mantém mais do que o necessário, nunca exclui a mais.

## D23. Seção readonly e forma de execução

`/config/readonly` guarda IDs internos que o Panorama mantém (por exemplo, um por address-group de Shared e de DG). O XML transformado não edita essa seção, porque não há documentação de como os IDs são gerados. Depois da conversão:
- 78 address-groups novos em Shared ficam sem ID;
- os IDs dos grupos removidos dos DGs e dos 27 grupos de Shared excluídos ficam órfãos.

Conclusão: o XML final é o **modelo validado do estado de destino**, não um arquivo para importar diretamente. A execução recomendada é pelos comandos da Etapa 7, com os quais o Panorama mantém `readonly` sozinho. Importar o XML diretamente só seria aceitável depois de testar em laboratório (Etapa 9).

## D24. Geração de comandos (Etapa 7)

`plan --stage commands --plan <plano> --convert-run <apply> --cleanup-run <apply cleanup>` gera os arquivos em `cli/<run_id>/` e não executa nada. Os runs informados precisam estar íntegros e encadeados: mesmo backup, mesmo plano e hashes conferidos.

- **Forma principal: Panorama XML API** (`type=config`):
  - criação: `action=set` no contêiner, com a entry copiada do XML convertido;
  - referências: `action=edit` na lista de membros do campo, preservando a ordem;
  - exclusões: `action=delete`.

  Se o contêiner de destino não existir, o `set` vai para o ancestral existente com o contêiner dentro do elemento (merge), sem supor criação implícita de nós.
- **Prova:** `simulate_xmlapi` aplica as operações sobre o original. O resultado tem de ser idêntico (C14N) ao XML convertido, nas fases 1 a 3, e ao final, nas fases 1 a 4.
- **CLI:** linhas `set`/`delete` derivadas da hierarquia do XML, no formato de `set cli config-output-format set`.
  - A atualização de referência apaga e recria a lista do campo na ordem do XML final.
  - Valores com quebra de linha, aspas, colchetes, barra invertida ou caracteres fora do ASCII não geram CLI. Não há documentação de escape para esses casos, então a operação fica só na XML API.
  - Não há página oficial com a sintaxe por objeto em 11.1; a validação em laboratório é obrigatória.
- **`run-xmlapi.sh`:** executa um `.tsv` por vez, para no primeiro erro e registra as respostas. Só envia algo com `CONFIRM_EXECUTE=yes`. A chave vai no cabeçalho `X-PAN-KEY`, e a verificação do certificado TLS fica ativa por padrão.
- **Separação:** as exclusões de tags não utilizadas ficam em arquivo próprio (`41-...`), pendentes de decisão.
- **Commit e push:** só documentados (`60-commit-e-push.md`), nunca nos arquivos executáveis. Isso é verificado na geração.

## D25. Conferência da CLI contra o guia oficial

`cli_reference.py` lê as linhas `set ...` da hierarquia de comandos do PAN-OS CLI Quick Start 11.1 (`docs/referencias/pan-os-11.1/ngfw-cli.md`), reunindo as quebras de linha do PDF. Depois compara cada linha gerada, palavra por palavra:
- palavra-chave: tem de ser idêntica;
- variável `<...>`: aceita qualquer valor, mas uma lista de opções `<a|b>` restringe;
- lista `[ ... ]`: obrigatória onde o guia a define;
- `delete`: aceito quando o caminho é prefixo de uma linha `set` documentada.

**Corrigido na Etapa 9:** a conferência é feita **por seção de versão** (`check_by_version`). O guia tem hierarquias 11.1, 11.2, 12.1 e 12.2; as de 11.x são de firewall, e só as de 12.x incluem Panorama (`device-group`). Uma linha só é considerada validada para a versão do backup se constar na seção dessa versão; caso contrário, fica PENDENTE (laboratório). A verificação "nenhuma linha fora de qualquer hierarquia" continua obrigatória.

O guia também corrigiu e complementou os arquivos gerados:
- o comando de saída é `set cli config-output-format set` (antes estava escrito, por erro, `config-output-mode`);
- `set cli scripting-mode on` é necessário para colar mais de ~20 linhas;
- `delete` pela CLI não verifica dependências;
- o backup usa `save config to <nome>` e `scp export configuration from <nome>.xml to <usuario@host:caminho>`.

## D26. Validação final independente (Etapa 8)

`validate --input <original> --modified <final> --plan --convert-run --cleanup-run` parte só dos dois XMLs e das alterações autorizadas registradas:
- decisões do `plan.json`;
- renomeações aplicadas em `reference-updates.csv` da conversão;
- exclusões confirmadas em `deleted-objects.csv` da Etapa 6.

O XML informado precisa ter o sha256 do run de cleanup. As 24 verificações cobrem seis áreas:

| Área | O que é verificado |
|---|---|
| Estrutura | Raiz e versão, seções de primeiro nível, Device Groups, e todas as seções não relacionadas (templates, template stacks, deviceconfig, log-collector, panorama, mgt-config, readonly, partes não-objeto de Shared e de cada DG) idênticas em C14N. |
| Políticas | Mesmas rulebases, quantidade, ordem e UUIDs das regras. Comparação campo a campo: só são aceitas as renomeações de membro autorizadas, e campos que não referenciam objetos (ação, zonas, flags, alvo, tags, descrição) não podem mudar. |
| Objetos | Cada objeto do original tem exatamente o destino do plano, com conteúdo idêntico. Nenhum objeto do final fica sem origem autorizada. |
| Referências | Nenhuma referência nova sem resolução ou ambígua. Valor efetivo idêntico por firewall. Membros e tags resolvem no próprio escopo. Shared referencia só Shared. |
| Tags | Todas presentes e idênticas no mesmo escopo, nenhuma nova, tags dos objetos e campos tag/group-tag das regras inalterados. |
| Centralização | Todo objeto restante em DG tem motivo documentado. |

Qualquer falha resulta em NÃO APROVADO, e o modo termina com código 1.

## D27. Homologação (Etapa 9)

- **Testes de política:** `policy_tests.py` gera `test security-policy-match` / `test nat-policy-match` para as regras com referência renomeada e para até 40 regras por DG que usam objetos consolidados ou movidos, nos contextos de firewall de cada DG. O método é comparar a saída antes e depois, sem prever resultado. A sintaxe é conferida contra a hierarquia operacional 11.1 (firewall), aceitando subconjuntos dos parâmetros documentados na ordem documentada.
- **`validate --exported`:** valida um XML exportado do Panorama após a implantação. Dispensa o sha256 do XML gerado e exclui da comparação apenas `/config/readonly`, cujos IDs o Panorama mantém (D23). Todo o resto segue as 24 verificações.
