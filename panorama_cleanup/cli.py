"""Ponto de entrada: os cinco modos independentes (CLAUDE.md §10).

Nenhum modo encadeia o seguinte. Modos ainda não implementados encerram com
EXIT_NOT_IMPLEMENTED sem gravar nada.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

from panorama_cleanup import __version__
from panorama_cleanup.config import DEFAULT_SETTINGS_FILE, PROJECT_ROOT, Settings, load_settings
from panorama_cleanup.errors import ConfigError, PanoramaCleanupError
from panorama_cleanup.analysis import run_analysis
from panorama_cleanup.errors import AnalysisError
from panorama_cleanup.inventory import build_inventory
from panorama_cleanup.logging_setup import LOGGER_NAME, configure_logging
from panorama_cleanup.localization import plan_with_localization
from panorama_cleanup.report_generator import (
    analysis_summary, plan_summary, report_hashes, write_analysis_reports,
    write_inventory_reports, write_plan_reports,
)
from panorama_cleanup.run_context import MODES, create_run_context, sha256_file, write_manifest
from panorama_cleanup.safety import verify_unchanged
from panorama_cleanup.xml_reader import parse_backup

WORKDIR_ENV = "PANORAMA_CLEANUP_HOME"

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_NOT_IMPLEMENTED = 3

MODE_HELP = {
    "inventory": "Somente leitura. Inventário dos objetos.",
    "analyze": "Somente leitura. Utilização, dependências, duplicidades e conflitos.",
    "plan": "Somente leitura. Plano de alterações e comandos propostos.",
    "apply": "Aplica o plano em uma cópia do XML. Nunca altera o original.",
    "validate": "Compara original e modificado.",
}

# Etapa do protocolo (CLAUDE.md §17) em que cada modo passa a existir.
MODE_STAGE = {"inventory": 2, "analyze": 3, "plan": 4, "apply": 5, "validate": 8}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="panorama-cleanup",
        description="Panorama Configuration Analyzer & Object Cleanup",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.add_argument(
        "--config", type=Path, default=DEFAULT_SETTINGS_FILE, help="caminho do settings.json"
    )
    parser.add_argument(
        "--workdir", type=Path, default=os.environ.get(WORKDIR_ENV) or None,
        help=f"pasta de trabalho do cliente/Panorama (padrão: ${WORKDIR_ENV} ou a pasta do "
             "projeto). input/, output/, cli/, rollback/ e logs/ ficam dentro dela",
    )
    sub = parser.add_subparsers(dest="mode", metavar="MODO")
    sub.required = True
    for mode in MODES:
        mode_parser = sub.add_parser(mode, help=MODE_HELP[mode], description=MODE_HELP[mode])
        mode_parser.add_argument(
            "--input", type=Path, required=True, help="backup XML original do Panorama"
        )
        if mode == "apply":
            mode_parser.add_argument(
                "--plan", type=Path, required=True,
                help="diretório do run PLAN aprovado (output/reports/<run_id>)",
            )
            mode_parser.add_argument(
                "--stage", choices=("convert", "cleanup"), default="convert",
                help="convert: fases 1-3 (Etapa 5); cleanup: fase 4 sobre o XML convertido (Etapa 6)",
            )
            mode_parser.add_argument(
                "--from-run", type=Path,
                help="cleanup: diretório do run APPLY convert aprovado (output/reports/<run_id>)",
            )
            mode_parser.add_argument(
                "--keep-type", action="append", default=[], metavar="TIPO",
                help="cleanup: tipo de objeto que não deve ser removido (repetível; ex.: tag)",
            )
        if mode == "validate":
            mode_parser.add_argument("--modified", type=Path, required=True,
                                     help="XML final a validar")
            mode_parser.add_argument(
                "--exported", action="store_true",
                help="o XML foi exportado do Panorama após a implantação: não exige o sha256 do "
                     "XML gerado e ignora /config/readonly (IDs mantidos pelo Panorama) e, se ausente no "
                     "original, /config/predefined (conteúdo embutido lido por action=get)")
            for flag, text in (("--plan", "run PLAN aprovado"),
                               ("--convert-run", "run APPLY de conversão (Etapa 5)"),
                               ("--cleanup-run", "run APPLY cleanup (Etapa 6)")):
                mode_parser.add_argument(flag, type=Path, required=True, help=text)
        if mode == "plan":
            mode_parser.add_argument(
                "--stage", choices=("plan", "commands"), default="plan",
                help="plan: plano de alterações (Etapa 4); commands: comandos CLI/XML API (Etapa 7)",
            )
            mode_parser.add_argument(
                "--analysis", type=Path,
                help="plan: diretório do run ANALYZE sobre o mesmo backup",
            )
            mode_parser.add_argument("--plan", type=Path, help="commands: run PLAN aprovado")
            mode_parser.add_argument("--convert-run", type=Path,
                                     help="commands: run APPLY de conversão (Etapa 5)")
            mode_parser.add_argument("--cleanup-run", type=Path,
                                     help="commands: run APPLY cleanup (Etapa 6)")
        if mode == "analyze":
            mode_parser.add_argument(
                "--inventory", type=Path, required=True,
                help="diretório do run INVENTORY sobre o mesmo backup (output/reports/<run_id>)",
            )
    return parser


def _existing_sha256(path) -> str:
    """sha256 de um arquivo de um run anterior; ausência vira erro claro, sem traceback."""
    try:
        return sha256_file(path)
    except OSError as exc:
        raise AnalysisError(
            f"Arquivo de um run anterior ausente ou ilegível: {path} ({exc.strerror}). "
            "O run não pode ser usado: restaure o arquivo ou refaça a etapa que o gerou.")


def _safe_console() -> None:
    """Console do Windows (cp1252/cp850) não quebra com caracteres fora da página de código."""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="backslashreplace")
        except (AttributeError, ValueError):
            pass


def main(argv=None) -> int:
    _safe_console()
    args = build_parser().parse_args(argv)
    try:
        settings = load_settings(args.config, root=args.workdir)
        if args.workdir:
            prepare_workdir(settings)
    except PanoramaCleanupError as exc:
        print(f"ERRO: {exc}", file=sys.stderr)
        return EXIT_ERROR

    if not args.input.is_file():
        print(f"ERRO: arquivo de entrada não encontrado: {args.input}", file=sys.stderr)
        return EXIT_ERROR

    try:
        return _dispatch(args, settings)
    except PanoramaCleanupError as exc:
        # Ex.: colisão de run_id (dois runs do mesmo modo no mesmo segundo).
        print(f"ERRO: {exc}", file=sys.stderr)
        return EXIT_ERROR


def prepare_workdir(settings: Settings) -> None:
    """Cria a estrutura da pasta de trabalho. Nunca apaga nem sobrescreve nada."""
    if not settings.root.is_dir():
        raise ConfigError(f"--workdir não existe: {settings.root}")
    for d in (settings.input_dir, *settings.writable_dirs):
        d.mkdir(parents=True, exist_ok=True)


def _dispatch(args, settings) -> int:
    if args.mode == "inventory":
        return run_inventory(args.input, settings)
    if args.mode == "analyze":
        return run_analyze(args.input, args.inventory, settings)
    if args.mode == "validate":
        return run_validate(args.input, args.modified, args.plan, args.convert_run,
                            args.cleanup_run, settings, args.exported)
    if args.mode == "plan" and args.stage == "commands":
        missing = [n for n in ("plan", "convert_run", "cleanup_run") if getattr(args, n) is None]
        if missing:
            print(f"ERRO: --stage commands exige --plan, --convert-run e --cleanup-run", file=sys.stderr)
            return EXIT_ERROR
        return run_commands(args.input, args.plan, args.convert_run, args.cleanup_run, settings)
    if args.mode == "plan":
        if args.analysis is None:
            print("ERRO: plan exige --analysis", file=sys.stderr)
            return EXIT_ERROR
        return run_plan(args.input, args.analysis, settings)
    if args.mode == "apply" and args.stage == "cleanup":
        if args.from_run is None:
            print("ERRO: --stage cleanup exige --from-run", file=sys.stderr)
            return EXIT_ERROR
        return run_cleanup(args.input, args.plan, args.from_run, settings, tuple(args.keep_type))
    if args.mode == "apply":
        return run_apply(args.input, args.plan, settings)

    print(
        f"Modo {args.mode.upper()} ainda não implementado "
        f"(previsto para a Etapa {MODE_STAGE[args.mode]}). Nenhuma ação executada.",
        file=sys.stderr,
    )
    return EXIT_NOT_IMPLEMENTED


def run_inventory(input_path: Path, settings: Settings) -> int:
    """INVENTORY: somente leitura do backup; grava apenas relatórios, log e manifest."""
    ctx = create_run_context("inventory", input_path)
    log_file = configure_logging(settings, ctx.run_id)
    log = logging.getLogger(LOGGER_NAME)
    log.info("Run %s: entrada %s sha256=%s", ctx.run_id, ctx.input_path, ctx.input_sha256)
    try:
        inv = build_inventory(parse_backup(ctx.input_path))
        verify_unchanged(ctx.input_path, ctx.input_sha256)
        log.info(
            "PAN-OS %s (detail %s); %d Device Groups; %d objetos",
            inv.metadata.config_version, inv.metadata.detail_version,
            len(inv.device_groups), len(inv.records),
        )
        for warning in inv.warnings:
            log.warning(warning)
        unreconciled = [r for r in inv.reconciliation if r[2] != r[3]]
        reports = write_inventory_reports(inv, ctx, settings)
        verify_unchanged(ctx.input_path, ctx.input_sha256)
    except PanoramaCleanupError as exc:
        log.error("%s", exc)
        write_manifest(ctx, settings, {"status": "error", "error": str(exc), "log": str(log_file)})
        return EXIT_ERROR

    status = "ok" if not unreconciled else "error"
    manifest = write_manifest(ctx, settings, {
        "status": status,
        "panos_version": inv.metadata.config_version,
        "panos_detail_version": inv.metadata.detail_version,
        "objects": len(inv.records),
        "warnings": len(inv.warnings),
        "input_unchanged": True,
        "log": str(log_file),
        "outputs": report_hashes(reports),
    })
    if unreconciled:
        log.error("Contagem divergente em %d contêiner(es): %s", len(unreconciled), unreconciled)
        return EXIT_ERROR
    log.info("Relatórios em %s", manifest.parent)
    return EXIT_OK


def check_inventory_run(inventory_dir: Path, ctx, inv) -> str:
    """Confirma que o run INVENTORY corresponde ao mesmo backup e ao mesmo inventário."""
    import csv
    import json

    manifest_path = Path(inventory_dir) / "manifest.json"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise AnalysisError(f"Manifest do inventário ilegível: {manifest_path}: {exc}")
    if manifest.get("mode") != "inventory" or manifest.get("status") != "ok":
        raise AnalysisError(f"{manifest_path}: não é um run INVENTORY concluído com sucesso")
    if manifest["input"]["sha256"] != ctx.input_sha256:
        raise AnalysisError("O inventário informado foi gerado a partir de outro arquivo (sha256 diferente)")
    csv_path = manifest_path.parent / "inventory.csv"
    if _existing_sha256(csv_path) != manifest["outputs"].get("inventory.csv"):
        raise AnalysisError(f"{csv_path} foi alterado após a geração (sha256 diverge do manifest)")
    with open(csv_path, encoding="utf-8", newline="") as fh:
        previous = [(r["xpath"], r["value_sha256"]) for r in csv.DictReader(fh)]
    current = [(r.xpath, r.value_sha256) for r in inv.records]
    if previous != current:
        raise AnalysisError("Inventário atual diverge do inventory.csv do run informado")
    return manifest["run_id"]


def run_analyze(input_path: Path, inventory_dir: Path, settings: Settings) -> int:
    """ANALYZE: somente leitura; dependências, uso e duplicidades."""
    ctx = create_run_context("analyze", input_path)
    log_file = configure_logging(settings, ctx.run_id)
    log = logging.getLogger(LOGGER_NAME)
    log.info("Run %s: entrada %s sha256=%s", ctx.run_id, ctx.input_path, ctx.input_sha256)
    try:
        an = run_analysis(parse_backup(ctx.input_path))
        inventory_run = check_inventory_run(inventory_dir, ctx, an.inventory)
        verify_unchanged(ctx.input_path, ctx.input_sha256)
        log.info("Inventário de origem: %s", inventory_run)
        for warning in an.warnings:
            log.warning(warning)
        reports = write_analysis_reports(an, ctx, settings, inventory_run)
        verify_unchanged(ctx.input_path, ctx.input_sha256)
    except PanoramaCleanupError as exc:
        log.error("%s", exc)
        write_manifest(ctx, settings, {"status": "error", "error": str(exc), "log": str(log_file)})
        return EXIT_ERROR

    summary = analysis_summary(an)
    log.info("Resumo: %s", summary)
    manifest = write_manifest(ctx, settings, {
        "status": "ok", "inventory_run": inventory_run, "summary": summary,
        "input_unchanged": True, "log": str(log_file), "outputs": report_hashes(reports),
    })
    log.info("Relatórios em %s", manifest.parent)
    return EXIT_OK


def check_analysis_run(analysis_dir: Path, ctx, an) -> str:
    """Confirma que o run ANALYZE corresponde ao mesmo backup e à mesma classificação."""
    import csv
    import json

    from panorama_cleanup.report_generator import classification_rows

    manifest_path = Path(analysis_dir) / "manifest.json"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise AnalysisError(f"Manifest da análise ilegível: {manifest_path}: {exc}")
    if manifest.get("mode") != "analyze" or manifest.get("status") != "ok":
        raise AnalysisError(f"{manifest_path}: não é um run ANALYZE concluído com sucesso")
    if manifest["input"]["sha256"] != ctx.input_sha256:
        raise AnalysisError("A análise informada foi gerada a partir de outro arquivo (sha256 diferente)")
    csv_path = manifest_path.parent / "object-classification.csv"
    if _existing_sha256(csv_path) != manifest["outputs"].get("object-classification.csv"):
        raise AnalysisError(f"{csv_path} foi alterado após a geração (sha256 diverge do manifest)")
    keys = ("xpath", "classifications", "value_cluster", "value_differs_in")
    with open(csv_path, encoding="utf-8", newline="") as fh:
        previous = [tuple(r[k] for k in keys) for r in csv.DictReader(fh)]
    current = [tuple(str(r[k]) for k in keys) for r in classification_rows(an)]
    if previous != current:
        raise AnalysisError("Classificação atual diverge da análise informada")
    return manifest["run_id"]


def run_plan(input_path: Path, analysis_dir: Path, settings: Settings) -> int:
    """PLAN: somente leitura; plano de centralização com prova de equivalência simulada."""
    ctx = create_run_context("plan", input_path)
    log_file = configure_logging(settings, ctx.run_id)
    log = logging.getLogger(LOGGER_NAME)
    log.info("Run %s: entrada %s sha256=%s", ctx.run_id, ctx.input_path, ctx.input_sha256)
    try:
        original = parse_backup(ctx.input_path)
        analysis_run = check_analysis_run(analysis_dir, ctx, run_analysis(original))
        lp = plan_with_localization(original)
        an, plan = lp.analysis, lp.plan
        verify_unchanged(ctx.input_path, ctx.input_sha256)
        log.info("Análise de origem: %s", analysis_run)
        reports = write_plan_reports(an, plan, ctx, settings, analysis_run)
        verify_unchanged(ctx.input_path, ctx.input_sha256)
    except PanoramaCleanupError as exc:
        log.error("%s", exc)
        write_manifest(ctx, settings, {"status": "error", "error": str(exc), "log": str(log_file)})
        return EXIT_ERROR

    summary = plan_summary(an, plan)
    log.info("Resumo: %s", summary)
    manifest = write_manifest(ctx, settings, {
        "status": "ok", "analysis_run": analysis_run, "summary": summary,
        "input_unchanged": True, "log": str(log_file), "outputs": report_hashes(reports),
    })
    log.info("Relatórios em %s", manifest.parent)
    return EXIT_OK


def load_approved_plan(plan_dir: Path, ctx, lp) -> tuple:
    """Plano aprovado: manifest íntegro, mesmo backup e idêntico ao recalculado agora."""
    import csv
    import json

    from panorama_cleanup.report_generator import operation_rows

    manifest_path = Path(plan_dir) / "manifest.json"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise AnalysisError(f"Manifest do plano ilegível: {manifest_path}: {exc}")
    if manifest.get("mode") != "plan" or manifest.get("status") != "ok":
        raise AnalysisError(f"{manifest_path}: não é um run PLAN concluído com sucesso")
    if manifest["input"]["sha256"] != ctx.input_sha256:
        raise AnalysisError("O plano informado foi gerado a partir de outro arquivo (sha256 diferente)")
    for name in ("plan.json", "reference-updates.csv", "renamed-objects.csv", "deleted-objects.csv"):
        if _existing_sha256(manifest_path.parent / name) != manifest["outputs"].get(name):
            raise AnalysisError(f"{name} do plano foi alterado após a geração")
    if manifest["summary"].get("verification_differences") != 0:
        raise AnalysisError("O plano informado não foi provado equivalente")
    plan_json = json.loads((manifest_path.parent / "plan.json").read_text(encoding="utf-8"))
    operations = plan_json["operations"]
    if operation_rows(lp.analysis, lp.plan) != operations:
        raise AnalysisError("O plano recalculado sobre este XML difere do plano aprovado")
    with open(manifest_path.parent / "reference-updates.csv", encoding="utf-8", newline="") as fh:
        copies = [r for r in csv.DictReader(fh) if r["applies_to"] == "cópia criada em Shared"]
    return manifest["run_id"], operations, copies


def _planned_references_check(plan_dir, result):
    """Toda atualização de referência listada no plano foi aplicada (antes, só a Etapa 8 via)."""
    import csv

    from panorama_cleanup.report_generator import reference_applied
    from panorama_cleanup.xml_validator import Check
    with open(Path(plan_dir) / "reference-updates.csv", encoding="utf-8", newline="") as fh:
        planned = list(csv.DictReader(fh))
    missing = [f"{r['scope']}:{r['source_name']} {r['field']} {r['old']}->{r['new']}"
               for r in planned if not reference_applied(r, result)]
    return Check("todas as atualizações de referência do plano aplicadas", not missing,
                 f"{len(planned)} planejadas; não aplicadas: {len(missing)} {missing[:5]}")


def _phase0_check(full_tree, written, lp):
    """Fases 0-3 sobre o original verdadeiro = XML gravado (fases 1-3 sobre a base)."""
    from panorama_cleanup.policy_comparator import canonical
    from panorama_cleanup.xml_validator import Check
    same = canonical(full_tree.getroot()) == canonical(written.getroot())
    leftovers = [f"{p[2]}:{p[1]}" for p in lp.pairs
                 if written.xpath(f"/config/devices/entry/device-group/entry[@name='{p[2]}']"
                                  f"/{p[0]}/entry[@name='{p[1]}']")]
    return Check("fase 0 sobre o original verdadeiro produz o mesmo XML; nenhuma cópia local "
                 "remanescente", same and not leftovers,
                 f"{len(lp.pairs)} cópias locais (grupo × DG); idêntico: {same}; "
                 f"remanescentes: {leftovers[:5]}")


def _rule_equivalence_check(original, final):
    """Verificador independente: IP, porta e application de cada regra em cada DG."""
    from panorama_cleanup.rule_equivalence import compare_rules, summary
    from panorama_cleanup.xml_validator import Check
    r = compare_rules(original, final)
    return Check("independente: IPs, portas e applications das regras idênticos ao ORIGINAL",
                 r.ok, summary(r))


def run_apply(input_path: Path, plan_dir: Path, settings: Settings) -> int:
    """APPLY: fases 1-3 do plano aprovado em uma cópia; o original não é tocado."""
    from panorama_cleanup.safety import ensure_writable_target
    from panorama_cleanup.shared_converter import apply_plan, serialize
    from panorama_cleanup.report_generator import write_apply_reports
    from panorama_cleanup.xml_validator import validate_transformation

    ctx = create_run_context("apply", input_path)
    log_file = configure_logging(settings, ctx.run_id)
    log = logging.getLogger(LOGGER_NAME)
    log.info("Run %s: entrada %s sha256=%s", ctx.run_id, ctx.input_path, ctx.input_sha256)
    try:
        original = parse_backup(ctx.input_path)
        lp = plan_with_localization(original)
        an, base = lp.analysis, lp.tree
        plan_run, operations, copies = load_approved_plan(plan_dir, ctx, lp)
        log.info("Plano aprovado: %s (%d operações)", plan_run, len(operations))
        # Fases 1-3 sobre a base com as cópias da fase 0; a mesma sequência aplicada ao
        # original verdadeiro, com a fase 0, tem de produzir exatamente o mesmo XML.
        result = apply_plan(base, operations, copies, phases=(1, 2, 3))
        full = apply_plan(original, operations, copies, phases=(0, 1, 2, 3))

        xml_path = ensure_writable_target(
            settings.xml_output_dir / ctx.run_id / f"{ctx.input_path.stem}.shared.xml",
            settings, ctx.input_path)
        xml_path.parent.mkdir(parents=True, exist_ok=True)
        xml_path.write_bytes(serialize(result.tree, ctx.input_path.read_bytes()))
        xml_sha = sha256_file(xml_path)

        # Valida o arquivo gravado, relido do disco.
        reread = parse_backup(xml_path)
        checks, policy_rows, _ = validate_transformation(base, reread, result, operations)
        checks.append(_planned_references_check(plan_dir, result))
        checks.append(_phase0_check(full.tree, reread, lp))
        checks.append(_rule_equivalence_check(original, reread))
        verify_unchanged(ctx.input_path, ctx.input_sha256)
        for c in checks:
            (log.info if c.passed else log.error)("%s %s: %s", "OK" if c.passed else "FALHA",
                                                  c.name, c.detail)
        reports = write_apply_reports(an, result, checks, policy_rows, ctx, settings, plan_run,
                                      plan_dir, xml_path, xml_sha)
        verify_unchanged(ctx.input_path, ctx.input_sha256)
    except PanoramaCleanupError as exc:
        log.error("%s", exc)
        write_manifest(ctx, settings, {"status": "error", "error": str(exc), "log": str(log_file)})
        return EXIT_ERROR

    passed = all(c.passed for c in checks)
    write_manifest(ctx, settings, {
        "status": "ok" if passed else "error", "plan_run": plan_run,
        "output_xml": {"path": str(xml_path), "sha256": xml_sha},
        "phases_applied": [1, 2, 3], "phase_4_executed": False,
        "checks_passed": sum(c.passed for c in checks), "checks_total": len(checks),
        "input_unchanged": True, "log": str(log_file), "outputs": report_hashes(reports),
    })
    if not passed:
        log.error("Integridade NÃO APROVADA; o XML gerado não deve ser utilizado")
        return EXIT_ERROR
    log.info("XML transformado: %s", xml_path)
    return EXIT_OK


def load_convert_run(run_dir: Path, ctx, plan_run: str) -> tuple:
    """Run APPLY convert aprovado: mesmo backup, mesmo plano, 11/11 verificações, XML íntegro."""
    import csv
    import json

    manifest_path = Path(run_dir) / "manifest.json"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise AnalysisError(f"Manifest do run APPLY ilegível: {manifest_path}: {exc}")
    if manifest.get("mode") != "apply" or manifest.get("status") != "ok":
        raise AnalysisError(f"{manifest_path}: não é um run APPLY concluído com sucesso")
    if manifest.get("phase_4_executed") is not False or manifest.get("phases_applied") != [1, 2, 3]:
        raise AnalysisError(f"{manifest_path}: não é um run de conversão (fases 1-3)")
    if manifest["input"]["sha256"] != ctx.input_sha256 or manifest.get("plan_run") != plan_run:
        raise AnalysisError("O run APPLY informado não corresponde a este backup e a este plano")
    if manifest["checks_passed"] != manifest["checks_total"]:
        raise AnalysisError("O run APPLY informado não passou em todas as verificações")
    xml = Path(manifest["output_xml"]["path"])
    if _existing_sha256(xml) != manifest["output_xml"]["sha256"]:
        raise AnalysisError(f"{xml} foi alterado após a conversão")
    refs = manifest_path.parent / "reference-updates.csv"
    if _existing_sha256(refs) != manifest["outputs"].get("reference-updates.csv"):
        raise AnalysisError(f"{refs} foi alterado após a conversão")
    with open(refs, encoding="utf-8", newline="") as fh:
        renames = {(r["source_xpath"], r["field"], r["old"]): r["new"]
                   for r in csv.DictReader(fh) if r["kind"] == "regra"}
    return manifest["run_id"], xml, manifest["output_xml"]["sha256"], renames


def run_cleanup(input_path: Path, plan_dir: Path, from_run: Path, settings: Settings,
                keep_types: tuple = ()) -> int:
    """APPLY cleanup: fase 4 do plano (não utilizados) sobre o XML convertido."""
    from panorama_cleanup.cleanup import deletion_operations, review_candidates
    from panorama_cleanup.report_generator import write_cleanup_reports
    from panorama_cleanup.safety import ensure_writable_target
    from panorama_cleanup.shared_converter import apply_plan, serialize
    from panorama_cleanup.xml_validator import validate_end_to_end, validate_transformation

    ctx = create_run_context("apply", input_path, label="cleanup")
    log_file = configure_logging(settings, ctx.run_id)
    log = logging.getLogger(LOGGER_NAME)
    log.info("Run %s (cleanup): entrada %s sha256=%s", ctx.run_id, ctx.input_path, ctx.input_sha256)
    try:
        original = parse_backup(ctx.input_path)
        lp = plan_with_localization(original)
        base = lp.tree
        plan_run, operations, copies = load_approved_plan(plan_dir, ctx, lp)
        convert_run, converted_path, converted_sha, rule_renames = load_convert_run(
            from_run, ctx, plan_run)
        log.info("Plano %s; XML convertido %s (%s)", plan_run, converted_path, convert_run)

        converted = parse_backup(converted_path)
        an_converted = run_analysis(converted)
        # Valor de referência de cada candidato: o do estado esperado após as fases 1-3
        # do plano aprovado (original + plano), e não o do original. Assim, uma
        # atualização de membro feita na fase 2 (ex.: grupo sem uso cujo membro foi
        # renomeado) não é confundida com alteração; qualquer mudança fora do plano
        # continua mantendo o objeto.
        from panorama_cleanup.inventory import build_inventory
        expected = apply_plan(base, operations, copies, phases=(1, 2, 3)).tree
        planned_values = {r.xpath: r.value for r in build_inventory(expected).records}
        candidates = review_candidates(converted, an_converted, operations, planned_values,
                                       keep_types)
        if keep_types:
            log.info("Tipos mantidos por decisão do usuário: %s", ", ".join(keep_types))
        confirmed = deletion_operations(candidates)
        log.info("Candidatos: %d; exclusão confirmada: %d", len(candidates), len(confirmed))

        result = apply_plan(converted, confirmed, [], phases=(4,))
        xml_path = ensure_writable_target(
            settings.xml_output_dir / ctx.run_id / f"{ctx.input_path.stem}.final.xml",
            settings, ctx.input_path)
        if xml_path.resolve() == converted_path.resolve():
            raise AnalysisError("Destino coincide com o XML convertido")
        xml_path.parent.mkdir(parents=True, exist_ok=True)
        xml_path.write_bytes(serialize(result.tree, converted_path.read_bytes()))
        xml_sha = sha256_file(xml_path)

        final = parse_backup(xml_path)
        checks, policy_rows, _ = validate_transformation(converted, final, result, confirmed, phases=(4,))
        checks += validate_end_to_end(base, final, rule_renames, result.deleted_xpaths)
        checks.append(_rule_equivalence_check(original, final))
        verify_unchanged(ctx.input_path, ctx.input_sha256)
        verify_unchanged(converted_path, converted_sha)
        for c in checks:
            (log.info if c.passed else log.error)("%s %s: %s", "OK" if c.passed else "FALHA",
                                                  c.name, c.detail)
        reports = write_cleanup_reports(an_converted, candidates, result, checks, policy_rows, ctx,
                                        settings, plan_run, convert_run, xml_path, xml_sha)
        verify_unchanged(ctx.input_path, ctx.input_sha256)
    except PanoramaCleanupError as exc:
        log.error("%s", exc)
        write_manifest(ctx, settings, {"status": "error", "stage": "cleanup", "error": str(exc),
                                       "log": str(log_file)})
        return EXIT_ERROR

    passed = all(c.passed for c in checks)
    write_manifest(ctx, settings, {
        "status": "ok" if passed else "error", "stage": "cleanup", "plan_run": plan_run,
        "convert_run": convert_run, "converted_xml_sha256": converted_sha,
        "output_xml": {"path": str(xml_path), "sha256": xml_sha}, "phases_applied": [4],
        "candidates": len(candidates), "deleted": len(confirmed), "keep_types": list(keep_types),
        "kept": len(candidates) - len(confirmed),
        "checks_passed": sum(c.passed for c in checks), "checks_total": len(checks),
        "input_unchanged": True, "log": str(log_file), "outputs": report_hashes(reports),
    })
    if not passed:
        log.error("Integridade NÃO APROVADA; o XML final não deve ser utilizado")
        return EXIT_ERROR
    log.info("XML final: %s", xml_path)
    return EXIT_OK


def load_cleanup_run(run_dir: Path, ctx, plan_run: str, convert_run: str) -> tuple:
    import csv
    import json

    manifest_path = Path(run_dir) / "manifest.json"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise AnalysisError(f"Manifest do run cleanup ilegível: {manifest_path}: {exc}")
    if manifest.get("stage") != "cleanup" or manifest.get("status") != "ok":
        raise AnalysisError(f"{manifest_path}: não é um run APPLY cleanup concluído com sucesso")
    if (manifest["input"]["sha256"] != ctx.input_sha256 or manifest.get("plan_run") != plan_run
            or manifest.get("convert_run") != convert_run):
        raise AnalysisError("O run cleanup não corresponde a este backup, plano e conversão")
    if manifest["checks_passed"] != manifest["checks_total"]:
        raise AnalysisError("O run cleanup não passou em todas as verificações")
    xml = Path(manifest["output_xml"]["path"])
    if _existing_sha256(xml) != manifest["output_xml"]["sha256"]:
        raise AnalysisError(f"{xml} foi alterado após a Etapa 6")
    deleted = manifest_path.parent / "deleted-objects.csv"
    if _existing_sha256(deleted) != manifest["outputs"].get("deleted-objects.csv"):
        raise AnalysisError(f"{deleted} foi alterado após a Etapa 6")
    with open(deleted, encoding="utf-8", newline="") as fh:
        rows = list(csv.DictReader(fh))
    return manifest["run_id"], xml, manifest["output_xml"]["sha256"], rows


def run_commands(input_path, plan_dir, convert_dir, cleanup_dir, settings) -> int:
    """PLAN --stage commands: gera comandos CLI/XML API; não executa nada."""
    import subprocess

    from panorama_cleanup import generate_cli as gen
    from panorama_cleanup.report_generator import write_command_reports

    ctx = create_run_context("plan", input_path, label="commands")
    log_file = configure_logging(settings, ctx.run_id)
    log = logging.getLogger(LOGGER_NAME)
    log.info("Run %s (commands): entrada %s sha256=%s", ctx.run_id, ctx.input_path, ctx.input_sha256)
    try:
        original = parse_backup(ctx.input_path)
        lp = plan_with_localization(original)
        an = lp.analysis
        plan_run, operations, _ = load_approved_plan(plan_dir, ctx, lp)
        convert_run, converted_path, converted_sha, _ = load_convert_run(convert_dir, ctx, plan_run)
        cleanup_run, final_path, final_sha, deletions = load_cleanup_run(
            cleanup_dir, ctx, plan_run, convert_run)
        converted, final = parse_backup(converted_path), parse_backup(final_path)

        api_ops = gen.build_api_ops(operations, deletions, converted, final, original)
        checks = []
        add = lambda n, ok, d: checks.append((n, bool(ok), d))  # noqa: E731
        add("XML API fases 0-3 sobre o original = XML convertido",
            gen.same_config(gen.simulate_xmlapi(original, [o for o in api_ops if o.phase < 4]), converted),
            "simulação set/edit/delete")
        add("XML API fases 0-4 sobre o original = XML final",
            gen.same_config(gen.simulate_xmlapi(original, api_ops), final), "simulação set/edit/delete")
        add("fase 4 = exclusões confirmadas na Etapa 6",
            sorted(o.xpath for o in api_ops if o.phase == 4) == sorted(d["xpath"] for d in deletions),
            f"{len(deletions)} exclusões")
        rule_deletes = [o.xpath for o in api_ops if o.action == "delete" and "/rules/entry[" in o.xpath]
        add("nenhuma regra criada ou removida", not rule_deletes
            and all("/rules/" not in o.xpath for o in api_ops if o.action == "set"),
            f"exclusões em rulebase: {rule_deletes[:3]}")
        tag_edits = [o.xpath for o in api_ops if o.action == "edit"
                     and o.xpath.rsplit("/", 1)[-1] in ("tag", "group-tag")]
        add("nenhuma edição de campo de tag", not tag_edits, f"{tag_edits[:3]}")

        out_dir = settings.cli_dir / ctx.run_id
        from panorama_cleanup.safety import ensure_writable_target
        ensure_writable_target(out_dir / "00-LEIA-ME.md", settings, ctx.input_path)
        inv_types = {r.xpath: r.object_type for r in an.inventory.records}
        cli_missing = 0
        for op in api_ops:
            try:
                gen.cli_commands(op)
            except gen.CommandError:
                cli_missing += 1
        meta = {
            "run_id": ctx.run_id, "plan_run": plan_run, "convert_run": convert_run,
            "cleanup_run": cleanup_run, "panos": an.inventory.metadata.config_version,
            "panos_detail": an.inventory.metadata.detail_version, "input_sha": ctx.input_sha256,
            "converted_sha": converted_sha, "final_sha": final_sha,
            "backup_name": ctx.input_path.name, "final_name": final_path.name,
            "device_groups": [dg.name for dg in an.inventory.device_groups],
            "containers": gen.changed_containers(api_ops), "cli_missing": cli_missing,
        }
        from panorama_cleanup.cli_reference import check_by_version
        version = ".".join(an.inventory.metadata.config_version.split(".")[:2])
        reference = next((r / "docs" / "referencias" / f"pan-os-{version}" / "ngfw-cli.md"
                          for r in (settings.root, PROJECT_ROOT)
                          if (r / "docs" / "referencias" / f"pan-os-{version}" / "ngfw-cli.md").is_file()),
                         None)
        cli_lines = []
        for op in api_ops:
            try:
                cli_lines += gen.cli_commands(op)
            except gen.CommandError:
                pass
        checked = check_by_version(cli_lines, reference, version) if reference else []
        in_target = sum(1 for _, found, _ in checked if version in found)
        other = sum(1 for _, found, _ in checked if found and version not in found)
        undocumented = [line for line, found, _ in checked if not found]
        from panorama_cleanup.cli_reference import hierarchy_sections, load_section_patterns
        panorama_versions = sorted(
            (v for v, k in hierarchy_sections(reference) if k == "configure"
             and load_section_patterns(reference, v).get("device-group")),
            key=lambda v: tuple(map(int, v.split(".")))) if reference else []
        other_versions = panorama_versions
        meta.update(cli_checked=len(checked), cli_in_target=in_target, cli_other=other,
                    cli_other_versions=", ".join(other_versions), cli_version=version,
                    cli_validated=bool(checked) and in_target == len(checked))
        # Verificação com três estados: None = pendente (não bloqueia a XML API).
        add("CLI: nenhuma linha fora de qualquer hierarquia documentada do guia",
            reference is not None and not undocumented,
            f"sem correspondência em nenhuma versão: {len(undocumented)} {undocumented[:3]}")
        pending_detail = (f"{in_target} de {len(checked)} linhas na hierarquia PAN-OS {version} do guia; "
                          f"{other} somente em hierarquias de outras versões; a hierarquia de Panorama "
                          f"(device-group) só existe no guia para {', '.join(other_versions) or '-'}. "
                          f"CLI exige validação em "
                          f"Panorama {version} de laboratório; usar XML API.")
        checks.append((f"CLI validada para PAN-OS {version}",
                       True if meta["cli_validated"] else None, pending_detail))
        files = gen.write_command_files(out_dir, api_ops, inv_types, meta)
        # Roteiro de testes de política para os firewalls (Etapa 9).
        import json as _json
        from panorama_cleanup.policy_tests import build_tests, check_ops_lines, load_ops_patterns
        decisions = {d["xpath"]: d for d in _json.loads(
            (Path(plan_dir) / "plan.json").read_text(encoding="utf-8"))["decisions"]}
        _, _, _, applied_renames = load_convert_run(convert_dir, ctx, plan_run)
        tests = build_tests(an, decisions, applied_renames)
        ops_ok = check_ops_lines([t.command for t in tests],
                                 load_ops_patterns(reference, version)) if reference else []
        bad_tests = [line for line, ok in ops_ok if not ok]
        add(f"testes de política documentados na hierarquia operacional (firewall) PAN-OS {version}",
            reference is not None and not bad_tests,
            f"{len(tests) - len(bad_tests)} de {len(tests)} comandos; fora da hierarquia: {bad_tests[:3]}")
        renamed_rules = {x for x, _, _ in applied_renames}
        tested = {(t.rulebase, t.rule) for t in tests if t.reason == "referência renomeada"}
        devices = {dg.name: dg.devices for dg in an.inventory.device_groups}
        test_file = out_dir / "80-testes-de-politica.cli"
        lines = [
            "# Testes de política (CLI do FIREWALL, modo operacional). NÃO alteram configuração.",
            f"# Sintaxe conferida na hierarquia operacional PAN-OS {version} do PAN-OS CLI Quick Start.",
            "# Método: executar ANTES da mudança e salvar a saída (linha de base); executar de novo",
            "# após o push do Device Group; a saída de cada comando deve ser IDÊNTICA à linha de base.",
            f"# Regras com referência renomeada: {len(renamed_rules)}; com teste gerado: {len(tested)}",
            "# (regras sem destino IP resolvível, ex.: FQDN ou any, não têm teste automático).",
            "",
        ]
        for dg in sorted({t.device_group for t in tests}):
            lines.append(f"# ===== Device Group {dg} | firewalls: {' '.join(devices.get(dg, ()))}")
            for t in (t for t in tests if t.device_group == dg):
                lines.append(f"# {t.rulebase} | {t.rule} | {t.reason}")
                lines.append(t.command)
            lines.append("")
        test_file.write_text("\n".join(lines) + "\n", encoding="utf-8")
        files[test_file.name] = test_file
        meta["policy_tests"] = len(tests)

        if checked:
            import csv as _csv
            syntax_csv = out_dir / "cli-syntax-check.csv"
            with open(syntax_csv, "w", encoding="utf-8", newline="") as fh:
                writer = _csv.writer(fh)
                writer.writerow(("line", f"documented_for_{version}", "documented_versions",
                                 "documented_pattern"))
                for line, found, pattern in checked:
                    writer.writerow((line, "sim" if version in found else "NÃO", " ".join(found),
                                     pattern))
            files[syntax_csv.name] = syntax_csv

        # Sem bash (Windows sem Git Bash/WSL) a sintaxe não é conferida aqui: fica PENDENTE,
        # sem bloquear; o script é o mesmo modelo fixo conferido nos testes automatizados.
        import shutil
        bash = shutil.which("bash")
        if bash:
            syntax = subprocess.run([bash, "-n", str(files["run-xmlapi.sh"])],
                                    capture_output=True, text=True)
            checks.append(("run-xmlapi.sh: sintaxe bash válida (bash -n, sem executar)",
                           syntax.returncode == 0, syntax.stderr.strip()))
        else:
            checks.append(("run-xmlapi.sh: sintaxe bash válida (bash -n, sem executar)", None,
                           "bash não encontrado nesta máquina; executar o script em Git Bash ou "
                           "WSL e conferir com bash -n antes de usar"))
        malformed = [name for name, path in files.items() if name.endswith(".tsv")
                     and any(len(line.split("\t")) != 5
                             for line in path.read_text(encoding="utf-8").splitlines())]
        add("arquivos .tsv com 5 colunas em todas as linhas", not malformed, f"{malformed}")
        readme_text = files["00-LEIA-ME.md"].read_text(encoding="utf-8")
        not_in_order = [name for name in files if name.endswith(".tsv")
                        and f"`{name}`" not in readme_text]
        add("todo arquivo de fase gerado consta da ordem de execução do 00-LEIA-ME.md",
            not not_in_order, f"{not_in_order}")
        commit_lines = [name for name, path in files.items() if name.endswith((".cli", ".tsv"))
                        for line in path.read_text(encoding="utf-8").splitlines()
                        if not line.startswith("#") and "commit" in line.split("\t")[2:3] + line.split()[:1]]
        add("nenhum commit ou push nos arquivos executáveis", not commit_lines, f"{commit_lines[:3]}")
        add("cobertura da CLI", True, f"{len(api_ops) - cli_missing} de {len(api_ops)} operações; "
            f"{cli_missing} só pela XML API")
        verify_unchanged(ctx.input_path, ctx.input_sha256)
        for name, ok, detail in checks:
            label = "PENDENTE" if ok is None else ("OK" if ok else "FALHA")
            (log.warning if ok is None else log.info if ok else log.error)("%s %s: %s", label, name, detail)
        reports = write_command_reports(checks, files, api_ops, ctx, settings, meta)
    except (PanoramaCleanupError, gen.CommandError) as exc:
        log.error("%s", exc)
        write_manifest(ctx, settings, {"status": "error", "stage": "commands", "error": str(exc),
                                       "log": str(log_file)})
        return EXIT_ERROR

    passed = all(ok for _, ok, _ in checks if ok is not None)
    write_manifest(ctx, settings, {
        "status": "ok" if passed else "error", "stage": "commands",
        "cli_validated_for_version": meta["cli_validated"], "recommended_interface": "Panorama XML API", "plan_run": plan_run,
        "convert_run": convert_run, "cleanup_run": cleanup_run, "commands_dir": str(out_dir),
        "operations": len(api_ops), "cli_missing": cli_missing,
        "checks_passed": sum(1 for _, ok, _ in checks if ok), "checks_total": len(checks),
        "checks_pending": sum(1 for _, ok, _ in checks if ok is None), "executed": False, "input_unchanged": True, "log": str(log_file),
        "outputs": report_hashes(reports), "command_files": report_hashes(list(files.values())),
    })
    if not passed:
        log.error("Geração NÃO APROVADA; os comandos não devem ser utilizados")
        return EXIT_ERROR
    log.info("Comandos em %s (nada foi executado)", out_dir)
    return EXIT_OK


def run_validate(input_path, modified_path, plan_dir, convert_dir, cleanup_dir, settings,
                 exported: bool = False) -> int:
    """VALIDATE: comparação integral original x final; resultado APROVADO ou NÃO APROVADO."""
    import csv
    import json

    from panorama_cleanup.final_validation import (
        APPROVED, Authorized, strip_exported_predefined, validate_final,
    )
    from panorama_cleanup.report_generator import write_validation_reports

    ctx = create_run_context("validate", input_path)
    log_file = configure_logging(settings, ctx.run_id)
    log = logging.getLogger(LOGGER_NAME)
    log.info("Run %s: original %s sha256=%s", ctx.run_id, ctx.input_path, ctx.input_sha256)
    try:
        original = parse_backup(ctx.input_path)
        lp = plan_with_localization(original)
        plan_run, _, _ = load_approved_plan(plan_dir, ctx, lp)
        convert_run, _, _, rule_renames = load_convert_run(convert_dir, ctx, plan_run)
        cleanup_run, final_path, final_sha, deleted = load_cleanup_run(
            cleanup_dir, ctx, plan_run, convert_run)
        modified_sha = _existing_sha256(modified_path)
        if not exported and modified_sha != final_sha:
            raise AnalysisError(f"{modified_path} não é o XML final do run {cleanup_run} "
                                f"(sha256 {modified_sha} != {final_sha})")
        cleanup_manifest = json.loads((Path(cleanup_dir) / "manifest.json").read_text(encoding="utf-8"))

        decisions = {d["xpath"]: d for d in json.loads(
            (Path(plan_dir) / "plan.json").read_text(encoding="utf-8"))["decisions"]}
        local, copies = [], {}
        with open(Path(convert_dir) / "reference-updates.csv", encoding="utf-8", newline="") as fh:
            for r in csv.DictReader(fh):
                if r["status"] != "aplicado":
                    raise AnalysisError(f"Atualização não aplicada na Etapa 5: {r}")
                if r["applies_to"] == "objeto local":
                    local.append((r["source_xpath"], r["field"], r["old"], r["new"]))
                elif r["applies_to"] == "cópia criada em Shared":
                    copies.setdefault(r["source_xpath"], []).append((r["field"], r["old"], r["new"]))
        plan_ops = json.loads((Path(plan_dir) / "plan.json").read_text(encoding="utf-8"))["operations"]
        descriptions = {o["xpath"]: o["value"] for o in plan_ops if o["operation"] == "SET_DESCRIPTION"}
        auth = Authorized(decisions, rule_renames, local, copies,
                          {d["xpath"] for d in deleted}, tuple(cleanup_manifest.get("keep_types", ())),
                          descriptions)

        final = parse_backup(modified_path)
        predefined_removed = exported and strip_exported_predefined(lp.tree, final)
        # Base: original + cópias locais da fase 0 (transparentes); as verificações de
        # estrutura e origem dos objetos seguem o plano. A verificação independente de
        # IP, porta e application é feita contra o ORIGINAL verdadeiro.
        v = validate_final(lp.tree, final, auth,
                           ignore_sections=("/config/readonly",) if exported else ())
        if predefined_removed:
            v.add("estrutura", "/config/predefined do XML exportado ignorada (conteúdo embutido do "
                  "Panorama, ausente no original)", True, "leitura por type=config&action=get")
        eq = _rule_equivalence_check(original, final)
        v.add("independente", eq.name, eq.passed, eq.detail)
        verify_unchanged(ctx.input_path, ctx.input_sha256)
        verify_unchanged(modified_path, modified_sha)
        for area, name, ok, detail in v.checks:
            (log.info if ok else log.error)("%s [%s] %s: %s", "OK" if ok else "FALHA", area, name, detail)
        meta = {"plan_run": plan_run, "convert_run": convert_run, "cleanup_run": cleanup_run,
                "final_path": str(modified_path), "final_sha": modified_sha,
                "generated_final_sha": final_sha, "exported": exported,
                "keep_types": list(auth.kept_types)}
        reports = write_validation_reports(v, ctx, settings, meta)
        verify_unchanged(ctx.input_path, ctx.input_sha256)
    except PanoramaCleanupError as exc:
        log.error("%s", exc)
        write_manifest(ctx, settings, {"status": "error", "result": "NÃO APROVADO",
                                       "error": str(exc), "log": str(log_file)})
        return EXIT_ERROR

    write_manifest(ctx, settings, {
        "status": "ok", "result": v.result, **meta, "summary": v.summary,
        "checks_passed": sum(ok for _, _, ok, _ in v.checks), "checks_total": len(v.checks),
        "input_unchanged": True, "log": str(log_file), "outputs": report_hashes(reports),
    })
    log.info("Resultado: %s", v.result)
    return EXIT_OK if v.result == APPROVED else EXIT_ERROR


IMPLEMENTED = {"inventory": run_inventory, "analyze": run_analyze, "plan": run_plan,
               "apply": run_apply, "validate": run_validate}
