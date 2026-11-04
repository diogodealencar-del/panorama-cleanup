# Fixtures XML

Arquivos sintéticos. Nenhum é derivado de configuração real.

| Arquivo | Cenário |
|---|---|
| `panorama_basic.xml` | Shared e 3 Device Groups (DG-BH-FILIAL filho de DG-BH), todos os tipos de objeto reconhecidos, contêiner não reconhecido (`admin-role`), mesmo nome com valores diferentes (`SERVER`), nomes com espaço, `/` e apóstrofo, descrição que parece fórmula, regras que não devem entrar no inventário. |

Cenários de hierarquia inválida e XML inseguro são montados dentro dos próprios testes.
| `panorama_deps.xml` | Etapa 3. Referências diretas e indiretas (grupos aninhados, service-group, profile-group, address group dinâmico por tag), sombreamento de Shared por DG, herança em DG neto, nome visível só em descendente, irmão não visível, referência ambígua (address e address-group com mesmo nome), campo de regra não mapeado (next hop de PBF), regra desabilitada, NAT, application-override, IP literal, App-ID predefinida, casos A/B/C de duplicidade, conflito entre tipos, grupos com mesmos nomes de membros e valores efetivos diferentes. |
