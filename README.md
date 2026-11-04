# Panorama Configuration Analyzer & Object Cleanup

Ferramenta de análise, auditoria e saneamento de backups XML do Palo Alto Networks Panorama: inventário de objetos, análise de dependências, identificação de objetos não utilizados, resolução de conflitos de nome, centralização em Shared e geração dos comandos para aplicar as alterações.

As regras do projeto estão no [`CLAUDE.md`](CLAUDE.md). A prioridade absoluta é preservar a integridade das configurações e das políticas.

## Status

Etapas 1 a 8 implementadas e validadas em Panorama PAN-OS 11.1 (incluindo commit e push em ambiente real). Todos os modos implementados: `inventory`, `analyze`, `plan`, `apply` e `validate`. Implantação pela Panorama XML API. Procedimento: [`docs/procedimento-uso-da-ferramenta.md`](docs/procedimento-uso-da-ferramenta.md); modelo de homologação, implantação e rollback: [`docs/modelo-etapa-09.md`](docs/modelo-etapa-09.md).

## Instalação (você e a equipe)

```sh
git clone <url-do-repositório> ~/panorama-cleanup
pipx install -e ~/panorama-cleanup          # comando `panorama-cleanup` em qualquer pasta
```

Sempre instale em modo editável (`-e`): `config/settings.json` e `docs/referencias/` são lidos do clone. O guia oficial da versão (`docs/referencias/pan-os-X.Y/ngfw-cli.md`) não vai no git: copie-o para o clone (ver `docs/referencias/README.md`).

Skill do Claude Code (plugin `panorama-cleanup` do marketplace `panorama-tools`, neste repositório):

```sh
claude plugin marketplace add ~/panorama-cleanup     # ou a URL git do repositório
claude plugin install panorama-cleanup@panorama-tools
```

Depois disso, em qualquer pasta, basta pedir ao Claude Code algo como "analise o backup do Panorama em ~/Downloads/running-config.xml". A skill conduz as etapas com aprovação entre elas.

Desenvolvimento: `python3 -m venv .venv && .venv/bin/pip install -e '.[dev]' && .venv/bin/pytest`.

## Uso

Uma pasta de trabalho por cliente/Panorama, fora do clone. A ferramenta cria `input/`, `output/`, `cli/`, `rollback/` e `logs/` dentro dela:

```sh
W=~/panorama-trabalho/<cliente>; mkdir -p "$W/input"; cp running-config.xml "$W/input/"
export PANORAMA_CLEANUP_HOME="$W"     # ou --workdir "$W" em cada comando
panorama-cleanup inventory --input "$W/input/running-config.xml"
```

Sem `--workdir` nem `PANORAMA_CLEANUP_HOME`, as saídas vão para a pasta do clone (comportamento anterior).

Sequência completa (também vale `python -m panorama_cleanup`):

```sh
panorama-cleanup inventory --input input/<backup>.xml
panorama-cleanup analyze --input input/<backup>.xml --inventory output/reports/<run_id-do-inventory>
panorama-cleanup plan --input input/<backup>.xml --analysis output/reports/<run_id-do-analyze>
panorama-cleanup apply --input input/<backup>.xml --plan output/reports/<run_id-do-plan-aprovado>
panorama-cleanup apply --stage cleanup --keep-type tag --input input/<backup>.xml --plan output/reports/<run_id-do-plan> --from-run output/reports/<run_id-do-apply>
panorama-cleanup plan --stage commands --input input/<backup>.xml --plan <run plan> --convert-run <run apply> --cleanup-run <run cleanup>
```

| Modo | Escreve? | Função |
|---|---|---|
| `inventory` | só relatórios | Inventário dos objetos |
| `analyze` | só relatórios | Utilização, dependências, duplicidades e conflitos |
| `plan` | só relatórios | Plano de alterações e comandos propostos |
| `apply` | cópia do XML | Aplica o plano em uma cópia; nunca altera o original |
| `validate` | só relatórios | Compara original e modificado |

Nenhum modo executa o seguinte automaticamente. A ferramenta não acessa o Panorama nem faz commit ou push.

## Estrutura

```
panorama_cleanup/   código-fonte (um módulo por responsabilidade)
config/             settings.json
input/              backups XML originais (somente leitura; fora do git)
output/reports/     relatórios por execução: <run_id>/
output/xml/         XML transformado por execução (modo apply)
cli/                comandos PAN-OS CLI / XML API gerados, por execução
rollback/           material de rollback, por execução
logs/               um log por execução
scripts/            utilitários (check_env.py)
tests/              testes automatizados e fixtures XML
docs/               arquitetura e andamento das etapas
```

## Testes

```sh
.venv/bin/pytest
```

```sh
python -m panorama_cleanup validate --input input/<backup>.xml --modified output/xml/<run cleanup>/<backup>.final.xml \
  --plan <run plan> --convert-run <run apply> --cleanup-run <run cleanup>
```
