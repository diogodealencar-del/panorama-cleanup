"""Carregamento da configuração e resolução dos diretórios do projeto."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from panorama_cleanup.errors import ConfigError

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_SETTINGS_FILE = PROJECT_ROOT / "config" / "settings.json"

PATH_KEYS = ("input", "reports", "xml_output", "cli", "rollback", "logs")
LOG_LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR")


@dataclass(frozen=True)
class Settings:
    root: Path
    input_dir: Path
    reports_dir: Path
    xml_output_dir: Path
    cli_dir: Path
    rollback_dir: Path
    logs_dir: Path
    log_level: str

    @property
    def writable_dirs(self) -> tuple:
        """Únicos diretórios onde a ferramenta pode gravar."""
        return (
            self.reports_dir,
            self.xml_output_dir,
            self.cli_dir,
            self.rollback_dir,
            self.logs_dir,
        )


def load_settings(path: Path = DEFAULT_SETTINGS_FILE, root: Path | None = None) -> Settings:
    """Lê settings.json. Chaves ausentes ou desconhecidas são erro, não default silencioso."""
    path = Path(path)
    root = Path(root).expanduser().resolve() if root else path.resolve().parent.parent
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise ConfigError(f"Arquivo de configuração não encontrado: {path}")
    except json.JSONDecodeError as exc:
        raise ConfigError(f"JSON inválido em {path}: {exc}")

    _require_exact_keys(data, ("paths", "logging"), "raiz")
    _require_exact_keys(data["paths"], PATH_KEYS, "paths")
    _require_exact_keys(data["logging"], ("level",), "logging")

    level = data["logging"]["level"]
    if level not in LOG_LEVELS:
        raise ConfigError(f"logging.level inválido: {level!r}")

    dirs = {}
    for key in PATH_KEYS:
        resolved = (root / data["paths"][key]).resolve()
        if not resolved.is_relative_to(root):
            raise ConfigError(f"paths.{key} aponta para fora do projeto: {resolved}")
        dirs[key] = resolved

    input_dir = dirs["input"]
    for key in PATH_KEYS[1:]:
        if dirs[key].is_relative_to(input_dir) or input_dir.is_relative_to(dirs[key]):
            raise ConfigError(f"paths.{key} não pode se sobrepor ao diretório de entrada")

    return Settings(
        root=root,
        input_dir=input_dir,
        reports_dir=dirs["reports"],
        xml_output_dir=dirs["xml_output"],
        cli_dir=dirs["cli"],
        rollback_dir=dirs["rollback"],
        logs_dir=dirs["logs"],
        log_level=level,
    )


def _require_exact_keys(section: dict, expected: tuple, name: str) -> None:
    if not isinstance(section, dict):
        raise ConfigError(f"Seção {name} deve ser um objeto JSON")
    missing = set(expected) - set(section)
    unknown = set(section) - set(expected)
    if missing:
        raise ConfigError(f"Chaves ausentes em {name}: {sorted(missing)}")
    if unknown:
        raise ConfigError(f"Chaves desconhecidas em {name}: {sorted(unknown)}")
