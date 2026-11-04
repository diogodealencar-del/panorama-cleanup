# Procedimento — Windows e Linux

Instalação e uso do **panorama-cleanup** no Windows 10/11 e no Linux. O fluxo, as regras e o que revisar em cada etapa são os mesmos do macOS, descritos em [`procedimento-uso-da-ferramenta.md`](procedimento-uso-da-ferramenta.md). Este documento traz só o que muda por sistema: instalação, caminhos, comandos de shell e execução do `run-xmlapi.sh`.

> **Estado da validação.** A ferramenta foi construída e validada no macOS. Para Windows e Linux, o código foi revisado e corrigido para portabilidade: arquivos de comando sempre em LF, Etapa 7 sem bash, console com acentos e `.gitattributes` com LF no checkout. Mas os testes automatizados ainda não rodaram nesses sistemas. As funções de shell do Linux (seção 6) foram testadas em bash; as de PowerShell, não. **No primeiro uso em cada sistema, faça a seção 8** antes de processar um backup real.

---

## 1. Requisitos

| Item | Windows 10/11 | Linux |
|---|---|---|
| Python | 3.9 ou superior (recomendado 3.12) | 3.9 ou superior |
| Git | Git for Windows (inclui o **Git Bash**) | `git` |
| pipx | via `pip` | pacote da distribuição ou via `pip` |
| Shell para os passos do procedimento | PowerShell | bash |
| Shell para o `run-xmlapi.sh` (Etapa 9) | **Git Bash** (ou WSL) | bash |
| `curl` (Etapa 9) | incluído no Git Bash | `curl` |
| Claude Code (opcional, para a skill) | sim | sim |

---

## 2. Instalação no Linux

### Debian / Ubuntu

```bash
sudo apt update
sudo apt install -y git python3 python3-venv pipx curl
pipx ensurepath            # feche e reabra o terminal
```

### RHEL / Rocky / Alma / Fedora

```bash
sudo dnf install -y git python3 python3-pip curl
python3 -m pip install --user pipx    # ou: sudo dnf install pipx (Fedora; EPEL no RHEL)
python3 -m pipx ensurepath            # feche e reabra o terminal
```

### Ferramenta (todas as distribuições)

```bash
git clone https://github.com/diogodealencar-del/panorama-cleanup.git ~/panorama-cleanup
pipx install -e ~/panorama-cleanup
panorama-cleanup --version
```

---

## 3. Instalação no Windows

Execute no **PowerShell** (não é preciso abrir como administrador):

```powershell
winget install --id Git.Git -e
winget install --id Python.Python.3.12 -e
```

Feche e reabra o PowerShell. Depois:

```powershell
py -m pip install --user pipx
py -m pipx ensurepath
```

Feche e reabra o PowerShell de novo, para o PATH valer. Em seguida:

```powershell
git clone https://github.com/diogodealencar-del/panorama-cleanup.git $HOME\panorama-cleanup
pipx install -e $HOME\panorama-cleanup
panorama-cleanup --version
```

Se `winget` não estiver disponível, instale o Git em https://git-scm.com/download/win e o Python em https://www.python.org/downloads/windows/, marcando **Add python.exe to PATH**.

---

## 4. Guia oficial de CLI (Etapa 7)

A Etapa 7 confere a sintaxe dos comandos contra o **PAN-OS CLI Quick Start** da versão do backup. Sem o guia, ela para de propósito. O guia não vai no git: copie-o para o clone.

| Sistema | Destino |
|---|---|
| Linux | `~/panorama-cleanup/docs/referencias/pan-os-X.Y/ngfw-cli.md` |
| Windows | `$HOME\panorama-cleanup\docs\referencias\pan-os-X.Y\ngfw-cli.md` |

`X.Y` é a versão principal do backup (por exemplo, `11.1`). Origem e conversão: [`referencias/README.md`](referencias/README.md).

---

## 5. Pasta de trabalho e backup

Uma pasta por cliente/Panorama, **fora** do clone da ferramenta. A ferramenta cria `input/`, `output/`, `cli/`, `rollback/` e `logs/` dentro dela.

### Linux (bash)

```bash
W=~/panorama-trabalho/cliente-panorama
mkdir -p "$W/input"
cp ~/Downloads/running-config.xml "$W/input/cliente-panorama-20261104.xml"
B="$W/input/cliente-panorama-20261104.xml"
sha256sum "$B"                         # registrar
H=$(sha256sum "$B" | cut -c1-8)
```

### Windows (PowerShell)

Use um caminho **curto**, como `C:\pc\<cliente>`. Os nomes de run são longos, e caminhos profundos podem passar do limite de 260 caracteres do Windows.

```powershell
$W = "C:\pc\cliente-panorama"
New-Item -ItemType Directory -Force "$W\input" | Out-Null
Copy-Item "$HOME\Downloads\running-config.xml" "$W\input\cliente-panorama-20261104.xml"
$B = "$W\input\cliente-panorama-20261104.xml"
Get-FileHash $B -Algorithm SHA256       # registrar
$H = (Get-FileHash $B -Algorithm SHA256).Hash.Substring(0, 8).ToLower()
```

Nunca edite o arquivo em `input/`. A ferramenta se recusa a gravar ali.

---

## 6. Run mais recente de cada etapa

Cada etapa usa o diretório de um run anterior. Estas funções devolvem o run mais recente, com status ok, de um tipo (`inventory`, `analyze`, `plan`, `apply`, `apply-cleanup`, `plan-commands`, `validate`) deste backup. Confira sempre qual foi escolhido antes de seguir.

### Linux (bash)

```bash
ultimo() {
  for d in $(ls -1 "$W/output/reports" | sort -r | grep -E -- "^[0-9]{8}T[0-9]{6}Z-$1-$H\$"); do
    m="$W/output/reports/$d/manifest.json"
    grep -q '"status": "ok"' "$m" 2>/dev/null || continue
    echo "$W/output/reports/$d"; return
  done
}
```

### Windows (PowerShell)

```powershell
function Ultimo([string]$Tipo) {
  Get-ChildItem "$W\output\reports" -Directory |
    Where-Object { $_.Name -match "^\d{8}T\d{6}Z-$([regex]::Escape($Tipo))-$H$" } |
    Sort-Object Name -Descending |
    Where-Object { (Get-Content "$($_.FullName)\manifest.json" -Raw | ConvertFrom-Json).status -eq "ok" } |
    Select-Object -First 1 -ExpandProperty FullName
}
```

Exemplo: `ultimo plan` (bash) ou `Ultimo plan` (PowerShell).

---

## 7. Etapas

O que revisar antes de aprovar cada etapa está em [`procedimento-uso-da-ferramenta.md`](procedimento-uso-da-ferramenta.md), seção 5. **Uma etapa por vez, com aprovação entre elas.**

### Linux (bash)

```bash
# Etapa 2 — Inventário
panorama-cleanup --workdir "$W" inventory --input "$B"

# Etapa 3 — Análise
panorama-cleanup --workdir "$W" analyze --input "$B" --inventory "$(ultimo inventory)"

# Etapa 4 — Plano
panorama-cleanup --workdir "$W" plan --input "$B" --analysis "$(ultimo analyze)"

# Etapa 5 — Conversão para Shared
panorama-cleanup --workdir "$W" apply --input "$B" --plan "$(ultimo plan)"

# Etapa 6 — Remoção de não utilizados (tags preservadas)
panorama-cleanup --workdir "$W" apply --stage cleanup --keep-type tag \
  --input "$B" --plan "$(ultimo plan)" --from-run "$(ultimo apply)"

# Etapa 7 — Comandos
panorama-cleanup --workdir "$W" plan --stage commands --input "$B" --plan "$(ultimo plan)" \
  --convert-run "$(ultimo apply)" --cleanup-run "$(ultimo apply-cleanup)"

# Etapa 8 — Validação final
FINAL=$(ls "$W/output/xml/$(basename "$(ultimo apply-cleanup)")"/*.final.xml)
panorama-cleanup --workdir "$W" validate --input "$B" --modified "$FINAL" \
  --plan "$(ultimo plan)" --convert-run "$(ultimo apply)" --cleanup-run "$(ultimo apply-cleanup)"
```

### Windows (PowerShell)

No PowerShell, a continuação de linha é o acento grave (`` ` ``) no fim da linha.

```powershell
# Etapa 2 — Inventário
panorama-cleanup --workdir $W inventory --input $B

# Etapa 3 — Análise
panorama-cleanup --workdir $W analyze --input $B --inventory (Ultimo inventory)

# Etapa 4 — Plano
panorama-cleanup --workdir $W plan --input $B --analysis (Ultimo analyze)

# Etapa 5 — Conversão para Shared
panorama-cleanup --workdir $W apply --input $B --plan (Ultimo plan)

# Etapa 6 — Remoção de não utilizados (tags preservadas)
panorama-cleanup --workdir $W apply --stage cleanup --keep-type tag `
  --input $B --plan (Ultimo plan) --from-run (Ultimo apply)

# Etapa 7 — Comandos
panorama-cleanup --workdir $W plan --stage commands --input $B --plan (Ultimo plan) `
  --convert-run (Ultimo apply) --cleanup-run (Ultimo apply-cleanup)

# Etapa 8 — Validação final
$FINAL = Get-ChildItem "$W\output\xml\$(Split-Path (Ultimo apply-cleanup) -Leaf)\*.final.xml" |
  Select-Object -First 1 -ExpandProperty FullName
panorama-cleanup --workdir $W validate --input $B --modified $FINAL `
  --plan (Ultimo plan) --convert-run (Ultimo apply) --cleanup-run (Ultimo apply-cleanup)
```

Para conferir o código de saída de uma etapa: `$LASTEXITCODE` (PowerShell) ou `echo $?` (bash). Na Etapa 8, 0 = APROVADO e 1 = NÃO APROVADO.

**No Windows, sem Git Bash no PATH**, a Etapa 7 registra a verificação "run-xmlapi.sh: sintaxe bash válida" como **PENDENTE**, sem bloquear. Confira a sintaxe no Git Bash antes de usar o script: `bash -n run-xmlapi.sh`.

**Planilhas:** abra os `.xlsx`. Os `.csv` estão em UTF-8 sem BOM, e o Excel pode exibir acentos errados ao abri-los com duplo clique. Para usar o CSV, prefira **Dados > De Texto/CSV**, com origem UTF-8.

---

## 8. Validação no primeiro uso em cada sistema

Faça uma vez por sistema operacional (e a cada atualização relevante), **antes** de processar um backup real:

### Linux

```bash
cd ~/panorama-cleanup
python3 -m venv .venv
.venv/bin/pip install -e '.[dev]'
.venv/bin/pytest -q                      # todos devem passar
```

### Windows (PowerShell)

```powershell
cd $HOME\panorama-cleanup
py -m venv .venv
.venv\Scripts\pip install -e ".[dev]"
.venv\Scripts\pytest -q                  # todos devem passar
```

Depois, rode as Etapas 2 a 8 da seção 7 com o arquivo de teste `tests/fixtures/xml/panorama_deps.xml`, que é fictício. O resultado esperado:
- 3 Device Groups e 55 objetos no inventário;
- Etapa 8 com **APROVADO**.

Se algum teste falhar, guarde a saída e não use o sistema em backup real até a falha ser resolvida.

No Windows, alguns testes podem falhar por causa do próprio ambiente de teste, não da ferramenta:
- os que simulam pastas somente leitura (`tests/test_build_skill.py`), porque o Windows não tem o mesmo modelo de permissões do Unix;
- os que executam o `run-xmlapi.sh` com `bash` (`tests/test_generate_cli.py`), quando o Git Bash não está no PATH do PowerShell.

Registre essas falhas e informe o responsável. Qualquer outra falha bloqueia o uso.

---

## 9. Execução da XML API (Etapa 9)

O `run-xmlapi.sh` é um script bash. Ele aplica um `.tsv` por vez, para no primeiro erro e **nunca faz commit**. Sem `CONFIRM_EXECUTE=yes`, só lista as operações e não envia nada.

Para não deixar a chave de API no histórico do shell, digite-a com `read -s`.

### Linux

```bash
cd "$W/cli/<run plan-commands>"
./run-xmlapi.sh 10-fase1-criar-shared.tsv            # só lista (nada é enviado)

read -rs -p "Chave de API: " PANORAMA_API_KEY; echo; export PANORAMA_API_KEY
CONFIRM_EXECUTE=yes PANORAMA_HOST=<host> PANORAMA_CA_CERT=<cert.pem> \
  ./run-xmlapi.sh 10-fase1-criar-shared.tsv
```

### Windows (Git Bash)

Abra o **Git Bash** pelo menu Iniciar. No Git Bash, `C:\pc\cliente` vira `/c/pc/cliente`.

```bash
cd "/c/pc/cliente-panorama/cli/<run plan-commands>"
./run-xmlapi.sh 10-fase1-criar-shared.tsv            # só lista (nada é enviado)

read -rs -p "Chave de API: " PANORAMA_API_KEY; echo; export PANORAMA_API_KEY
CONFIRM_EXECUTE=yes PANORAMA_HOST=<host> PANORAMA_CA_CERT=/c/pc/certs/panorama-ca.pem \
  ./run-xmlapi.sh 10-fase1-criar-shared.tsv
```

Se `./run-xmlapi.sh` responder "Permission denied", rode `bash run-xmlapi.sh …`.

Alternativa no Windows: **WSL** (Ubuntu). Siga os passos de Linux, e os arquivos do Windows ficam em `/mnt/c/...`.

Repita para `20-…`, `30-…` e `40-…`, nessa ordem, conforme [`modelo-etapa-09.md`](modelo-etapa-09.md). Ao terminar: `unset PANORAMA_API_KEY`.

---

## 10. Skill no Claude Code

A instalação do plugin é a mesma nos três sistemas:

```bash
claude plugin marketplace add ~/panorama-cleanup                 # Windows: $HOME\panorama-cleanup
claude plugin install panorama-cleanup@panorama-tools
```

Reinicie o Claude Code depois de instalar ou atualizar. No Windows, o Claude Code executa os comandos da skill pelo **Git Bash**, que precisa estar instalado (seção 3). Os passos da skill são escritos em bash e funcionam ali.

Peça, por exemplo: "Use a skill panorama-cleanup para analisar o backup `C:\pc\cliente-panorama\input\cliente-panorama-20261104.xml` e execute só a Etapa 2".

---

## 11. Atualização

| Sistema | Comandos |
|---|---|
| Linux | `git -C ~/panorama-cleanup pull` |
| Windows | `git -C $HOME\panorama-cleanup pull` |
| Plugin (ambos) | `claude plugin marketplace update panorama-tools` e `claude plugin update panorama-cleanup@panorama-tools`, depois reiniciar o Claude Code |

Reinstalar com `pipx install -e <clone> --force` só é necessário se as dependências mudarem.

---

## 12. Problemas comuns

| Sintoma | Causa provável | Solução |
|---|---|---|
| `panorama-cleanup` não é reconhecido | PATH do pipx não carregado | Rodar `pipx ensurepath` (Windows: `py -m pipx ensurepath`) e reabrir o terminal |
| `py` não é reconhecido (Windows) | Python instalado sem o launcher | Reinstalar o Python marcando *py launcher* e *Add to PATH* |
| `\r: command not found` no `run-xmlapi.sh` | Script convertido para CRLF por um editor ou cópia | Regenerar a Etapa 7 ou rodar `sed -i 's/\r$//' run-xmlapi.sh` no Git Bash |
| Etapa 7 para por falta de guia | `ngfw-cli.md` da versão ausente | Copiar o guia para `docs/referencias/pan-os-X.Y/` (seção 4) |
| Caminho muito longo (Windows) | Pasta de trabalho profunda | Usar `C:\pc\<cliente>` ou habilitar caminhos longos no Windows |
| Acentos errados ao abrir CSV no Excel | CSV UTF-8 sem BOM | Abrir o `.xlsx` ou importar o CSV como UTF-8 |
| `--workdir não existe` | Pasta não criada | Criar a pasta antes (seção 5) |
