"""Guardas de escrita: o XML original e o diretório de entrada nunca são alterados."""

from __future__ import annotations

from pathlib import Path

from panorama_cleanup.config import Settings
from panorama_cleanup.errors import SafetyViolation
from panorama_cleanup.run_context import sha256_file


def ensure_writable_target(
    target: Path,
    settings: Settings,
    input_path: Path | None = None,
    allow_overwrite: bool = False,
) -> Path:
    """Valida um destino de escrita e devolve o caminho resolvido.

    Recusa: o próprio arquivo de entrada, qualquer caminho dentro do diretório
    de entrada, caminhos fora dos diretórios graváveis e sobrescrita de arquivo
    existente (salvo allow_overwrite, reservado a logs do próprio run).
    """
    resolved = Path(target).resolve()

    if input_path is not None and resolved == Path(input_path).resolve():
        raise SafetyViolation(f"Escrita recusada sobre o arquivo de entrada: {resolved}")
    if resolved.is_relative_to(settings.input_dir):
        raise SafetyViolation(f"Escrita recusada no diretório de entrada: {resolved}")
    if not any(resolved.is_relative_to(d) for d in settings.writable_dirs):
        raise SafetyViolation(f"Escrita recusada fora dos diretórios permitidos: {resolved}")
    if resolved.exists() and not allow_overwrite:
        raise SafetyViolation(f"Escrita recusada: arquivo já existe: {resolved}")
    return resolved


def verify_unchanged(path: Path, expected_sha256: str) -> None:
    """Confirma que o arquivo de entrada não mudou desde o início do run."""
    actual = sha256_file(path)
    if actual != expected_sha256:
        raise SafetyViolation(
            f"Arquivo de entrada alterado durante a execução: {path} "
            f"(esperado {expected_sha256}, encontrado {actual})"
        )
