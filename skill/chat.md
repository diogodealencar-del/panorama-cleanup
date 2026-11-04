---
name: panorama-cleanup
description: {{DESCRIPTION}}
---

# Saneamento de objetos do Panorama (panorama-cleanup {{VERSION}})

Conduz a ferramenta `panorama-cleanup`, que vem dentro desta skill, sobre um backup XML do Panorama anexado à conversa. O trabalho é feito etapa por etapa, com aprovação explícita entre etapas. Prioridade absoluta: o comportamento das políticas de cada firewall não pode mudar. O valor efetivo de cada objeto, resolvido pela herança Shared > DG ancestral > DG, deve ser idêntico antes e depois.

{{REGRAS}}

## Antes de tudo: confidencialidade

O backup completo contém hashes de senha, chaves e certificados. **Antes de abrir o arquivo**, pergunte ao usuário se o contrato do cliente permite processá-lo aqui. Se não permitir, não prossiga e indique a alternativa: o mesmo fluxo no Claude Code, com o plugin `panorama-cleanup` do repositório da equipe, em que nada sai da máquina.

Não aceite backups recortados ou editados à mão (por exemplo, sem templates): referências fora dos trechos mantidos deixariam de ser vistas e objetos em uso poderiam ser classificados como não utilizados. Use sempre o export completo do Panorama.

## Ambiente

`SKILL_DIR` é a pasta onde está este `SKILL.md` (por exemplo, `/mnt/skills/user/panorama-cleanup`). Na primeira mensagem, confira o ambiente:

```sh
python3 "$SKILL_DIR/scripts/run.py" --check
```

Se a linha `guias de CLI` mostrar `nenhum`, este é o pacote público, sem os guias da Palo Alto. Avise o usuário já na Etapa 2: as Etapas 2 a 6 e 8 funcionam, mas a Etapa 7 (comandos) vai parar pedindo o guia. Para ela, é preciso um pacote gerado com o guia da versão (`python scripts/build_skill.py` num clone do repositório que tenha `docs/referencias/pan-os-X.Y/ngfw-cli.md`).

`lxml` e `openpyxl` vêm do ambiente; o resto vem na skill. Se o resultado não for `AMBIENTE OK` e faltar uma dessas bibliotecas, tente uma vez `pip install --quiet lxml openpyxl` e rode `--check` de novo. Se continuar falhando, mostre a saída ao usuário e pare. Não tente fazer a análise sem a ferramenta.

## Novo backup

1. Localize o XML anexado (normalmente em `/mnt/user-data/uploads/`). Nunca o altere nem o mova.
2. Crie a pasta de trabalho dentro da pasta de saída, para o usuário poder baixar os relatórios:

```sh
B="/mnt/user-data/uploads/<arquivo>.xml"
W="/mnt/user-data/outputs/panorama-$(basename "$B" .xml)"
mkdir -p "$W"
sha256sum "$B"          # informe ao usuário para registro
```

3. Grave o `session.sh` (seção abaixo) e siga as etapas.
4. Se, em mensagens seguintes, `$W` não existir mais (ambiente reiniciado), avise o usuário e refaça as etapas já aprovadas a partir da 2. A ferramenta é determinística para o mesmo backup e o mesmo plano; confira que as contagens batem com as aprovadas antes de continuar.

## Como apresentar cada etapa

- Leia os CSVs de resumo da etapa e mostre os números principais numa tabela curta, mais os itens de "Revisar antes de aprovar".
- Indique os arquivos para download (`.xlsx` e `.html` primeiro).
- Termine perguntando se aprova a etapa. Só continue com aprovação explícita.

{{ETAPAS}}

## Comandos e homologação (Etapas 7 a 9)

- Os arquivos para o Panorama ficam em `$W/cli/<run plan-commands>/`. Gere um `.zip` dessa pasta para o usuário baixar e diga para começar pelo `00-LEIA-ME.md`.
- O `run-xmlapi.sh` roda **na máquina do usuário**, com acesso ao Panorama. Daqui nada é enviado ao Panorama.
- Para validar o laboratório ou a produção, peça o candidate/running exportado do Panorama e rode `validate --exported` sobre ele.
- O modelo de procedimento de homologação, implantação e rollback está em `$SKILL_DIR/docs/modelo-etapa-09.md`. Copie-o para `$W/` e preencha com os dados da execução.
