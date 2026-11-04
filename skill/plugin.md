---
name: panorama-cleanup
description: {{DESCRIPTION}}
---

<!-- Gerado por scripts/build_skill.py a partir de skill/. Não edite este arquivo: edite skill/ e rode o build. -->

# Saneamento de objetos do Panorama (panorama-cleanup {{VERSION}})

Conduz a ferramenta `panorama-cleanup` sobre um backup XML do Panorama, etapa por etapa, com aprovação explícita entre etapas. Prioridade absoluta: o comportamento das políticas de cada firewall não pode mudar. O valor efetivo de cada objeto, resolvido pela herança Shared > DG ancestral > DG, deve ser idêntico antes e depois.

{{REGRAS}}

## Preparação (primeira vez na máquina)

Confira com `panorama-cleanup --version`. Se o comando não existir, peça ao usuário para instalar:

```sh
git clone https://github.com/diogodealencar-del/panorama-cleanup.git ~/panorama-cleanup
pipx install -e ~/panorama-cleanup     # sempre editável: lê config/ e docs/referencias/ do clone
```

O guia oficial da versão precisa estar em `<clone>/docs/referencias/pan-os-X.Y/ngfw-cli.md`. O guia não vai no git: é obtido em docs.paloaltonetworks.com (PAN-OS CLI Quick Start da versão, convertido para texto).

## Novo Panorama / novo backup

1. **Confidencialidade.** O backup contém a configuração completa e hashes de senha. Pergunte se o contrato permite processá-lo nesta máquina. Nunca envie o arquivo a serviços externos nem o coloque em git.
2. **Pasta de trabalho por cliente/Panorama**, fora do clone da ferramenta. Nunca rode sem `--workdir`: as saídas cairiam na pasta do clone e misturariam clientes.

```sh
W=~/panorama-trabalho/<cliente>-<panorama>
mkdir -p "$W/input"
cp <export>.xml "$W/input/<cliente>-<panorama>-<AAAAMMDD>.xml"
B="$W/input/<cliente>-<panorama>-<AAAAMMDD>.xml"
shasum -a 256 "$B"          # registrar
```

{{ETAPAS}}
