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
