"""Publish verified Windows artifacts using GitHub credentials from Git or environment."""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
import subprocess
import urllib.error
import urllib.parse
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
REPOSITORY = "diogodealencar-del/panorama-cleanup"
API = "https://api.github.com/repos/" + REPOSITORY


def git(*args):
    return subprocess.check_output(["git", "-c", "safe.directory=" + str(ROOT), *args], cwd=ROOT, text=True).strip()


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def push(token, refspec):
    environment = os.environ.copy()
    header = base64.b64encode(("x-access-token:" + token).encode()).decode()
    environment.update({"GIT_CONFIG_COUNT": "2", "GIT_CONFIG_KEY_0": "safe.directory", "GIT_CONFIG_VALUE_0": str(ROOT),
                        "GIT_CONFIG_KEY_1": "http.https://github.com/.extraheader", "GIT_CONFIG_VALUE_1": "Authorization: Basic " + header})
    subprocess.run(["git", "push", "origin", refspec], cwd=ROOT, check=True, env=environment)


def credential():
    token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
    if token:
        return token
    request = "protocol=https\nhost=github.com\npath=" + REPOSITORY + ".git\n\n"
    process = subprocess.run(["git", "-c", "safe.directory=" + str(ROOT), "credential", "fill"],
                             input=request, text=True, capture_output=True, cwd=ROOT)
    values = dict(line.split("=", 1) for line in process.stdout.splitlines() if "=" in line)
    if not values.get("password"):
        raise RuntimeError("A credencial do GitHub está indisponível. Execute este publicador no terminal normal do Windows, com sua conta autenticada no Git.")
    return values["password"]


def api(token, url, *, method="GET", data=None, content_type="application/json"):
    headers = {"Authorization": "Bearer " + token, "Accept": "application/vnd.github+json",
               "X-GitHub-Api-Version": "2022-11-28", "User-Agent": "panorama-cleanup-windows-release"}
    if data is not None:
        headers["Content-Type"] = content_type
        data = json.dumps(data).encode("utf-8") if isinstance(data, dict) else data
    try:
        with urllib.request.urlopen(urllib.request.Request(url, data=data, method=method, headers=headers), timeout=180) as response:
            return json.load(response)
    except urllib.error.HTTPError as error:
        if error.code == 404 and method == "GET":
            return None
        raise RuntimeError(f"GitHub respondeu HTTP {error.code} para {method} {urllib.parse.urlsplit(url).path}") from None


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--tag", help="Tag nova para a release Windows")
    args = parser.parse_args()
    dist = ROOT / "dist" / "windows"
    info = json.loads((dist / "build-info.json").read_text(encoding="utf-8"))
    commit = git("rev-parse", "HEAD")
    remote = git("remote", "get-url", "origin").removesuffix(".git")
    if remote != "https://github.com/" + REPOSITORY:
        raise RuntimeError("O origin não corresponde ao repositório esperado.")
    if info["source_dirty"] or commit != info["commit"] or git("status", "--porcelain"):
        raise RuntimeError("O código precisa estar limpo e corresponder ao commit registrado na compilação.")
    for line in (dist / "SHA256SUMS.txt").read_text(encoding="utf-8").splitlines():
        expected, name = line.split("  ", 1)
        path = dist / name
        if path.parent != dist or digest(path) != expected:
            raise RuntimeError("Checksum divergente: " + name)
    names = ["PanoramaCleanup.exe", "panorama-cleanup.exe", f"PanoramaCleanup-v{info['version']}-windows-x64.zip", "SHA256SUMS.txt", "build-info.json"]
    files = [dist / name for name in names]
    token = credential()
    repository = api(token, API)
    if not repository or not repository.get("permissions", {}).get("push"):
        raise RuntimeError("A conta autenticada não tem permissão de escrita neste repositório.")
    tag = args.tag or f"v{info['version']}-windows.1"
    git("check-ref-format", "refs/tags/" + tag)
    existing_tags = git("tag", "--list", tag)
    if existing_tags and git("rev-parse", "refs/tags/" + tag + "^{commit}") != commit:
        raise RuntimeError("A tag local já aponta para outro commit. Escolha outra com --tag.")
    release = api(token, API + "/releases/tags/" + urllib.parse.quote(tag, safe=""))
    if release and release["target_commitish"] != commit:
        raise RuntimeError("A release existente identifica outro commit. Escolha outra tag.")
    push(token, "HEAD:refs/heads/build/windows-v" + info["version"])
    if not existing_tags:
        git("tag", tag, commit)
    push(token, "refs/tags/" + tag)
    if not release:
        body = (f"Executáveis Windows x64 do motor {info['version']}, compilados a partir do commit {commit}.\n\n"
                "A interface inclui as etapas de análise e saneamento, comparação de dois XMLs com relatório Excel e cruzamento opcional de logs de tráfego CSV. A CLI portátil usa os mesmos argumentos do projeto.\n\n"
                f"Testes do código: {info['source_tests']['passed']} aprovados e {info['source_tests']['skipped']} não executados (motivos em build-info.json). Os executáveis foram testados no Windows, incluindo abertura da interface, inventário até validação sobre XML sintético e comparação com CSV de tráfego. Checksums e informações da compilação estão nos anexos.\n\n"
                "Para usar, extraia o ZIP e abra PanoramaCleanup.exe. Os guias CLI da Palo Alto são fornecidos pelo usuário conforme o procedimento do projeto.")
        release = api(token, API + "/releases", method="POST", data={
            "tag_name": tag, "target_commitish": commit, "name": "Panorama Cleanup " + info["version"] + " — Windows x64",
            "body": body, "draft": True, "prerelease": False, "make_latest": "false"})
    assets = {asset["name"]: asset for asset in release["assets"]}
    for path in files:
        expected = "sha256:" + digest(path)
        asset = assets.get(path.name)
        if asset is None:
            url = release["upload_url"].split("{", 1)[0] + "?name=" + urllib.parse.quote(path.name)
            content_type = "application/zip" if path.suffix == ".zip" else "application/octet-stream"
            asset = api(token, url, method="POST", data=path.read_bytes(), content_type=content_type)
        if asset["state"] != "uploaded" or asset["size"] != path.stat().st_size or (asset.get("digest") and asset["digest"] != expected):
            raise RuntimeError("O anexo remoto não corresponde ao local: " + path.name)
        print("Conferido no GitHub:", path.name, flush=True)
    if release["draft"]:
        release = api(token, API + "/releases/" + str(release["id"]), method="PATCH", data={"draft": False, "make_latest": "false"})
    verified = api(token, API + "/releases/" + str(release["id"]))
    if verified["draft"] or not set(names) <= {asset["name"] for asset in verified["assets"]}:
        raise RuntimeError("A publicação não foi confirmada pelo GitHub.")
    print("Release publicada:", verified["html_url"])


if __name__ == "__main__":
    main()
