"""Ponto de entrada do panorama-cleanup dentro da skill (claude.ai e outros agentes).

Uso:
    python3 run.py --check
    python3 run.py --workdir <pasta> <modo> [opções]

Usa o pacote e o defusedxml que vêm na skill (_vendor/). lxml e openpyxl precisam existir
no ambiente (--check confere). --workdir é obrigatório: a pasta da skill pode ser somente leitura e
as saídas de cada backup precisam ficar isoladas.
"""

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.dont_write_bytecode = True
sys.path[:0] = [HERE, os.path.join(HERE, "_vendor")]

MIN_PYTHON = (3, 9)


def check() -> int:
    problems = []
    print(f"Python {sys.version.split()[0]} ({sys.executable})")
    if sys.version_info < MIN_PYTHON:
        problems.append(f"Python >= {'.'.join(map(str, MIN_PYTHON))} necessário")
    for module in ("lxml.etree", "defusedxml", "openpyxl", "panorama_cleanup.cli"):
        try:
            mod = __import__(module, fromlist=["_"])
        except Exception as exc:  # noqa: BLE001 - relatar qualquer falha de import
            problems.append(f"{module}: {exc}")
            continue
        version = getattr(mod, "__version__", "") or getattr(mod, "LXML_VERSION", "")
        origin = getattr(mod, "__file__", "") or ""
        where = "skill" if origin.startswith(HERE) else "ambiente"
        print(f"  {module}: ok {version} [{where}]")
    refs = os.path.join(HERE, "docs", "referencias")
    versions = sorted(d[len("pan-os-"):] for d in (os.listdir(refs) if os.path.isdir(refs) else [])
                      if os.path.isfile(os.path.join(refs, d, "ngfw-cli.md")))
    print(f"  guias de CLI: {', '.join(versions) or 'nenhum (Etapa 7 indisponível)'}")
    if problems:
        print("AMBIENTE COM PROBLEMAS:")
        for p in problems:
            print(f"  - {p}")
        missing = [m for m, pkg in (("lxml", "lxml"), ("openpyxl", "openpyxl"))
                   if any(p.startswith(m) for p in problems)]
        if missing:
            print(f"Sugestão: pip install {' '.join(missing)} e rode --check de novo.")
        return 1
    print("AMBIENTE OK")
    return 0


def main(argv) -> int:
    if argv[:1] == ["--check"]:
        return check()
    has_workdir = any(a == "--workdir" or a.startswith("--workdir=") for a in argv)
    if not has_workdir and not os.environ.get("PANORAMA_CLEANUP_HOME") \
            and not any(a in ("-h", "--help", "--version") for a in argv):
        print("ERRO: informe --workdir <pasta de trabalho do backup>", file=sys.stderr)
        return 1
    from panorama_cleanup.cli import main as cli_main
    return cli_main(argv)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
