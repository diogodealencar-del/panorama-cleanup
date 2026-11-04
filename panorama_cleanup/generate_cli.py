"""Geração de comandos para reproduzir no Panorama as operações validadas (Etapa 7).

Fonte: o plano aprovado (fases 1-3), as exclusões confirmadas na Etapa 6 (fase 4)
e os XMLs convertido e final, já validados. Nada é executado aqui.

Duas formas para cada operação, na mesma ordem:

- Panorama XML API (principal): type=config com action=set (cria a entry no
  contêiner), action=edit (substitui a lista de membros de um campo, preservando a
  ordem do XML final) e action=delete. XPaths e elementos vêm literalmente do XML.
- PAN-OS CLI (modo configure): linhas set/delete derivadas da hierarquia do XML, no
  mesmo formato exibido por "set cli config-output-format set". Valores que a CLI não
  representa com segurança (quebra de linha, aspas, colchetes, barra invertida,
  caracteres fora do ASCII) não geram linha de CLI; a operação fica só na XML API.

simulate_xmlapi() aplica as operações da XML API sobre o XML original e o resultado
precisa ser idêntico (C14N) ao XML final validado.
"""

from __future__ import annotations

import copy
import re
from dataclasses import dataclass

from lxml import etree

from panorama_cleanup.policy_comparator import canonical

SAFE_TOKEN = re.compile(r"^[A-Za-z0-9_.:/-]+$")
UNSAFE_CHARS = re.compile(r'[\n\r\t"\[\]\\]|[^\x20-\x7e]')
_STEP = re.compile(r"""^([\w-]+)(?:\[@name=(?:'([^']*)'|"([^"]*)")\])?$""")


@dataclass(frozen=True)
class ApiOp:
    seq: int
    phase: int
    operation: str          # CREATE_SHARED | UPDATE_RULE_REFERENCE | UPDATE_OBJECT_REFERENCE
                            # | DELETE_LOCAL_COPY | DELETE_UNUSED
    action: str             # set | edit | delete
    xpath: str
    element: str            # XML compacto (vazio para delete)
    target: str             # objeto ou regra afetada (para leitura humana)
    detail: str


class CommandError(Exception):
    """Operação não pôde ser convertida em comando."""


# --- XML API ---------------------------------------------------------------------

def compact(el) -> str:
    """Elemento em uma linha, sem espaços de formatação (para o parâmetro element)."""
    c = copy.deepcopy(el)
    for x in c.iter():
        if x.text is not None and not x.text.strip():
            x.text = None
        if x.tail is not None and not x.tail.strip():
            x.tail = None
    c.tail = None
    return etree.tostring(c, encoding="unicode")


def build_api_ops(operations, cleanup_deletions, converted, final, original=None) -> list:
    """Operações da XML API na ordem do plano (fases 1-3) e da Etapa 6 (fase 4).

    original: XML de partida. Se o contêiner de destino de uma criação ainda não existir,
    o set é feito no ancestral existente com o contêiner dentro do elemento (o set faz
    merge no nó indicado; não se supõe criação implícita de nós intermediários).
    """
    ops, edited = [], set()
    existing = set()
    if original is not None:
        existing = {original.getpath(e) for e in original.iter() if isinstance(e.tag, str)}
    for op in sorted(operations, key=lambda o: int(o["seq"])):
        phase, kind = int(op["phase"]), op["operation"]
        if phase not in (1, 2, 3):
            continue
        if kind == "CREATE_SHARED":
            container = f"/config/shared/{op['object_type']}"
            xpath = f"{container}/entry[@name={_q(op['new_name'])}]"
            found = converted.xpath(xpath)
            if len(found) != 1:
                raise CommandError(f"Objeto criado não encontrado no XML convertido: {xpath}")
            target, element = container, compact(found[0])
            if original is not None:
                steps = op["object_type"].split("/")
                while steps and f"/config/shared/{'/'.join(steps)}" not in existing:
                    element = f"<{steps[-1]}>{element}</{steps[-1]}>"
                    steps = steps[:-1]
                    target = "/config/shared" + ("/" + "/".join(steps) if steps else "")
                existing.add(container)
            ops.append(ApiOp(int(op["seq"]), 1, kind, "set", target, element,
                             f"shared:{op['new_name']}",
                             f"cria em Shared (origem {op['scope']}:{op['name']})"))
        elif kind in ("UPDATE_RULE_REFERENCE", "UPDATE_OBJECT_REFERENCE"):
            xpath = f"{op['xpath']}/{op['field']}"
            if xpath in edited:
                continue
            found = converted.xpath(xpath)
            if len(found) != 1:
                raise CommandError(f"Campo não encontrado no XML convertido: {xpath}")
            edited.add(xpath)
            renames = [f"{o['name']} -> {o['new_name']}" for o in operations
                       if o["operation"] == kind and o["xpath"] == op["xpath"] and o["field"] == op["field"]]
            ops.append(ApiOp(int(op["seq"]), 2, kind, "edit", xpath, compact(found[0]),
                             op["source"] or op["xpath"],
                             f"campo {op['field']}: " + "; ".join(renames)))
        elif kind == "DELETE_LOCAL_COPY":
            ops.append(ApiOp(int(op["seq"]), 3, kind, "delete", op["xpath"], "",
                             f"{op['scope']}:{op['name']}",
                             f"remove cópia local; passa a valer shared:{op['new_name']}"))
        else:
            raise CommandError(f"Operação inesperada na fase {phase}: {kind}")
    for d in sorted(cleanup_deletions, key=lambda d: int(d["seq"])):
        ops.append(ApiOp(int(d["seq"]), 4, "DELETE_UNUSED", "delete", d["xpath"], "",
                         f"{d['scope']}:{d['name']}", f"remove {d['object_type']} não utilizado"))
    return ops


def simulate_xmlapi(tree, api_ops) -> etree._ElementTree:
    """Semântica documentada: set faz merge no XPath, edit substitui o nó, delete remove."""
    work = copy.deepcopy(tree)
    for op in api_ops:
        if op.action == "set":
            (node,) = work.xpath(op.xpath)
            _merge(node, etree.fromstring(op.element))
        elif op.action == "edit":
            (node,) = work.xpath(op.xpath)
            node.getparent().replace(node, etree.fromstring(op.element))
        elif op.action == "delete":
            (node,) = work.xpath(op.xpath)
            node.getparent().remove(node)
    return work


def _merge(node, element) -> None:
    """Merge de set: entry nova é acrescentada; contêiner existente recebe os filhos."""
    if element.tag != "entry":
        current = node.find(element.tag)
        if current is None:
            node.append(element)
            return
        for child in list(element):
            _merge(current, child)
        return
    if node.xpath("entry[@name=$n]", n=element.get("name")):
        raise CommandError(f"set sobre entry existente: {element.get('name')}")
    node.append(element)


def same_config(a, b) -> bool:
    return canonical(a.getroot()) == canonical(b.getroot())


# --- PAN-OS CLI --------------------------------------------------------------------

def xpath_tokens(xpath: str) -> list:
    """Tokens de CLI a partir do XPath (formato XML API)."""
    steps = _split_steps(xpath)
    if steps[0] != ("config", None):
        raise CommandError(f"XPath fora de /config: {xpath}")
    steps = steps[1:]
    tokens = []
    if steps[:1] == [("shared", None)]:
        tokens, steps = ["shared"], steps[1:]
    elif (len(steps) >= 4 and steps[0] == ("devices", None) and steps[1][0] == "entry"
          and steps[2] == ("device-group", None) and steps[3][0] == "entry"):
        tokens, steps = ["device-group", steps[3][1]], steps[4:]
    else:
        raise CommandError(f"Escopo não suportado: {xpath}")
    for tag, name in steps:
        tokens.append(name if tag == "entry" else tag)
    return tokens


def _split_steps(xpath: str) -> list:
    parts, buf, quote = [], "", None
    for ch in xpath.strip("/"):
        if quote:
            buf += ch
            if ch == quote:
                quote = None
        elif ch in "'\"":
            quote = ch
            buf += ch
        elif ch == "/":
            parts.append(buf)
            buf = ""
        else:
            buf += ch
    parts.append(buf)
    steps = []
    for p in parts:
        m = _STEP.match(p)
        if not m:
            raise CommandError(f"Passo de XPath não suportado: {p}")
        steps.append((m.group(1), m.group(2) if m.group(2) is not None else m.group(3)))
    return steps


def cli_safe(value: str) -> bool:
    return not UNSAFE_CHARS.search(value)


def token(value: str) -> str:
    if not cli_safe(value):
        raise CommandError(f"Valor não representável com segurança na CLI: {value!r}")
    return value if SAFE_TOKEN.match(value) else f'"{value}"'


def element_set_lines(prefix: list, el) -> list:
    """Linhas 'set' para os filhos de el, sob o prefixo (formato config-output-format set)."""
    lines = []
    children = [c for c in el if isinstance(c.tag, str)]
    if not children:
        return [prefix]
    for child in children:
        lines += _lines(prefix, child)
    return lines


def _lines(prefix, el) -> list:
    children = [c for c in el if isinstance(c.tag, str)]
    text = (el.text or "").strip()
    if not children:
        return [prefix + [el.tag, text]] if text else [prefix + [el.tag]]
    if all(c.tag == "member" for c in children):
        return [prefix + [el.tag, "["] + [(c.text or "").strip() for c in children] + ["]"]]
    out = []
    for c in children:
        if c.tag == "entry":
            sub = [x for x in c if isinstance(x.tag, str)]
            name = c.get("name", "")
            if not sub:
                out.append(prefix + [el.tag, name])
            for x in sub:
                out += _lines(prefix + [el.tag, name], x)
        else:
            out += _lines(prefix + [el.tag], c)
    return out


def render(tokens: list) -> str:
    return " ".join(t if t in ("[", "]") else token(t) for t in tokens)


def cli_commands(op: ApiOp) -> list:
    """Linhas de CLI equivalentes à operação; CommandError se não houver forma segura."""
    if op.action == "delete":
        return ["delete " + render(xpath_tokens(op.xpath))]
    el = etree.fromstring(op.element)
    if op.action == "set" and el.tag != "entry":
        return ["set " + render(line) for line in _lines(xpath_tokens(op.xpath), el)]
    if op.action == "set":
        prefix = xpath_tokens(op.xpath) + [el.get("name", "")]
        return ["set " + render(line) for line in element_set_lines(prefix, el)]
    # edit de campo: apaga a lista inteira e recria na ordem do XML final.
    tokens = xpath_tokens(op.xpath)
    lines = ["delete " + render(tokens)]
    lines += ["set " + render(line) for line in _lines(tokens[:-1], el)]
    return lines


def _q(value: str) -> str:
    from panorama_cleanup.inventory import quote
    return quote(value)


# --- arquivos de execução ---------------------------------------------------------

PHASE_FILES = (
    (1, "10-fase1-criar-shared", "Criar objetos em Shared"),
    (2, "20-fase2-atualizar-referencias", "Atualizar referências em regras e grupos locais"),
    (3, "30-fase3-excluir-copias-locais", "Excluir cópias locais nos Device Groups"),
    (4, "40-fase4-excluir-nao-utilizados", "Excluir objetos não utilizados (exceto tags)"),
    (5, "41-fase4-excluir-tags-nao-utilizadas", "Excluir tags não utilizadas (PENDENTE de decisão)"),
)
SOURCES = {
    "cli_output": "https://docs.paloaltonetworks.com/ngfw/pan-os-cli-quick-start/cli-cheat-sheet-panorama",
    "xmlapi_actions": "https://docs.paloaltonetworks.com/ngfw/api/pan-os-xml-api-request-types-and-actions",
    "xmlapi_config": "https://docs.paloaltonetworks.com/ngfw/api/pan-os-xml-api-request-types-and-actions/configuration-api",
    "xmlapi_commit": "https://docs.paloaltonetworks.com/ngfw/api/pan-os-xml-api-request-types-and-actions/commit",
    "xmlapi_commit_all": "https://docs.paloaltonetworks.com/ngfw/api/pan-os-xml-api-request-types-and-actions/commit-all",
    "xmlapi_auth": "https://docs.paloaltonetworks.com/pan-os/11-1/pan-os-panorama-api/pan-os-api-authentication",
    "scp": "https://docs.paloaltonetworks.com/pan-os/11-1/pan-os-cli-quick-start/use-the-cli/use-secure-copy-to-import-and-export-files/export-a-saved-configuration-from-one-firewall-and-import-it-into-another",
    "load_partial": "https://docs.paloaltonetworks.com/ngfw/pan-os-cli-quick-start/use-the-cli/load-a-partial-configuration",
    "commit_gui": "https://docs.paloaltonetworks.com/panorama/administration/administer-panorama/preview-validate-or-commit-configuration-changes",
    "cli_guide": "PAN-OS CLI Quick Start, Version 11.1 & later (docs/referencias/pan-os-11.1/ngfw-cli.md)",
}
RISK = {
    "CREATE_SHARED": "baixo: objeto novo em Shared; o local de mesmo nome continua valendo até a fase 3",
    "UPDATE_RULE_REFERENCE": "médio: edita a lista de membros de um campo da regra (mesmo valor efetivo)",
    "UPDATE_OBJECT_REFERENCE": "médio: edita a lista de membros de um grupo local (mesmo valor efetivo)",
    "DELETE_LOCAL_COPY": "baixo: o DG passa a usar o objeto de Shared de valor idêntico",
    "DELETE_UNUSED": "médio: sem referência no backup; uso fora do Panorama não é visível",
}


def file_key(op: ApiOp, inv_types: dict) -> int:
    if op.phase == 4 and inv_types.get(op.xpath) == "tag":
        return 5
    return op.phase


def _write_lf(path: Path, text: str) -> None:
    """Grava texto UTF-8 com quebra de linha LF em qualquer sistema.

    No Windows, a gravação em modo texto converteria "\n" em "\r\n"; o run-xmlapi.sh
    deixaria de funcionar no bash e os arquivos (e seus sha256) mudariam conforme o SO.
    """
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)


def write_command_files(out_dir, api_ops, inv_types, meta: dict) -> dict:
    """Grava os arquivos de comandos; devolve {nome: caminho} e o índice de comandos."""
    import csv

    out_dir.mkdir(parents=True, exist_ok=True)
    groups = {key: [] for key, _, _ in PHASE_FILES}
    for op in api_ops:
        groups[file_key(op, inv_types)].append(op)

    files, index = {}, []
    for key, stem, title in PHASE_FILES:
        ops = groups[key]
        if key == 5 and not ops:
            continue
        tsv = out_dir / f"{stem}.tsv"
        with open(tsv, "w", encoding="utf-8", newline="") as fh:
            fh.write("seq\tphase\taction\txpath\telement\n")
            for op in ops:
                for field in (op.xpath, op.element):
                    if "\t" in field or "\n" in field:
                        raise CommandError(f"Tab ou quebra de linha no seq {op.seq}")
                fh.write(f"{op.seq}\t{op.phase}\t{op.action}\t{op.xpath}\t{op.element}\n")
        files[tsv.name] = tsv

        cli = out_dir / f"{stem}.cli"
        lines = [
            f"# {title}",
            f"# Gerado pelo run {meta['run_id']} a partir do plano {meta['plan_run']}.",
            "# Interface: PAN-OS CLI do Panorama, modo de configuração (entrar com: configure).",
            f"# ATENÇÃO: CLI NÃO VALIDADA PARA PANORAMA {meta['panos']}. As linhas correspondem à",
            f"# hierarquia de Panorama {meta['cli_other_versions'] or '-'} do PAN-OS CLI Quick Start; o guia não",
            f"# traz a hierarquia de Panorama {meta['cli_version']} (cli-syntax-check.csv). Use a XML API (.tsv).",
            f"# Só use este arquivo após confirmar a sintaxe em um Panorama {meta['cli_version']} de laboratório.",
            "# Antes de colar: no modo operacional, 'set cli scripting-mode on' (o guia limita a colagem",
            "# sem scripting mode a ~20 linhas). Cole em blocos e confira a saída de cada bloco.",
            "# 'delete' pela CLI NÃO verifica dependências (guia 11.1, Modify the Configuration):",
            "# execute as fases na ordem e não pule arquivos.",
            "# NÃO contém commit. Operações sem forma segura na CLI estão indicadas: use o .tsv",
            "# correspondente com run-xmlapi.sh.",
            "",
        ]
        for op in ops:
            lines.append(f"# seq {op.seq} | {op.operation} | {op.target} | {op.detail}")
            try:
                commands = cli_commands(op)
                lines += commands
                cli_text = " && ".join(commands)
            except CommandError as exc:
                lines.append(f"# SEM CLI SEGURA ({exc}); usar XML API: {tsv.name} seq {op.seq}")
                cli_text = ""
            lines.append("")
            api_text = f"type=config action={op.action} xpath={op.xpath}" + (
                f" element={op.element}" if op.element else "")
            common = dict(seq=op.seq, phase=op.phase, file=stem, operation=op.operation,
                          target=op.target, purpose=op.detail, risk=RISK[op.operation])
            index.append(dict(common, interface="Panorama XML API",
                              context="HTTPS POST /api/ com X-PAN-KEY (run-xmlapi.sh)",
                              command=api_text,
                              expected_result='resposta status="success"; candidate config alterado',
                              validation=_validation(op)))
            index.append(dict(common, interface="PAN-OS CLI", context="Panorama, modo configure",
                              command=cli_text or "(sem forma segura; usar XML API)",
                              expected_result="comando aceito sem erro; candidate config alterado",
                              validation=_validation(op)))
        _write_lf(cli, "\n".join(lines) + "\n")
        files[cli.name] = cli

    columns = ("seq", "phase", "file", "operation", "interface", "context", "target", "command",
               "purpose", "expected_result", "validation", "risk")
    idx = out_dir / "commands-index.csv"
    with open(idx, "w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=columns)
        writer.writeheader()
        writer.writerows(index)
    files[idx.name] = idx

    runner = out_dir / "run-xmlapi.sh"
    _write_lf(runner, RUNNER)
    runner.chmod(0o750)
    files[runner.name] = runner

    for name, text in documents(meta, {k: len(v) for k, v in groups.items()}).items():
        path = out_dir / name
        _write_lf(path, text)
        files[name] = path
    return files


def _validation(op: ApiOp) -> str:
    if op.action == "delete":
        return f"type=config action=get xpath={op.xpath} retorna resultado vazio"
    return f"type=config action=get xpath={op.xpath} retorna o elemento igual ao do .tsv"


RUNNER = r'''#!/usr/bin/env bash
# Executa UM arquivo .tsv de operações da Panorama XML API, em ordem, parando no primeiro erro.
# Altera apenas o candidate config. NÃO faz commit nem push.
#
# Uso:  PANORAMA_HOST=<host> PANORAMA_API_KEY=<chave> ./run-xmlapi.sh <arquivo.tsv>
#       Sem CONFIRM_EXECUTE=yes apenas lista o que seria enviado (nada é enviado).
# TLS:  PANORAMA_CA_CERT=<arquivo.pem> para validar o certificado do Panorama.
#       PANORAMA_INSECURE=yes desativa a validação (curl -k); use só em laboratório.
set -euo pipefail

[ $# -eq 1 ] || { echo "uso: $0 <arquivo.tsv>" >&2; exit 2; }
FILE=$1
[ -f "$FILE" ] || { echo "arquivo não encontrado: $FILE" >&2; exit 2; }
TOTAL=$(($(wc -l < "$FILE") - 1))

if [ "${CONFIRM_EXECUTE:-}" != "yes" ]; then
  echo "Conferência (nada será enviado): $FILE com $TOTAL operações"
  tail -n +2 "$FILE" | cut -f1-4 | head -n 20
  echo "..."
  echo "Para executar: CONFIRM_EXECUTE=yes PANORAMA_HOST=... PANORAMA_API_KEY=... $0 $FILE"
  exit 0
fi

: "${PANORAMA_HOST:?defina PANORAMA_HOST}"
: "${PANORAMA_API_KEY:?defina PANORAMA_API_KEY}"
TLS=()
if [ -n "${PANORAMA_CA_CERT:-}" ]; then
  TLS=(--cacert "$PANORAMA_CA_CERT")
elif [ "${PANORAMA_INSECURE:-}" = "yes" ]; then
  echo "AVISO: validação de certificado desativada (PANORAMA_INSECURE=yes)" >&2
  TLS=(-k)
fi
LOG="${FILE%.tsv}.$(date -u +%Y%m%dT%H%M%SZ).log"
DONE=0

while IFS=$'\t' read -r seq phase action xpath element; do
  args=(--data-urlencode "type=config" --data-urlencode "action=$action" --data-urlencode "xpath=$xpath")
  if [ -n "$element" ]; then
    args+=(--data-urlencode "element=$element")
  fi
  resp=$(curl -sS ${TLS[@]+"${TLS[@]}"} -X POST "https://${PANORAMA_HOST}/api/" \
           -H "X-PAN-KEY: ${PANORAMA_API_KEY}" "${args[@]}") || {
    echo "FALHA de comunicação no seq $seq. Execução interrompida; nada foi commitado." >&2
    exit 1
  }
  printf '%s\t%s\t%s\t%s\n' "$seq" "$action" "$xpath" "$resp" >> "$LOG"
  case "$resp" in
    *'status="success"'*) DONE=$((DONE + 1)) ;;
    *) echo "FALHA no seq $seq ($action $xpath): $resp" >&2
       echo "Execução interrompida após $DONE de $TOTAL operações; nada foi commitado." >&2
       echo "Log: $LOG" >&2
       exit 1 ;;
  esac
done < <(tail -n +2 "$FILE")

echo "Concluído: $DONE de $TOTAL operações de $FILE. Log: $LOG. Nenhum commit foi feito."
'''


def documents(meta: dict, counts: dict) -> dict:
    s = SOURCES
    dgs = " ".join(meta["device_groups"])
    tag_row = (f"| 6 | `41-fase4-excluir-tags-nao-utilizadas.tsv` / `.cli` | {counts[5]} | "
               "**pendente de decisão** |\n") if counts[5] else ""
    backup = meta["backup_name"]
    readme = f"""# Execução da limpeza no Panorama

Gerado pelo run `{meta['run_id']}`. Versão identificada no backup: **PAN-OS {meta['panos']}**
(detail-version {meta['panos_detail']}).

Origem, rastreável por sha256:
- backup original `{meta['input_sha']}`;
- plano `{meta['plan_run']}`;
- conversão `{meta['convert_run']}` (XML `{meta['converted_sha']}`);
- remoção `{meta['cleanup_run']}` (XML final `{meta['final_sha']}`).

**Nada aqui foi executado.** Os arquivos alteram apenas o candidate config; commit e push estão
documentados em `60-commit-e-push.md` e exigem aprovação.

## Ordem de execução

| Passo | Arquivo | Operações | Interface |
|---|---|---|---|
| 0 | `01-precheck.md` | conferência do estado do Panorama | CLI + ferramenta local |
| 1 | `02-backup.cli` | backup | PAN-OS CLI |
| 2 | `10-fase1-criar-shared.tsv` / `.cli` | {counts[1]} | XML API (recomendado) ou CLI |
| 3 | `20-fase2-atualizar-referencias.tsv` / `.cli` | {counts[2]} | XML API (recomendado) ou CLI |
| 4 | `30-fase3-excluir-copias-locais.tsv` / `.cli` | {counts[3]} | XML API ou CLI |
| 5 | `40-fase4-excluir-nao-utilizados.tsv` / `.cli` | {counts[4]} | XML API ou CLI |
{tag_row}| 7 | `50-validacao.md` | validação sem commit | interface web + XML API |
| 8 | `60-commit-e-push.md` | commit e push controlados | **somente após aprovação** |
| — | `80-testes-de-politica.cli` | testes antes/depois em cada firewall | CLI do firewall (operacional) |

O procedimento completo de homologação, implantação e rollback parte do modelo
`docs/modelo-etapa-09.md` da ferramenta, preenchido com os dados desta execução.

Cada arquivo deve ser executado inteiro e na ordem; o `run-xmlapi.sh` para no primeiro erro.
Pela CLI, ative `set cli scripting-mode on` antes de colar e lembre que `delete` pela CLI não
verifica dependências: a ordem das fases é o que garante que nenhuma referência fica quebrada.
Se algo falhar, não continue: descarte o candidate config com Revert Changes (interface web) e reavalie.
O detalhe de cada comando (sintaxe, contexto, finalidade, resultado esperado, validação e risco)
está em `commands-index.csv`.

## Por que a XML API é a forma recomendada

- Os XPaths e os elementos vêm literalmente do XML validado. Aplicados em sequência sobre o
  backup, reproduzem exatamente o XML final; isso foi verificado por simulação neste run.
- A atualização de referências usa `action=edit` sobre a lista de membros do campo, o que
  preserva a ordem dos membros. Na CLI, a mesma operação exige apagar e recriar a lista.
- {meta['cli_missing']} operação(ões) não têm forma segura na CLI (valor com caractere fora do
  ASCII). Elas aparecem como comentário no `.cli` e devem ser feitas pela XML API.
- A seção `readonly` (IDs internos) é mantida pelo próprio Panorama quando as alterações são
  feitas por API ou CLI. Por isso não se recomenda importar o XML final inteiro.

## Sintaxe e fontes

- **XML API** (ações set, edit, delete; `type=config`): [Request Types and Actions]({s['xmlapi_actions']}),
  [Configuration (API)]({s['xmlapi_config']}), [Autenticação com X-PAN-KEY]({s['xmlapi_auth']}).
- **CLI — não validada para Panorama {meta['cli_version']}**: das {meta['cli_checked']} linhas,
  {meta['cli_in_target']} constam na hierarquia {meta['cli_version']} do {s['cli_guide']} e
  {meta['cli_other']} constam apenas na hierarquia de Panorama {meta['cli_other_versions'] or '-'}.
  A seção {meta['cli_version']} desse guia é a hierarquia de firewall e não inclui `device-group`
  nem os objetos de Shared do Panorama. Detalhe em `cli-syntax-check.csv`. Pela regra do projeto,
  comandos de outra versão não são presumidos válidos: a CLI só pode ser usada depois de
  confirmada em um Panorama {meta['cli_version']} de laboratório (ver `50-validacao.md`). O guia
  também documenta `set cli config-output-format set`, `set cli scripting-mode on` (necessário para
  colar mais de ~20 linhas) e avisa que `delete` pela CLI não verifica dependências.
- **Por isso a forma de implantação é a XML API**: as ações são genéricas da API e os XPaths e
  elementos saem literalmente do backup PAN-OS {meta['panos']}, ou seja, do esquema do próprio
  Panorama.
- **Backup**: `save config to` e `scp export configuration` ([Secure Copy, PAN-OS 11.1]({s['scp']})).
- **Commit**: [Commit]({s['xmlapi_commit']}), [Commit-All]({s['xmlapi_commit_all']}),
  [Preview, Validate or Commit]({s['commit_gui']}).
- **Alternativa de importação controlada**: [Load a Partial Configuration]({s['load_partial']}).
"""
    precheck = f"""# 0. Pré-verificação (obrigatória)

O plano foi provado sobre o backup `{backup}` (sha256 `{meta['input_sha']}`). A execução só é válida
se o Panorama estiver exatamente nesse estado.

1. Na interface web, abra **Commit > Preview Changes** e confirme que **não há alterações pendentes**
   no candidate config. Se houver, pare.
2. Exporte a configuração atual (passos do arquivo `02-backup.cli`).
3. Na estação, rode a ferramenta sobre o arquivo exportado, numa pasta de trabalho **nova**
   (`W`), separada da usada no plano aprovado:

```sh
panorama-cleanup --workdir W inventory --input W/input/<exportado>.xml
panorama-cleanup --workdir W analyze   --input W/input/<exportado>.xml --inventory W/output/reports/<run inventory>
panorama-cleanup --workdir W plan      --input W/input/<exportado>.xml --analysis  W/output/reports/<run analyze>
```

   No Windows (PowerShell), os mesmos comandos com caminhos do Windows; ver
   `docs/procedimento-windows-linux.md` da ferramenta.

4. Compare o `plan.json` gerado com o do plano aprovado `{meta['plan_run']}`: a lista `operations`
   deve ser **idêntica**. Se o sha256 do arquivo exportado for igual a `{meta['input_sha']}`, o estado
   é o mesmo. Se for diferente e o plano mudar, **não execute**: os comandos precisam ser regenerados.
5. Confirme que as exclusões da fase 4 não são usadas fora do Panorama (configuração local dos
   firewalls, scripts, integrações). A lista está em `40-fase4-excluir-nao-utilizados.cli`.
"""
    backup_cli = f"""# 1. Backup antes de qualquer alteração
# Fonte: {s['cli_guide']}, "Export a Saved Configuration from One Firewall and Import it into
# Another"; {s['scp']}
# Contexto: CLI do Panorama.

# Modo configure: grava um snapshot nomeado da configuração.
configure
save config to pre-limpeza-shared
exit

# Modo operacional: exporta o snapshot para um servidor SCP (ajuste usuário, host e caminho).
# No guia 11.1, o snapshot salvo como "fw1-config" é exportado como "fw1-config.xml".
scp export configuration from pre-limpeza-shared.xml to <usuario>@<host>:<caminho>

# Resultado esperado: arquivo pre-limpeza-shared.xml no servidor de destino.
# Validação: o arquivo abre como XML e contém <config version="{meta['panos']}">.
# Risco: nenhum (somente leitura da configuração).
"""
    validation = f"""# 7. Validação antes de qualquer commit

1. Na interface web: **Commit > Preview Changes** e **Change Summary**. A quantidade e o tipo de
   alterações devem corresponder a `commands-index.csv`, sem nenhuma regra criada, removida
   ou reordenada.
2. **Commit > Validate Changes** (valida o commit no Panorama sem aplicar) e resolver qualquer erro.
3. **Validate Device Group Push** para os Device Groups {dgs}.
   Equivalente na XML API (somente validação, sem push), documentado em
   [Commit-All]({s['xmlapi_commit_all']}):

```
type=commit&action=all&cmd=<commit-all><shared-policy><validate-only></validate-only></shared-policy></commit-all>
```

   O resultado da validação é acompanhado por `type=op&cmd=<show><jobs><id>JOB</id></jobs></show>`
   ([Commit]({s['xmlapi_commit']})).
4. Validação de sintaxe da CLI (obrigatória antes de usar qualquer `.cli`): o guia não traz a
   hierarquia de Panorama {meta['cli_version']} (`cli-syntax-check.csv`). Carregue o XML final em um
   Panorama {meta['panos']} de laboratório, rode `set cli config-output-format set` (modo
   operacional) e `show` em modo configure, e compare as linhas exibidas com as dos arquivos `.cli`.
5. Exporte o candidate config e rode a Etapa 8 (VALIDATE) da ferramenta comparando-o ao XML final
   validado (sha256 `{meta['final_sha']}`).
"""
    commit = f"""# 8. Commit e push controlados — NÃO EXECUTAR SEM APROVAÇÃO

Nenhum arquivo deste diretório faz commit ou push. Esta seção só documenta o procedimento.

1. Commit somente no Panorama (sem push): interface web **Commit > Commit to Panorama**, ou XML API
   `type=commit&cmd=<commit></commit>` ([Commit]({s['xmlapi_commit']})). Acompanhar o job até
   `FIN`/`OK` com `type=op&cmd=<show><jobs><id>JOB</id></jobs></show>`.
2. Push por Device Group, um de cada vez, começando por um DG piloto. Interface web: **Commit >
   Push to Devices**. Na XML API ([Commit-All]({s['xmlapi_commit_all']})):

```
type=commit&action=all&cmd=<commit-all><shared-policy><device-group><entry name="<DG>"/></device-group></shared-policy></commit-all>
```

   DGs afetados: {dgs}. As alterações em Shared atingem todos os DGs, inclusive os sem objetos locais.
   A documentação avisa que a XML API faz push só para firewalls ligados diretamente ao DG informado.
   Neste backup todos os DGs são filhos diretos de Shared.
3. Após cada push, no firewall: `show config pushed-shared-policy` ([CLI Cheat Sheet]({s['cli_output']})),
   e conferir regras e objetos afetados (Etapa 9).
"""
    load_partial = f"""# Alternativa: importação controlada (load config partial)

Documentado em [Load a Partial Configuration]({s['load_partial']}). Substitui contêineres inteiros a
partir do XML final validado. Use **somente** se a pré-verificação confirmar que o Panorama está
exatamente no estado do backup, porque qualquer alteração feita depois do backup nesses contêineres
seria sobrescrita.

1. Importar o XML final para o Panorama ([Secure Copy]({s['scp']})):
   `scp import configuration from <usuario>@<host>:<caminho>/{meta['final_name']}`
2. Em modo configure, para cada contêiner alterado (lista abaixo):
   `load config partial mode replace from-xpath <from> to-xpath <to> from {meta['final_name']}`
   (from-xpath começa em `shared` ou `devices`; to-xpath começa em `/config`).
3. Validar e seguir `50-validacao.md`; commit apenas após aprovação.

Contêineres alterados (from-xpath | to-xpath):

""" + "\n".join(f"- `{c[len('/config/'):]}` | `{c}`" for c in meta["containers"]) + """

Riscos: substituição integral dos contêineres (inclusive rulebases dos DGs com referências
atualizadas); `readonly` não é tocado por este comando. Por isso a forma recomendada continua
sendo a XML API operação a operação.
"""
    return {
        "00-LEIA-ME.md": readme, "01-precheck.md": precheck, "02-backup.cli": backup_cli,
        "50-validacao.md": validation, "60-commit-e-push.md": commit,
        "70-alternativa-load-config-partial.md": load_partial,
    }


def changed_containers(api_ops) -> list:
    """Contêineres de objeto ou rulebase tocados (para a alternativa load config partial)."""
    out = []
    for op in api_ops:
        path = op.xpath if op.action == "set" else op.xpath.rsplit("/entry[", 1)[0]
        if op.action == "edit":
            path = op.xpath.split("/rules/entry[")[0] if "/rules/entry[" in op.xpath \
                else op.xpath.rsplit("/entry[", 1)[0]
        if path not in out:
            out.append(path)
    return out
