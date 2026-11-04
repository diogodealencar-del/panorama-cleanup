"""Conferência dos comandos gerados contra a hierarquia oficial da CLI.

Fonte: PAN-OS CLI Quick Start (Version 11.1 & later), convertido para texto em
docs/referencias/pan-os-11.1/. O documento traz uma hierarquia completa por versão
("PAN-OS 11.1 Configure CLI Command Hierarchy", "... 12.1 ...", etc.). A conferência
é feita por seção: uma linha só é considerada documentada para a versão X.Y se
corresponder à hierarquia daquela versão (CLAUDE.md §16: não presumir que comandos
de uma versão funcionam em outra). Cada linha gerada é comparada token a token:

- palavra-chave do documento: igualdade exata;
- variável "<...>": qualquer valor; se for uma lista de opções "<a|b>", o valor
  precisa ser uma delas;
- lista "[ <x1> <x2>... ]": o comando gerado precisa ter uma lista entre colchetes.

"delete <caminho>" é aceito quando o caminho é prefixo de uma linha "set"
documentada (o documento descreve delete sobre a mesma hierarquia).

O arquivo de referência é tratado como dado: só as linhas "set ..." são lidas.
"""

from __future__ import annotations

import re
import shlex
from collections import defaultdict
from dataclasses import dataclass

FOOTER = re.compile(r"^PAN-OS CLI Quick Start Version|^Use the CLI$|^```|^CLI Changes in PAN-OS")
CONTINUATION = re.compile(r"^[a-z0-9<\[]")
LIST, VAR, KW = "list", "var", "kw"


@dataclass(frozen=True)
class CheckedLine:
    line: str
    ok: bool
    pattern: str
    reason: str


SECTION = re.compile(r"^PAN-OS (\d+\.\d+) (CLI Ops|Configure CLI) Command Hierarchy$")


def hierarchy_sections(path) -> dict:
    """{(versão, "configure"|"ops"): (primeira linha, última linha)} das hierarquias completas."""
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    marks = [(i, m.group(1), "ops" if m.group(2) == "CLI Ops" else "configure")
             for i, line in enumerate(lines) if (m := SECTION.match(line.strip()))]
    out = {}
    for n, (start, version, kind) in enumerate(marks):
        end = marks[n + 1][0] if n + 1 < len(marks) else len(lines)
        out[(version, kind)] = (start, end)
    return out


def load_section_patterns(path, version: str, kind: str = "configure") -> dict:
    sections = hierarchy_sections(path)
    if (version, kind) not in sections:
        return {}
    start, end = sections[(version, kind)]
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()[start:end]
    return _patterns_from_lines(lines)


def load_patterns(path) -> dict:
    """Linhas 'set' de todo o documento (todas as versões), quebras do PDF reunidas.

    Retorna {primeira palavra após 'set': [(tokens, texto original)]}.
    """
    return _patterns_from_lines(path.read_text(encoding="utf-8", errors="replace").splitlines())


def _patterns_from_lines(raw) -> dict:
    lines, current = [], None
    for text in raw:
        text = text.strip()
        if FOOTER.match(text):
            continue
        if text.startswith("set "):
            if current:
                lines.append(current)
            current = text
            continue
        # Continuação: o PDF quebra linhas da hierarquia antes de "<", "[" ou de uma
        # palavra-chave (sempre minúscula); prosa começa com maiúscula.
        if current and text and (_unbalanced(current) or CONTINUATION.match(text)):
            current = f"{current} {text}" if not current.endswith("-") else current + text
            continue
        if current:
            lines.append(current)
            current = None
    if current:
        lines.append(current)

    patterns = defaultdict(list)
    for line in lines:
        tokens = _pattern_tokens(line)
        if len(tokens) >= 2:
            patterns[tokens[1][1]].append((tokens, line))
    return patterns


def _unbalanced(text: str) -> bool:
    return text.count("<") > text.count(">") or text.count("[") > text.count("]")


def _pattern_tokens(line: str) -> list:
    tokens, i, words = [], 0, line.split()
    while i < len(words):
        w = words[i]
        if w == "[":
            j = i
            while j < len(words) and words[j] != "]":
                j += 1
            tokens.append((LIST, None))
            i = j + 1
            continue
        if w.startswith("<"):
            body = w.strip("<>")
            choices = None
            if "|" in body and not any(ch in body for ch in " ,."):
                choices = frozenset(c for c in body.split("|") if c)
            tokens.append((VAR, choices))
        else:
            tokens.append((KW, w))
        i += 1
    return tokens


def _command_tokens(line: str) -> list:
    words = shlex.split(line)
    tokens, i = [], 0
    while i < len(words):
        if words[i] == "[":
            j = words.index("]", i)
            tokens.append((LIST, words[i + 1:j]))
            i = j + 1
        else:
            tokens.append(("value", words[i]))
            i += 1
    return tokens


def _match(pattern, command) -> bool:
    if len(pattern) != len(command):
        return False
    for (pk, pv), (ck, cv) in zip(pattern, command):
        if pk == LIST:
            if ck != LIST:
                return False
        elif ck == LIST:
            return False
        elif pk == KW:
            if pv != cv:
                return False
        elif pv is not None and cv not in pv:
            return False
    return True


def _match_prefix(pattern, command) -> bool:
    return len(pattern) >= len(command) and _match(pattern[:len(command)], command)


def check_lines(lines, patterns) -> list:
    results = []
    for line in lines:
        verb = line.split(" ", 1)[0]
        command = _command_tokens(line)
        if verb not in ("set", "delete"):
            results.append(CheckedLine(line, False, "", "comando não é set/delete"))
            continue
        command = [("value", "set")] + command[1:]
        candidates = patterns.get(command[1][1], []) if len(command) > 1 else []
        matcher = _match if verb == "set" else _match_prefix
        hit = next((text for tokens, text in candidates if matcher(tokens, command)), None)
        results.append(CheckedLine(
            line, hit is not None, hit or "",
            "" if hit else "nenhuma linha da hierarquia documentada corresponde"))
    return results


def command_lines(cli_text: str) -> list:
    return [l for l in cli_text.splitlines()
            if l and not l.startswith("#") and l.split(" ", 1)[0] in ("set", "delete")]


def check_by_version(lines, path, target: str) -> list:
    """Para cada linha: versões cuja hierarquia 'configure' a documenta.

    Retorna [(linha, [versões], padrão da versão-alvo ou da primeira versão)].
    """
    versions = sorted({v for v, k in hierarchy_sections(path) if k == "configure"},
                      key=lambda v: tuple(map(int, v.split("."))))
    per_version = {v: load_section_patterns(path, v) for v in versions}
    results = {v: check_lines(lines, per_version[v]) for v in versions}
    out = []
    for n, line in enumerate(lines):
        found = [v for v in versions if results[v][n].ok]
        pick = target if target in found else (found[0] if found else None)
        out.append((line, found, results[pick][n].pattern if pick else ""))
    return out
