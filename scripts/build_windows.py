"""Build and verify the Windows GUI and CLI using the current repository source."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
from importlib import metadata
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from panorama_cleanup import __version__

BUILD = ROOT / ".build" / "windows"
DIST = ROOT / "dist" / "windows"


def git(*args):
    return subprocess.check_output(["git", "-c", "safe.directory=" + str(ROOT), *args], cwd=ROOT, text=True).strip()


def run(args, **kwargs):
    subprocess.run([str(a) for a in args], cwd=ROOT, check=True, **kwargs)


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def smoke(gui, cli):
    from desktop_support import accept_result, atomic_json, new_session

    version = subprocess.check_output([str(cli), "--version"], text=True, cwd=ROOT).strip()
    if version != "panorama-cleanup " + __version__:
        raise RuntimeError("Unexpected CLI version: " + version)
    evidence = {"cli_version": version, "steps": []}
    with tempfile.TemporaryDirectory(prefix="panorama-release-") as temporary:
        base = Path(temporary)
        ui = base / "ui.json"
        run([gui, "--ui-smoke", ui], timeout=120)
        evidence["ui"] = json.loads(ui.read_text(encoding="utf-8"))
        if not evidence["ui"].get("ui_created"):
            raise RuntimeError("GUI initialization failed")
        fixture = ROOT / "tests" / "fixtures" / "xml" / "panorama_deps.xml"
        root, state = new_session(fixture, base / "Project with spaces")
        for step in ("inventory", "analyze", "plan", "convert", "cleanup", "validate"):
            request = base / (step + ".json")
            atomic_json(request, {"step": step, "root": str(root), "keep_tags": True})
            process = subprocess.run([str(gui), "--engine", str(request)], cwd=ROOT, timeout=180)
            result = json.loads(request.with_suffix(".result.json").read_text(encoding="utf-8"))
            if process.returncode or result["code"]:
                raise RuntimeError(f"Packaged {step} failed: " + request.with_suffix(".log").read_text(encoding="utf-8"))
            accept_result(state, step, result["run"], True)
            atomic_json(root / "desktop-session.json", state)
            evidence["steps"].append(step)
        final = json.loads((Path(state["runs"]["cleanup"]) / "manifest.json").read_text(encoding="utf-8"))["output_xml"]["path"]
        comparison = base / "comparison.json"
        traffic_csv = base / "traffic.csv"
        traffic_csv.write_text("Rule,Device Group,Type,Action,Application\nRJ-R1,DG-RJ,TRAFFIC,allow,ssl\n", encoding="utf-8")
        atomic_json(comparison, {"step": "compare", "initial": str(fixture), "final": final,
                                 "output": str(base / "comparison.xlsx"), "traffic_csv": str(traffic_csv)})
        run([gui, "--engine", comparison], timeout=120)
        result = json.loads(comparison.with_suffix(".result.json").read_text(encoding="utf-8"))
        if result["code"] or not Path(result["report"]).is_file():
            raise RuntimeError("Packaged comparison failed")
        evidence["comparison"] = result["summary"]
        from openpyxl import load_workbook
        workbook = load_workbook(result["report"], read_only=True)
        try:
            evidence["traffic_csv"] = any(
                row[1] == "RJ-R1" for row in workbook["Fluxos observados"].iter_rows(min_row=2, values_only=True))
        finally:
            workbook.close()
        if not evidence["traffic_csv"]:
            raise RuntimeError("Packaged Traffic CSV linkage failed")
        cli_root = base / "cli"
        cli_root.mkdir()
        run([cli, "--workdir", cli_root, "inventory", "--input", fixture], timeout=120,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        evidence["cli_inventory"] = len(list((cli_root / "output" / "reports").glob("*/manifest.json"))) == 1
    return evidence


def notices():
    directory = DIST / "licenses"
    directory.mkdir(exist_ok=True)
    for name in ("lxml", "openpyxl", "et_xmlfile", "defusedxml", "pyinstaller"):
        distribution = metadata.distribution(name)
        for item in distribution.files or ():
            if any(part.lower().startswith(("license", "copying")) for part in item.parts):
                source = Path(distribution.locate_file(item))
                if source.is_file():
                    destination = directory / (name + "-" + source.name)
                    if destination.exists() and destination.read_bytes() != source.read_bytes():
                        destination = directory / (name + "-" + sha256(source)[:8] + "-" + source.name)
                    shutil.copyfile(source, destination)
    runtime = Path(sys.base_prefix)
    for source, name in ((runtime / "LICENSE.txt", "Python-LICENSE.txt"),
                         (runtime / "tcl/tcl8.6/license.terms", "Tcl-LICENSE.txt"),
                         (runtime / "tcl/tk8.6/license.terms", "Tk-LICENSE.txt")):
        if source.is_file():
            shutil.copyfile(source, directory / name)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--skip-tests", action="store_true", help="Use after running the source test suite separately")
    args = parser.parse_args()
    if sys.platform != "win32" or platform.architecture()[0] != "64bit":
        raise SystemExit("Use a 64-bit Python on Windows.")
    BUILD.mkdir(parents=True, exist_ok=True)
    DIST.mkdir(parents=True, exist_ok=True)
    temporary = BUILD / "tmp"
    temporary.mkdir(exist_ok=True)
    os.environ["TEMP"] = os.environ["TMP"] = str(temporary)
    os.environ["GIT_CONFIG_COUNT"] = "1"
    os.environ["GIT_CONFIG_KEY_0"] = "safe.directory"
    os.environ["GIT_CONFIG_VALUE_0"] = str(ROOT)
    for candidate in (Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "Git/bin",
                      Path(os.environ.get("ProgramW6432", r"C:\Program Files")) / "Git/bin"):
        if (candidate / "bash.exe").is_file():
            os.environ["PATH"] = str(candidate) + os.pathsep + os.environ.get("PATH", "")
            break
    if not args.skip_tests:
        run([sys.executable, "-m", "pytest", "-q", "--junitxml", ROOT / ".build/source-tests.xml"])
    suites = ET.parse(ROOT / ".build/source-tests.xml").getroot().findall("testsuite")
    source_tests = {key: sum(int(s.get(key, 0)) for s in suites) for key in ("tests", "failures", "errors", "skipped")}
    source_tests["passed"] = source_tests["tests"] - sum(source_tests[key] for key in ("failures", "errors", "skipped"))
    if source_tests["failures"] or source_tests["errors"]:
        raise RuntimeError("The source test report contains failures")
    source_tests["skip_reasons"] = [node.get("message", "") for suite in suites for node in suite.findall("testcase/skipped")]
    common = [sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--onefile",
              "--distpath", DIST, "--workpath", BUILD / "pyinstaller", "--specpath", BUILD,
              "--paths", ROOT, "--collect-submodules", "panorama_cleanup",
              "--exclude-module", "pandas", "--exclude-module", "numpy", "--exclude-module", "pytest",
              "--add-data", str(ROOT / "config/settings.json") + os.pathsep + "config",
              "--add-data", str(ROOT / "docs/modelo-etapa-09.md") + os.pathsep + "docs",
              "--add-data", str(ROOT / "LICENSE") + os.pathsep + "."]
    run([*common, "--windowed", "--name", "PanoramaCleanup", ROOT / "desktop.py"])
    run([*common, "--console", "--name", "panorama-cleanup", ROOT / "scripts/windows_cli.py"])
    gui, cli = DIST / "PanoramaCleanup.exe", DIST / "panorama-cleanup.exe"
    checks = smoke(gui, cli)
    info = {"version": __version__, "commit": git("rev-parse", "HEAD"),
            "source_dirty": bool(git("status", "--porcelain")),
            "built_at": datetime.now(timezone.utc).isoformat(), "python": platform.python_version(),
            "platform": platform.platform(), "checks": checks, "source_tests": source_tests,
            "packages": {name: metadata.version(name) for name in ("lxml", "openpyxl", "defusedxml", "pyinstaller")},
            "sha256": {path.name: sha256(path) for path in (gui, cli)}}
    (DIST / "build-info.json").write_text(json.dumps(info, ensure_ascii=False, indent=2), encoding="utf-8")
    shutil.copyfile(ROOT / "docs/windows-executable.md", DIST / "LEIA-ME.md")
    shutil.copyfile(ROOT / "LICENSE", DIST / "LICENSE.txt")
    notices()
    archive = DIST / f"PanoramaCleanup-v{__version__}-windows-x64.zip"
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as output:
        for path in [gui, cli, DIST / "build-info.json", DIST / "LEIA-ME.md", DIST / "LICENSE.txt", *sorted((DIST / "licenses").glob("*"))]:
            output.write(path, path.relative_to(DIST))
    hashes = [f"{sha256(path)}  {path.name}" for path in (gui, cli, archive, DIST / "build-info.json")]
    (DIST / "SHA256SUMS.txt").write_text("\n".join(hashes) + "\n", encoding="utf-8")
    print(json.dumps(info, ensure_ascii=False, indent=2))
    print(archive)


if __name__ == "__main__":
    main()
