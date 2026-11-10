"""Windows wrapper: independent steps, persisted sessions and isolated workers.

Only standard-library modules are imported here. The original engine remains
unchanged and is imported by the worker after its output streams are ready.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sys
import traceback
import uuid

STEPS = ("inventory", "analyze", "plan", "convert", "cleanup", "commands", "validate")
PREREQUISITES = {
    "inventory": (), "analyze": ("inventory",), "plan": ("analyze",),
    "convert": ("plan",), "cleanup": ("plan", "convert"),
    "commands": ("plan", "convert", "cleanup"),
    "validate": ("plan", "convert", "cleanup"),
}
DOWNSTREAM = {
    "inventory": ("analyze", "plan", "convert", "cleanup", "commands", "validate"),
    "analyze": ("plan", "convert", "cleanup", "commands", "validate"),
    "plan": ("convert", "cleanup", "commands", "validate"),
    "convert": ("cleanup", "commands", "validate"),
    "cleanup": ("commands", "validate"), "commands": (), "validate": (),
}


def digest(path: Path) -> str:
    h = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def atomic_json(path: Path, value: dict) -> None:
    path = Path(path)
    temporary = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temporary, path)


def new_session(backup: Path, destination: Path) -> tuple[Path, dict]:
    backup = backup.resolve()
    destination = destination.resolve()
    if not backup.is_file():
        raise ValueError("Selecione um backup XML existente.")
    if destination.exists():
        raise ValueError("Escolha uma pasta nova para o projeto; a pasta selecionada já existe.")
    destination.mkdir(parents=True, exist_ok=False)
    (destination / "input").mkdir()
    copied = destination / "input" / backup.name
    original_hash = digest(backup)
    shutil.copyfile(backup, copied)
    if digest(copied) != original_hash or digest(backup) != original_hash:
        raise ValueError("O backup mudou durante a cópia. Crie novamente o projeto.")
    state = {"format": 1, "input": str(copied), "input_sha256": original_hash,
             "original": str(backup), "runs": {}, "keep_tags": True}
    atomic_json(destination / "desktop-session.json", state)
    return destination, state


def load_session(root: Path) -> dict:
    root = root.resolve()
    state = json.loads((root / "desktop-session.json").read_text(encoding="utf-8"))
    if state.get("format") != 1 or not isinstance(state.get("runs"), dict):
        raise ValueError("Formato de projeto não reconhecido.")
    source = Path(state["input"]).resolve()
    if not source.is_relative_to(root / "input") or digest(source) != state["input_sha256"]:
        raise ValueError("A cópia do backup está fora do projeto ou seu SHA-256 foi alterado.")
    for step, run in state["runs"].items():
        if step not in STEPS or not Path(run).resolve().is_relative_to(root / "output" / "reports"):
            raise ValueError("O projeto contém uma referência de execução inválida.")
    return state


def build_args(step: str, root: Path, state: dict) -> list[str]:
    if step not in STEPS:
        raise ValueError("Etapa desconhecida.")
    runs = state["runs"]
    for required in PREREQUISITES[step]:
        if required not in runs:
            raise ValueError("Conclua a etapa anterior: " + required)
    mode = {"convert": "apply", "cleanup": "apply", "commands": "plan"}.get(step, step)
    args = ["--workdir", str(root), mode, "--input", state["input"]]
    if step == "analyze": args += ["--inventory", runs["inventory"]]
    if step == "plan": args += ["--analysis", runs["analyze"]]
    if step in ("convert", "cleanup", "commands", "validate"):
        args += ["--plan", runs["plan"]]
    if step == "cleanup":
        args += ["--stage", "cleanup", "--from-run", runs["convert"]]
        if state.get("keep_tags", True): args += ["--keep-type", "tag"]
    if step == "commands": args += ["--stage", "commands"]
    if step in ("commands", "validate"):
        args += ["--convert-run", runs["convert"], "--cleanup-run", runs["cleanup"]]
    if step == "validate":
        final_manifest = json.loads((Path(runs["cleanup"]) / "manifest.json").read_text(encoding="utf-8"))
        args += ["--modified", final_manifest["output_xml"]["path"]]
    return args


def accept_result(state: dict, step: str, run: str | None, success: bool) -> None:
    # A failed rerun also invalidates previous results. Never reuse a stale plan.
    for name in (step, *DOWNSTREAM[step]): state["runs"].pop(name, None)
    if success and run: state["runs"][step] = run


def command_preflight(root: Path, state: dict) -> None:
    inv = json.loads((Path(state["runs"]["inventory"]) / "manifest.json").read_text(encoding="utf-8"))
    version = ".".join(inv["panos_version"].split(".")[:2])
    if not re.fullmatch(r"\d+\.\d+", version):
        raise ValueError("Versão PAN-OS inválida no inventário.")
    guide = root / "docs" / "referencias" / ("pan-os-" + version) / "ngfw-cli.md"
    if not guide.is_file():
        raise ValueError("Importe o guia CLI oficial para PAN-OS " + version + " antes de gerar comandos.")
    if os.name == "nt":
        candidates = [Path(os.environ.get(env, r"C:\Program Files")) / "Git" / "bin" / "bash.exe"
                      for env in ("ProgramW6432", "ProgramFiles", "ProgramFiles(x86)")]
        found = next((p for p in candidates if p.is_file()), None)
        if not found:
            raise ValueError("A validação bash -n desta etapa exige o Git Bash (Git for Windows). "
                             "As demais etapas funcionam sem ele.")
        os.environ["PATH"] = str(found.parent) + os.pathsep + os.environ.get("PATH", "")


def worker(request_path: Path) -> int:
    request_path = request_path.resolve()
    request = json.loads(request_path.read_text(encoding="utf-8"))
    log_path = request_path.with_suffix(".log")
    result_path = request_path.with_suffix(".result.json")
    code, run, error = 1, None, ""
    result = {}
    with log_path.open("w", encoding="utf-8", buffering=1) as stream:
        sys.stdout = sys.stderr = stream
        try:
            step = request["step"]
            if step == "compare":
                from panorama_cleanup.comparison import compare_report
                output = Path(request["output"]).resolve()
                traffic_csv = Path(request["traffic_csv"]) if request.get("traffic_csv") else None
                print("Comparando os XMLs e preparando o relatório Excel.")
                summary = compare_report(Path(request["initial"]), Path(request["final"]), output, traffic_csv)
                result = {"report": str(output), "summary": summary}
                code = 0
            else:
                root = Path(request["root"]).resolve()
                state = load_session(root)
                state["keep_tags"] = bool(request.get("keep_tags", True))
                args = build_args(step, root, state)
                if step == "commands": command_preflight(root, state)
                previous = set((root / "output" / "reports").glob("*/manifest.json"))
                from panorama_cleanup.cli import main
                code = main(args)
                created = set((root / "output" / "reports").glob("*/manifest.json")) - previous
                if len(created) == 1:
                    manifest_path = created.pop()
                    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                    run = str(manifest_path.parent)
                    if manifest.get("status") != "ok": code = 1
                elif code == 0:
                    raise ValueError("Não foi possível identificar um único relatório desta execução.")
                if digest(Path(state["input"])) != state["input_sha256"]:
                    raise ValueError("A cópia original foi alterada durante o processamento.")
        except BaseException as exc:
            code, error = 1, str(exc)
            traceback.print_exc()
    atomic_json(result_path, {"code": code, "run": run, "error": error, **result})
    return code
