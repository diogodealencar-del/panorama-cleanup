"""Gera as skills do panorama-cleanup a partir de skill/ (fonte única).

Saídas:
- claude-plugin/skills/panorama-cleanup/SKILL.md   (plugin do Claude Code; versionado no git)
- dist/panorama-cleanup-skill-<versão>.zip           (claude.ai e agentes compatíveis com SKILL.md)

O zip contém somente uma lista fechada de arquivos: SKILL.md, o pacote Python, a
configuração padrão, o modelo da Etapa 9, os guias de CLI presentes em
docs/referencias/ e as bibliotecas Python puras necessárias (com licenças). Nenhum
backup, relatório, log ou comando gerado entra. O zip é determinístico (mesma entrada,
mesmos bytes) e leva um MANIFEST.json com o sha256 de cada arquivo e o commit de origem.

Uso:
    python scripts/build_skill.py            # gera os dois
    python scripts/build_skill.py --check    # só confere se o SKILL.md do plugin está atualizado
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata as md
import json
import re
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SKILL_SRC = ROOT / "skill"
PLUGIN_SKILL = ROOT / "claude-plugin" / "skills" / "panorama-cleanup" / "SKILL.md"
PLUGIN_JSON = ROOT / "claude-plugin" / ".claude-plugin" / "plugin.json"
DIST = ROOT / "dist"
TOP = "panorama-cleanup"
ZIP_DATE = (1980, 1, 1, 0, 0, 0)

DESCRIPTION = (
    "Ferramenta determinística incluída (execute-a; nunca analise o XML manualmente). "
    "Use para inventariar, analisar, limpar ou consolidar objetos (address, address group, "
    "service, service group, applications, tags etc.) de um backup XML do Panorama Palo Alto, "
    "centralizar objetos em Shared, resolver conflitos de nome entre Device Groups, gerar "
    "comandos XML API/CLI e validar o XML final ou um candidate exportado."
)

# Bibliotecas Python puras levadas no zip: (distribuição, pacotes de import).
# O claude.ai aceita no máximo 200 arquivos e nenhum zip aninhado: openpyxl (~190 arquivos)
# e lxml vêm do ambiente; run.py --check confere e a skill orienta o pip install se faltar.
VENDOR = (("defusedxml", ("defusedxml",)),)
FROM_ENVIRONMENT = ("lxml", "openpyxl")

VARIANTS = {
    "plugin": {"CMD": "panorama-cleanup", "SESSION_VARS": "`W` e `B`",
               "MODELO_ETAPA9": "`docs/modelo-etapa-09.md` do clone (copie para `$W/`)"},
    "chat": {"CMD": 'python3 "$SKILL_DIR/scripts/run.py"', "SESSION_VARS": "`W`, `B` e `SKILL_DIR`",
             "MODELO_ETAPA9": "`$SKILL_DIR/docs/modelo-etapa-09.md` (copie para `$W/`)"},
}


class BuildError(Exception):
    pass


def version() -> str:
    text = (ROOT / "panorama_cleanup" / "__init__.py").read_text(encoding="utf-8")
    v = re.search(r'__version__ = "([^"]+)"', text).group(1)
    py = re.search(r'^version = "([^"]+)"', (ROOT / "pyproject.toml").read_text(), re.M).group(1)
    plugin = json.loads(PLUGIN_JSON.read_text(encoding="utf-8"))["version"]
    if not v == py == plugin:
        raise BuildError(f"versões divergentes: __init__ {v}, pyproject {py}, plugin.json {plugin}")
    return v


def render(variant: str, ver: str) -> str:
    text = (SKILL_SRC / f"{variant}.md").read_text(encoding="utf-8")
    parts = {
        "REGRAS": (SKILL_SRC / "partials" / "regras.md").read_text(encoding="utf-8").strip(),
        "ETAPAS": (SKILL_SRC / "partials" / "etapas.md").read_text(encoding="utf-8").strip(),
    }
    for key, value in parts.items():
        text = text.replace("{{" + key + "}}", value)
    for key, value in {**VARIANTS[variant], "VERSION": ver, "DESCRIPTION": DESCRIPTION}.items():
        text = text.replace("{{" + key + "}}", value)
    leftover = re.findall(r"\{\{[A-Z_0-9]+\}\}", text)
    if leftover:
        raise BuildError(f"marcadores não substituídos em {variant}: {sorted(set(leftover))}")
    return text


def _git_commit() -> str:
    try:
        commit = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True,
                                text=True, check=True).stdout.strip()
        dirty = subprocess.run(["git", "status", "--porcelain"], cwd=ROOT, capture_output=True,
                               text=True, check=True).stdout.strip()
        return commit + ("-dirty" if dirty else "")
    except (OSError, subprocess.CalledProcessError):
        return "desconhecido"


def _vendor_files():
    """[(caminho em scripts/_vendor/, bytes)] das bibliotecas puras e de suas licenças."""
    out = []
    for dist_name, packages in VENDOR:
        try:
            dist = md.distribution(dist_name)
        except md.PackageNotFoundError:
            raise BuildError(f"{dist_name} não instalado no Python do build ({sys.executable})")
        for f in dist.files or ():
            parts = Path(str(f)).parts
            path = Path(dist.locate_file(f))
            if parts[0] in packages and path.suffix in (".py", ".typed"):
                out.append((f.as_posix(), path.read_bytes()))
            elif parts[0].endswith(".dist-info") and re.search(r"LICEN[CS]E|COPYING", parts[-1], re.I):
                out.append((f"licenses/{dist_name}-{dist.version}/{parts[-1]}", path.read_bytes()))
        if not any(p.startswith(f"{packages[0]}/") for p, _ in out):
            raise BuildError(f"nenhum arquivo de {dist_name} encontrado")
    return out


def collect(ver: str):
    files = [("SKILL.md", render("chat", ver).encode("utf-8")),
             ("scripts/run.py", (SKILL_SRC / "run.py").read_bytes()),
             ("scripts/config/settings.json", (ROOT / "config" / "settings.json").read_bytes()),
             ("docs/modelo-etapa-09.md", (ROOT / "docs" / "modelo-etapa-09.md").read_bytes())]
    for py in sorted((ROOT / "panorama_cleanup").glob("*.py")):
        files.append((f"scripts/panorama_cleanup/{py.name}", py.read_bytes()))
    guides = sorted((ROOT / "docs" / "referencias").glob("pan-os-*/ngfw-cli.md"))
    for g in guides:
        files.append((f"scripts/docs/referencias/{g.parent.name}/ngfw-cli.md", g.read_bytes()))
    files.append(("scripts/docs/referencias/README.md",
                  (ROOT / "docs" / "referencias" / "README.md").read_bytes()))
    files += [(f"scripts/_vendor/{n}", b) for n, b in _vendor_files()]
    _check_allowed(files)
    return files, [g.parent.name for g in guides]


ALLOWED = re.compile(
    r"^(SKILL\.md|scripts/run\.py|scripts/config/settings\.json|docs/modelo-etapa-09\.md"
    r"|scripts/panorama_cleanup/[a-z_]+\.py"
    r"|scripts/docs/referencias/(README\.md|pan-os-\d+\.\d+/ngfw-cli\.md)"
    r"|scripts/_vendor/defusedxml/[\w/.-]+\.(py|typed)"
    r"|scripts/_vendor/licenses/[\w.-]+/[\w.-]+)$")
MAX_FILES = 200  # limite do claude.ai para o zip de uma skill (e nenhum zip aninhado)


def _zip_bytes(files) -> bytes:
    """Zip determinístico em memória."""
    import io
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        for name, data in sorted(files):
            info = zipfile.ZipInfo(name, date_time=ZIP_DATE)
            info.external_attr = 0o644 << 16
            info.compress_type = zipfile.ZIP_DEFLATED
            z.writestr(info, data, compresslevel=9)
    return buf.getvalue()


def _check_allowed(files) -> None:
    names = [n for n, _ in files]
    bad = [n for n in names if not ALLOWED.match(n)]
    if bad:
        raise BuildError(f"arquivos fora da lista permitida: {bad[:10]}")
    if len(names) != len(set(names)):
        raise BuildError("arquivos duplicados no zip")
    nested = [n for n in names if n.lower().endswith((".zip", ".whl", ".egg", ".pyz", ".jar"))]
    if nested:
        raise BuildError(f"arquivos compactados dentro do zip (não aceitos pelo claude.ai): {nested}")
    if len(names) + 1 > MAX_FILES:  # + MANIFEST.json
        raise BuildError(f"{len(names) + 1} arquivos no zip; o claude.ai aceita no máximo {MAX_FILES}")


def build_zip(out_dir: Path = DIST) -> Path:
    ver = version()
    files, guides = collect(ver)
    vendored = {d: md.version(d) for d, _ in VENDOR}
    manifest = {
        "name": TOP, "version": ver, "source_commit": _git_commit(),
        "cli_guides": guides, "vendored": vendored,
        "files": {n: hashlib.sha256(b).hexdigest() for n, b in sorted(files)},
        "from_environment": list(FROM_ENVIRONMENT),
    }
    files.append(("MANIFEST.json", (json.dumps(manifest, indent=2, ensure_ascii=False) + "\n").encode()))
    out_dir.mkdir(parents=True, exist_ok=True)
    target = out_dir / f"{TOP}-skill-{ver}.zip"
    tmp = target.with_suffix(".zip.tmp")
    tmp.write_bytes(_zip_bytes([(f"{TOP}/{n}", b) for n, b in files]))
    tmp.replace(target)
    return target


def write_plugin_skill() -> bool:
    """Atualiza o SKILL.md do plugin. Retorna True se o conteúdo mudou."""
    text = render("plugin", version())
    old = PLUGIN_SKILL.read_text(encoding="utf-8") if PLUGIN_SKILL.exists() else None
    if old != text:
        PLUGIN_SKILL.write_text(text, encoding="utf-8")
    return old != text


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--check", action="store_true",
                        help="só confere se o SKILL.md do plugin corresponde a skill/")
    args = parser.parse_args(argv)
    try:
        if args.check:
            ok = PLUGIN_SKILL.read_text(encoding="utf-8") == render("plugin", version())
            print("SKILL.md do plugin atualizado" if ok else
                  "SKILL.md do plugin desatualizado: rode python scripts/build_skill.py")
            return 0 if ok else 1
        changed = write_plugin_skill()
        target = build_zip()
    except BuildError as exc:
        print(f"ERRO: {exc}", file=sys.stderr)
        return 1
    print(f"Plugin: {PLUGIN_SKILL.relative_to(ROOT)} ({'atualizado' if changed else 'sem mudança'})")
    if changed:
        print("  O SKILL.md do plugin mudou. Se a versão ainda não foi aumentada desde a última "
              "publicação, aumente-a em plugin.json, __init__.py e pyproject.toml e rode o build de novo.")
    digest = hashlib.sha256(target.read_bytes()).hexdigest()
    print(f"Zip:    {target.relative_to(ROOT)} ({target.stat().st_size / 1e6:.1f} MB, sha256 {digest})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
