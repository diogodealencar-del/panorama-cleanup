"""Validação do ambiente: versão do Python e dependências de requirements.txt."""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass
from importlib import metadata
from pathlib import Path

MIN_PYTHON = (3, 9)
# 3.9 está fora de suporte de segurança desde out/2025: aceito, mas sinalizado.
RECOMMENDED_PYTHON = (3, 10)

_REQ_LINE = re.compile(r"^([A-Za-z0-9_.\-]+)\s*>=\s*([0-9][0-9A-Za-z.]*)$")


@dataclass(frozen=True)
class Check:
    name: str
    required: str
    found: str
    ok: bool
    warning: str = ""


def parse_requirements(path: Path) -> list:
    """Aceita apenas linhas 'pacote>=versão'; qualquer outra forma é erro explícito."""
    requirements = []
    for number, raw in enumerate(Path(path).read_text(encoding="utf-8").splitlines(), 1):
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        match = _REQ_LINE.match(line)
        if not match:
            raise ValueError(f"{path}:{number}: formato não suportado: {raw!r}")
        requirements.append((match.group(1), match.group(2)))
    return requirements


def version_tuple(version: str) -> tuple:
    parts = []
    for piece in version.split("."):
        digits = re.match(r"\d+", piece)
        if not digits:
            break
        parts.append(int(digits.group()))
    return tuple(parts)


def check_python(version_info=None) -> Check:
    info = tuple(version_info or sys.version_info[:3])
    found = ".".join(str(p) for p in info)
    warning = ""
    if info[:2] < RECOMMENDED_PYTHON:
        warning = "versão sem suporte de segurança; recomenda-se Python 3.12+"
    return Check(
        name="python",
        required=">=" + ".".join(map(str, MIN_PYTHON)),
        found=found,
        ok=info[:2] >= MIN_PYTHON,
        warning=warning,
    )


def check_environment(requirements_file: Path) -> list:
    checks = [check_python()]
    for name, minimum in parse_requirements(requirements_file):
        try:
            found = metadata.version(name)
        except metadata.PackageNotFoundError:
            checks.append(Check(name, ">=" + minimum, "ausente", False))
            continue
        ok = version_tuple(found) >= version_tuple(minimum)
        checks.append(Check(name, ">=" + minimum, found, ok))
    return checks
