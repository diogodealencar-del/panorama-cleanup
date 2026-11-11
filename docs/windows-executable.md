# Executável Windows

O pacote para Windows de 64 bits inclui `PanoramaCleanup.exe`, com interface gráfica, e `panorama-cleanup.exe`, para linha de comando. O Python e as bibliotecas estão incluídos. A análise é local e funciona sem serviços de IA.

## Interface

Extraia o ZIP e abra `PanoramaCleanup.exe`. Crie um projeto com um backup completo do Panorama e execute as etapas individualmente. O programa conserva o original e grava os resultados dentro do projeto escolhido.

O botão **Comparar XMLs** gera uma planilha com as diferenças entre dois backups, incluindo valores antes e depois, XPath, objetos, campos de regras e mudanças de ordem. O CSV de **Monitor > Logs > Traffic** é opcional. Quando fornecido, o relatório relaciona os eventos às regras de Security e apresenta os fluxos observados. A ausência de registros depende da cobertura e dos filtros da exportação e não comprova ausência de uso.

A geração de comandos exige o guia oficial de CLI da versão PAN-OS do backup, importado pelo botão correspondente. O guia é fornecido pelo usuário. Essa etapa também usa Git Bash para verificar a sintaxe do script gerado. A homologação e a implantação seguem o procedimento do projeto.

## Linha de comando

```powershell
.\panorama-cleanup.exe --version
New-Item -ItemType Directory -Path C:\Panorama\Cliente -Force
.\panorama-cleanup.exe --workdir C:\Panorama\Cliente inventory --input C:\Backups\panorama.xml
```

Use `--workdir` para manter uma pasta por cliente. Quando omitido no executável de linha de comando, o diretório de trabalho é a pasta atual do terminal. Os argumentos das demais etapas são os mesmos documentados no README.

## Compilar

No Windows, com Python 3.12 de 64 bits e Git for Windows instalados, execute na raiz do repositório

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e '.[dev]' 'pyinstaller>=6.16,<7'
.\.venv\Scripts\python.exe scripts\build_windows.py
```

A compilação executa a suíte de testes, cria os dois executáveis e verifica a abertura da interface, o fluxo de inventário até validação com um XML sintético, o comparativo e o inventário pela CLI. Os arquivos ficam em `dist/windows/`, acompanhados de `build-info.json`, checksums e licenças.

`build-info.json` registra o commit, a versão do motor, as bibliotecas, os checksums dos executáveis e os testes do pacote. Um executável sem assinatura digital pode receber um aviso do Windows. Os checksums permitem conferir a integridade do download.

## Publicar no GitHub

O workflow `Executaveis Windows` testa e compila o código em um runner Windows, guarda os resultados e publica uma release `vVERSAO-windows.1` com os dois executáveis, o ZIP e os checksums. Ele é acionado por um push em uma branch `build/windows-v*` ou manualmente pela página de Actions. A publicação usa o token temporário da execução, sem solicitar credenciais aos usuários do aplicativo.

Para publicar uma compilação local, execute `PUBLICAR-GITHUB.cmd` na raiz do clone, com sua conta autenticada no Git. O publicador verifica o commit e os checksums antes do envio e não substitui anexos existentes por outros arquivos. O código é enviado a uma branch `source/windows-vVERSAO`, que não dispara uma segunda compilação durante o upload local.
