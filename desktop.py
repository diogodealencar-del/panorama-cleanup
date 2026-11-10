"""Panorama Cleanup Desktop — local GUI, no model, server or API key."""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import uuid

from desktop_support import STEPS, PREREQUISITES, accept_result, atomic_json, load_session, new_session, worker
from panorama_cleanup import __version__

LABELS = {
    "inventory": "1  Inventário", "analyze": "2  Dependências e uso",
    "plan": "3  Plano de alterações", "convert": "4  Centralizar em Shared",
    "cleanup": "5  Remover objetos sem uso", "commands": "6  Gerar comandos",
    "validate": "7  Validar XML final",
    "compare": "Comparar XMLs",
}
DESCRIPTIONS = {
    "inventory": "Lê o backup e gera inventário CSV e Excel, sem alterar a configuração.",
    "analyze": "Identifica uso, dependências, duplicidades e conflitos de objetos.",
    "plan": "Planeja alterações e verifica a equivalência das políticas antes de transformar o XML.",
    "convert": "Aplica o plano em uma nova cópia: Shared, nomes e referências de objetos.",
    "cleanup": "Remove na cópia os objetos cuja ausência de uso foi comprovada. Mantém tags por padrão.",
    "commands": "Gera arquivos CLI, XML API e rollback. Requer guia oficial da versão e Git Bash.",
    "validate": "Compara original e final, incluindo regras, ordem, UUIDs e referências efetivas.",
}


def gui(smoke_result: Path | None = None) -> None:
    import tkinter as tk
    from tkinter import filedialog, messagebox, simpledialog, ttk
    from tkinter.scrolledtext import ScrolledText

    class App(tk.Tk):
        def __init__(self):
            super().__init__()
            self.title("Panorama Cleanup | Desktop sem IA")
            self.geometry("1120x790")
            self.minsize(920, 680)
            self.configure(bg="#11121a")
            self.root_dir = None
            self.session = None
            self.process = None
            self.current_step = None
            self.last_run = None
            self.log_offset = 0
            self.compare_temp = None
            self.keep_tags = tk.BooleanVar(value=True)
            self.step = tk.StringVar(value="inventory")
            self.status = tk.StringVar(value="Crie um projeto para começar.")
            self.project_text = tk.StringVar(value="Nenhum projeto aberto")
            self.description = tk.StringVar(value=DESCRIPTIONS["inventory"])
            style = ttk.Style(self)
            style.theme_use("clam")
            style.configure("TFrame", background="#11121a")
            style.configure("TLabel", background="#11121a", foreground="#f1f0fa", font=("Segoe UI", 10))
            style.configure("Title.TLabel", font=("Segoe UI", 23, "bold"))
            style.configure("Muted.TLabel", foreground="#b4b1c5")
            style.configure("TButton", font=("Segoe UI", 10), padding=(12, 8),
                            background="#292637", foreground="#ffffff")
            style.map("TButton", background=[("active", "#5d3998"), ("disabled", "#242430")],
                      foreground=[("disabled", "#757180")])
            style.configure("Accent.TButton", background="#853df5", font=("Segoe UI", 11, "bold"))
            style.configure("TRadiobutton", background="#11121a", foreground="#e5def3", padding=8)
            style.map("TRadiobutton", background=[("active", "#292637")])
            style.configure("TCheckbutton", background="#11121a", foreground="#e5def3")
            outer = ttk.Frame(self, padding=24)
            outer.pack(fill="both", expand=True)
            ttk.Label(outer, text="Panorama Cleanup", style="Title.TLabel").pack(anchor="w")
            ttk.Label(outer, text=f"Processamento local • Motor {__version__} • Sem IA ou chave de API",
                      style="Muted.TLabel").pack(anchor="w", pady=(4, 18))
            toolbar = ttk.Frame(outer)
            toolbar.pack(fill="x")
            self.project_buttons = []
            for text, command in (("Novo projeto / selecionar XML", self.create_project),
                                  ("Abrir projeto", self.open_project), ("Abrir pasta", self.open_folder),
                                  ("Importar guia CLI", self.import_guide), ("Comparar XMLs", self.open_comparison)):
                button = ttk.Button(toolbar, text=text, command=command)
                button.pack(side="left", padx=(0, 8))
                self.project_buttons.append(button)
            ttk.Label(outer, textvariable=self.project_text, style="Muted.TLabel", wraplength=1000).pack(
                fill="x", pady=(12, 16))
            center = ttk.Frame(outer)
            center.pack(fill="x")
            self.step_buttons = {}
            sidebar = ttk.Frame(center)
            sidebar.pack(side="left", anchor="n", padx=(0, 25))
            for step in STEPS:
                button = ttk.Radiobutton(sidebar, text=LABELS[step], variable=self.step, value=step,
                                         command=self.refresh_description)
                button.pack(anchor="w", fill="x")
                self.step_buttons[step] = button
            detail = ttk.Frame(center)
            detail.pack(side="left", fill="both", expand=True)
            ttk.Label(detail, text="Uma etapa por vez", font=("Segoe UI", 15, "bold")).pack(anchor="w", pady=(8, 12))
            ttk.Label(detail, textvariable=self.description, wraplength=650, justify="left").pack(anchor="w")
            self.tags_check = ttk.Checkbutton(detail, text="Preservar tags durante a limpeza", variable=self.keep_tags)
            self.tags_check.pack(anchor="w", pady=(18, 12))
            self.run_button = ttk.Button(detail, text="Executar etapa selecionada", style="Accent.TButton", command=self.run_step)
            self.run_button.pack(anchor="w")
            actions = ttk.Frame(detail)
            actions.pack(anchor="w", pady=(12, 0))
            ttk.Button(actions, text="Abrir relatórios da etapa", command=self.open_report).pack(side="left", padx=(0, 8))
            ttk.Button(actions, text="Abrir pasta do XML final", command=self.open_xml).pack(side="left")
            ttk.Label(outer, textvariable=self.status, wraplength=1000).pack(fill="x", pady=(18, 8))
            self.progress = ttk.Progressbar(outer, mode="indeterminate")
            self.progress.pack(fill="x", pady=(0, 10))
            self.log = ScrolledText(outer, height=10, font=("Consolas", 10), bg="#080b12", fg="#d8d3e6",
                                   insertbackground="white", relief="flat", state="disabled")
            self.log.pack(fill="both", expand=True)
            ttk.Label(outer, text="O backup é preservado. Revise os relatórios entre as etapas. "
                      "O XML gerado exige homologação; a ferramenta não faz commit ou push.",
                      style="Muted.TLabel", wraplength=1000).pack(anchor="w", pady=(12, 0))
            self.protocol("WM_DELETE_WINDOW", self.close_app)
            self.update_controls()

        def append(self, text):
            self.log.configure(state="normal")
            self.log.insert("end", text)
            self.log.see("end")
            self.log.configure(state="disabled")

        def refresh_description(self):
            selected = self.step.get()
            suffix = " — concluído" if self.session and selected in self.session["runs"] else ""
            self.description.set(DESCRIPTIONS[selected] + suffix)

        def update_controls(self):
            busy = self.process is not None
            for button in self.project_buttons: button.configure(state="disabled" if busy else "normal")
            for step, button in self.step_buttons.items():
                ready = self.session is not None and all(p in self.session["runs"] for p in PREREQUISITES[step])
                button.configure(state="normal" if ready and not busy else "disabled")
            self.run_button.configure(state="normal" if self.session and not busy else "disabled")
            self.tags_check.configure(state="disabled" if busy else "normal")
            self.refresh_description()

        def save(self):
            atomic_json(self.root_dir / "desktop-session.json", self.session)

        def set_project(self, root, state):
            self.root_dir, self.session = root, state
            self.keep_tags.set(state.get("keep_tags", True))
            self.last_run = None
            next_step = next((s for s in STEPS if s not in state["runs"] and
                              all(p in state["runs"] for p in PREREQUISITES[s])), "validate")
            self.step.set(next_step)
            self.project_text.set(str(root) + "\nBackup: " + Path(state["input"]).name +
                                  " | SHA-256: " + state["input_sha256"][:16] + "…")
            self.status.set("Projeto aberto. Selecione uma etapa para executar.")
            self.update_controls()

        def create_project(self):
            backup = filedialog.askopenfilename(title="Selecione o backup completo do Panorama",
                                                filetypes=[("Backup XML", "*.xml"), ("Todos os arquivos", "*.*")])
            if not backup: return
            parent = filedialog.askdirectory(title="Escolha onde criar a pasta do projeto")
            if not parent: return
            name = simpledialog.askstring("Nome do projeto", "Nome da nova pasta do cliente / Panorama:",
                                          initialvalue="Panorama_" + uuid.uuid4().hex[:6], parent=self)
            if not name: return
            if re.search(r'[<>:"/\\|?*\x00-\x1f]', name) or name.strip() != name or name.endswith(".") or name in (".", ".."):
                messagebox.showerror("Nome inválido", "Use um nome de pasta simples, sem barras ou caracteres reservados.")
                return
            try:
                root, state = new_session(Path(backup), Path(parent) / name)
                self.set_project(root, state)
            except Exception as exc: messagebox.showerror("Não foi possível criar o projeto", str(exc))

        def open_project(self):
            folder = filedialog.askdirectory(title="Selecione a pasta do projeto já criado")
            if not folder: return
            try:
                root = Path(folder).resolve()
                self.set_project(root, load_session(root))
            except Exception as exc: messagebox.showerror("Projeto inválido", str(exc))

        def open_folder(self):
            if self.root_dir: os.startfile(self.root_dir)

        def open_report(self):
            if not self.session: return
            run = self.session["runs"].get(self.step.get()) or self.last_run
            if run and Path(run).is_dir(): os.startfile(run)
            else: messagebox.showinfo("Relatórios", "Execute primeiro a etapa selecionada.")

        def open_xml(self):
            if self.root_dir and (self.root_dir / "output" / "xml").is_dir():
                os.startfile(self.root_dir / "output" / "xml")

        def import_guide(self):
            if not self.session or "inventory" not in self.session["runs"]:
                messagebox.showinfo("Guia CLI", "Conclua o inventário para identificar a versão PAN-OS.")
                return
            try:
                inv = json.loads((Path(self.session["runs"]["inventory"]) / "manifest.json").read_text(encoding="utf-8"))
                version = ".".join(inv["panos_version"].split(".")[:2])
                if not re.fullmatch(r"\d+\.\d+", version): raise ValueError("Versão PAN-OS inválida.")
                source = filedialog.askopenfilename(title="Guia oficial CLI convertido em Markdown / texto",
                                                    filetypes=[("Texto ou Markdown", "*.md *.txt"), ("Todos", "*.*")])
                if not source: return
                data = Path(source).read_text(encoding="utf-8")
                if not re.search(r"^PAN-OS " + re.escape(version) + r" (CLI Ops|Configure CLI) Command Hierarchy$", data, re.M):
                    raise ValueError("O arquivo não contém a hierarquia CLI esperada para PAN-OS " + version + ".")
                target = self.root_dir / "docs" / "referencias" / ("pan-os-" + version) / "ngfw-cli.md"
                if target.exists() and not messagebox.askyesno("Substituir guia", "Substituir o guia já importado neste projeto?"): return
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(data, encoding="utf-8")
                self.status.set("Guia CLI PAN-OS " + version + " importado.")
            except Exception as exc: messagebox.showerror("Guia CLI", str(exc))

        def open_comparison(self):
            dialog = tk.Toplevel(self)
            dialog.title("Comparar XML inicial e final")
            dialog.transient(self)
            dialog.resizable(False, False)
            frame = ttk.Frame(dialog, padding=20)
            frame.pack(fill="both", expand=True)
            initial = tk.StringVar(value=self.session["input"] if self.session else "")
            final = tk.StringVar()
            traffic_csv = tk.StringVar()
            if self.session and (run := self.session["runs"].get("cleanup")):
                try:
                    manifest = json.loads((Path(run) / "manifest.json").read_text(encoding="utf-8"))
                    path = Path(manifest["output_xml"]["path"])
                    if path.is_file():
                        final.set(str(path))
                except (OSError, ValueError, KeyError, TypeError):
                    pass

            def choose(label, variable, pattern):
                path = filedialog.askopenfilename(parent=dialog, title=label, filetypes=[(label, pattern), ("Todos os arquivos", "*.*")])
                if path:
                    variable.set(path)

            for row, (label, variable, pattern) in enumerate((
                ("XML inicial", initial, "*.xml"), ("XML final", final, "*.xml"),
                ("CSV de tráfego (opcional)", traffic_csv, "*.csv"),
            )):
                ttk.Label(frame, text=label).grid(row=row, column=0, padx=(0, 10), pady=6, sticky="w")
                ttk.Entry(frame, textvariable=variable, width=48).grid(row=row, column=1, pady=6)
                ttk.Button(frame, text="Selecionar", command=lambda l=label, v=variable, p=pattern: choose(l, v, p)).grid(row=row, column=2, padx=(8, 0))

            ttk.Label(frame, text="O relatório mostra as diferenças dos XMLs e cruza regras de Security com os eventos do CSV, quando informado.",
                      style="Muted.TLabel", wraplength=550).grid(row=3, column=0, columnspan=3, pady=(12, 8), sticky="w")

            def submit():
                paths = [initial.get().strip(), final.get().strip(), traffic_csv.get().strip()]
                if not all(p and Path(p).is_file() for p in paths[:2]) or (paths[2] and not Path(paths[2]).is_file()):
                    messagebox.showerror("Comparar XMLs", "Selecione dois XMLs existentes e, se desejar, um CSV de tráfego.", parent=dialog)
                    return
                output = filedialog.asksaveasfilename(parent=dialog, title="Salvar relatório", defaultextension=".xlsx",
                                                      initialfile="Comparativo_Panorama.xlsx", filetypes=[("Planilha Excel", "*.xlsx")])
                if not output:
                    return
                try:
                    self.compare_temp = tempfile.TemporaryDirectory(prefix="PanoramaComparacao-")
                    request = Path(self.compare_temp.name) / "compare.json"
                    atomic_json(request, {"step": "compare", "initial": paths[0], "final": paths[1],
                                          "traffic_csv": paths[2], "output": output})
                    self.start_worker("compare", request)
                except (OSError, ValueError) as exc:
                    if self.compare_temp:
                        self.compare_temp.cleanup()
                        self.compare_temp = None
                    messagebox.showerror("Falha ao iniciar comparação", str(exc), parent=dialog)
                    return
                dialog.destroy()

            ttk.Button(frame, text="Gerar relatório Excel", style="Accent.TButton", command=submit).grid(
                row=4, column=0, columnspan=3, sticky="e")
            dialog.grab_set()

        def start_worker(self, step, request):
            self.request_path = request
            command = [sys.executable]
            if not getattr(sys, "frozen", False):
                command.append(str(Path(__file__).resolve()))
            command.extend(("--engine", str(request)))
            self.process = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                            stderr=subprocess.DEVNULL, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            self.current_step, self.log_offset = step, 0
            self.status.set("Em execução: " + LABELS[step] + ". Aguarde o relatório.")
            self.append("\n" + "─" * 50 + "\n" + LABELS[step] + "\n")
            self.progress.start(15)
            self.update_controls()
            self.after(250, self.poll)

        def run_step(self):
            if not self.session or self.process: return
            step = self.step.get()
            if any(p not in self.session["runs"] for p in PREREQUISITES[step]):
                messagebox.showerror("Etapa indisponível", "Conclua as etapas anteriores.")
                return
            if step in ("convert", "cleanup"):
                text = ("Revise o plano antes de continuar. Centralizar os objetos em uma nova cópia XML?"
                        if step == "convert" else "Revise os candidatos do plano. Remover os objetos sem uso comprovado na cópia XML?\n\n"
                        + ("As tags serão preservadas." if self.keep_tags.get() else "Tags sem uso também poderão ser removidas."))
                if not messagebox.askyesno(LABELS[step], text): return
            if step in self.session["runs"] and not messagebox.askyesno("Reexecutar etapa", "Reexecutar esta etapa invalida as etapas seguintes no projeto. Os arquivos anteriores serão preservados. Continuar?"):
                return
            try:
                self.session = load_session(self.root_dir)
                self.session["keep_tags"] = self.keep_tags.get()
                accept_result(self.session, step, None, False)
                self.save()
                requests = self.root_dir / "logs" / "desktop"
                requests.mkdir(parents=True, exist_ok=True)
                request = requests / (uuid.uuid4().hex + ".json")
                atomic_json(request, {"root": str(self.root_dir), "step": step, "keep_tags": self.keep_tags.get()})
                self.start_worker(step, request)
            except Exception as exc:
                self.process = None
                self.progress.stop()
                self.update_controls()
                messagebox.showerror("Falha ao iniciar", str(exc))

        def poll(self):
            log = self.request_path.with_suffix(".log")
            if log.exists():
                with log.open("rb") as stream:
                    stream.seek(self.log_offset)
                    raw = stream.read()
                # Consume only complete UTF-8 lines; a worker may be mid-write.
                end = raw.rfind(b"\n") + 1
                if end:
                    self.append(raw[:end].decode("utf-8", errors="replace"))
                    self.log_offset += end
            if self.process.poll() is None:
                self.after(350, self.poll)
                return
            result_file = self.request_path.with_suffix(".result.json")
            try:
                result = json.loads(result_file.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                result = {"code": 1, "run": None, "error": "O processo foi encerrado sem resultado. Verifique o log."}
            success = self.process.returncode == 0 and result["code"] == 0
            step = self.current_step
            self.process = None
            self.progress.stop()
            if step == "compare":
                self.finish_comparison(success, result)
                self.update_controls()
                return
            self.last_run = result.get("run")
            accept_result(self.session, step, self.last_run, success)
            try: self.save()
            except Exception as exc:
                success = False
                messagebox.showerror("Falha ao salvar projeto", str(exc))
            label = "Concluído" if success else "Falhou — consulte o log e os relatórios"
            self.status.set(LABELS[step] + ": " + label)
            if success:
                try:
                    manifest = json.loads((Path(self.last_run) / "manifest.json").read_text(encoding="utf-8"))
                    summary = manifest.get("summary", {})
                    if summary: self.append("\nResumo:\n" + json.dumps(summary, ensure_ascii=False, indent=2) + "\n")
                    if step == "validate": self.status.set("Validação: " + manifest.get("result", "consulte o relatório") + ". Homologação em laboratório ainda necessária.")
                    pending = manifest.get("checks_pending", 0)
                    if pending: self.status.set(LABELS[step] + ": gerado com " + str(pending) + " verificações pendentes; consulte o relatório.")
                except (OSError, ValueError): pass
            else:
                messagebox.showerror("Etapa não concluída", result.get("error") or "A etapa encontrou inconsistências. Consulte o log e os relatórios para os detalhes.")
            self.update_controls()

        def finish_comparison(self, success, result):
            if self.compare_temp:
                self.compare_temp.cleanup()
                self.compare_temp = None
            if not success:
                self.status.set("A comparação falhou. Consulte a mensagem e o log.")
                messagebox.showerror("Comparação não concluída", result.get("error") or "Falha ao gerar o relatório Excel.")
                return
            self.status.set("Relatório de comparação salvo em " + result["report"])
            summary = result["summary"]
            message = (f'{summary["xml_differences"]} diferenças de XML e '
                       f'{summary["rules_changed"]} regras com mudanças.')
            if summary["changed_rules_with_traffic"] is not None:
                message += f' {summary["changed_rules_with_traffic"]} regras alteradas com tráfego observado.'
            self.append(message + "\n" + result["report"] + "\n")
            if messagebox.askyesno("Relatório pronto", message + "\n\nAbrir o relatório agora?"):
                try:
                    os.startfile(result["report"])
                except OSError as exc:
                    messagebox.showerror("Não foi possível abrir o relatório", str(exc))

        def close_app(self):
            if self.process:
                messagebox.showinfo("Processamento em andamento", "Aguarde esta etapa terminar antes de fechar o programa.")
                return
            self.destroy()

    app = App()
    if smoke_result is not None:
        def finish_smoke():
            atomic_json(smoke_result, {"ui_created": True, "title": app.title(),
                                      "steps": len(app.step_buttons), "tk": str(tk.TkVersion)})
            app.destroy()
        app.after(500, finish_smoke)
    app.mainloop()


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "--engine":
        raise SystemExit(worker(Path(sys.argv[2])))
    if len(sys.argv) == 3 and sys.argv[1] == "--ui-smoke":
        gui(Path(sys.argv[2]))
    else:
        gui()
