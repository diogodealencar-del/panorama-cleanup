"""Identificação e rastreabilidade de cada execução (CLAUDE.md §13)."""

from __future__ import annotations

import hashlib
import json
import platform
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from panorama_cleanup import __version__

MODES = ("inventory", "analyze", "plan", "apply", "validate")
_CHUNK = 1024 * 1024


@dataclass(frozen=True)
class RunContext:
    run_id: str
    mode: str
    input_path: Path
    input_sha256: str
    input_size: int
    started_at: str

    def manifest(self) -> dict:
        return {
            "run_id": self.run_id,
            "mode": self.mode,
            "started_at": self.started_at,
            "input": {
                "path": str(self.input_path),
                "sha256": self.input_sha256,
                "size_bytes": self.input_size,
            },
            "tool_version": __version__,
            "python_version": platform.python_version(),
            "platform": platform.platform(),
        }


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(_CHUNK), b""):
            digest.update(chunk)
    return digest.hexdigest()


def new_run_id(mode: str, input_sha256: str, now: datetime, label: str = "") -> str:
    """Formato: AAAAMMDDTHHMMSSZ-<modo>[-<rótulo>]-<8 primeiros hex do sha256 da entrada>."""
    if mode not in MODES:
        raise ValueError(f"Modo desconhecido: {mode!r}")
    stamp = now.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    middle = f"{mode}-{label}" if label else mode
    return f"{stamp}-{middle}-{input_sha256[:8]}"


def create_run_context(mode: str, input_path: Path, now: datetime | None = None,
                       label: str = "") -> RunContext:
    input_path = Path(input_path).resolve()
    now = now or datetime.now(timezone.utc)
    digest = sha256_file(input_path)
    return RunContext(
        run_id=new_run_id(mode, digest, now, label),
        mode=mode,
        input_path=input_path,
        input_sha256=digest,
        input_size=input_path.stat().st_size,
        started_at=now.astimezone(timezone.utc).isoformat(),
    )


def write_manifest(ctx: RunContext, settings, extra: dict | None = None) -> Path:
    """Grava output/reports/<run_id>/manifest.json, recusando sobrescrita."""
    from panorama_cleanup.safety import ensure_writable_target

    target = ensure_writable_target(
        settings.reports_dir / ctx.run_id / "manifest.json", settings, ctx.input_path
    )
    target.parent.mkdir(parents=True, exist_ok=True)
    data = ctx.manifest()
    data.update(extra or {})
    target.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return target
