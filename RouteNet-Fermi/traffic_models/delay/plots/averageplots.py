#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
npy_averager.py
Calcula la media (u otras operaciones) de varios archivos .npy
y guarda el resultado en un nuevo .npy.
Muestra estadísticas detalladas de cada archivo y del resultado.
Requiere: numpy, scipy
"""
from __future__ import annotations

import os
import tkinter as tk
from tkinter import filedialog, messagebox
from pathlib import Path
from typing import List

import numpy as np

try:
    from scipy import stats as sp_stats
except ImportError:
    import subprocess, sys
    subprocess.check_call([sys.executable, "-m", "pip", "install", "scipy", "--quiet"])
    from scipy import stats as sp_stats

# ─── PALETA (tema claro, coherente con npy_plotter) ──────────────────────────
C = {
    "bg":        "#f5f6f8",
    "bg_panel":  "#ffffff",
    "bg_tb":     "#f0f1f3",
    "entry_bg":  "#ffffff",
    "border":    "#d1d5db",
    "accent":    "#2563eb",
    "accent_h":  "#1d4ed8",
    "green":     "#16a34a",
    "red":       "#dc2626",
    "amber":     "#d97706",
    "fg":        "#111827",
    "fg_dim":    "#6b7280",
    "sel_bg":    "#dbeafe",
    "log_bg":    "#f8fafc",
    "log_head":  "#1e3a5f",
    "log_ok":    "#15803d",
    "log_warn":  "#b45309",
    "log_err":   "#b91c1c",
    "log_val":   "#1d4ed8",
}

F  = ("Segoe UI", 10)
FB = ("Segoe UI", 10, "bold")
FH = ("Segoe UI", 11, "bold")
FS = ("Segoe UI",  9)
FM = ("Consolas",  9)
FL = ("Consolas", 10)   # log


def _mk_btn(parent, text, command, bg, fg="#ffffff", **kw):
    return tk.Button(
        parent, text=text, command=command,
        bg=bg, fg=fg, font=FB,
        relief="flat", bd=0, cursor="hand2",
        activebackground=bg, activeforeground=fg,
        **kw,
    )


def _sep(parent):
    tk.Frame(parent, bg=C["border"], height=1).pack(fill="x", padx=10, pady=6)


def _section_label(parent, text):
    tk.Label(
        parent, text=text,
        bg=C["bg_panel"], fg=C["accent"], font=FB, anchor="w",
    ).pack(fill="x", padx=12, pady=(10, 4))


def next_npy_path(prefix: str, directory: str = ".") -> str:
    os.makedirs(directory, exist_ok=True)
    i = 1
    while True:
        p = os.path.join(directory, f"{prefix}_{i}.npy")
        if not os.path.exists(p):
            return p
        i += 1


# ─── ESTADÍSTICAS ────────────────────────────────────────────────────────────

def compute_stats(data: np.ndarray) -> dict:
    return {
        "N":         len(data),
        "Media":     float(np.mean(data)),
        "Desv. típ": float(np.std(data, ddof=1) if len(data) > 1 else 0.0),
        "Mín":       float(np.min(data)),
        "P10":       float(np.percentile(data, 10)),
        "P25":       float(np.percentile(data, 25)),
        "Mediana":   float(np.median(data)),
        "P75":       float(np.percentile(data, 75)),
        "P90":       float(np.percentile(data, 90)),
        "P95":       float(np.percentile(data, 95)),
        "P99":       float(np.percentile(data, 99)),
        "Máx":       float(np.max(data)),
        "Asimetría": float(sp_stats.skew(data)),
        "Curtosis":  float(sp_stats.kurtosis(data)),   # exceso (Fisher)
    }


# ─── APLICACIÓN ──────────────────────────────────────────────────────────────

class NpyAveragerApp:

    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        root.title("NPY Averager & Stats")
        root.configure(bg=C["bg"])
        root.minsize(1020, 600)
        root.geometry("1200x700")

        self._paths: List[Path] = []
        self._build_ui()

    # ── UI ────────────────────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        # barra título
        tb = tk.Frame(self.root, bg=C["bg_tb"], height=44, relief="flat")
        tb.pack(fill="x", side="top")
        tk.Frame(tb, bg=C["accent"], width=4).pack(side="left", fill="y")
        tk.Label(
            tb, text="🔢  NPY Averager & Stats",
            bg=C["bg_tb"], fg=C["fg"], font=FH, padx=12,
        ).pack(side="left", pady=8)

        # PanedWindow
        pw = tk.PanedWindow(
            self.root, orient="horizontal",
            bg=C["border"], sashwidth=5, sashrelief="flat",
        )
        pw.pack(fill="both", expand=True)

        left = tk.Frame(pw, bg=C["bg_panel"], width=380)
        pw.add(left, minsize=340)
        self._build_left(left)

        right = tk.Frame(pw, bg=C["bg_panel"])
        pw.add(right, minsize=540)
        self._build_right(right)

        # status bar
        sb = tk.Frame(self.root, bg=C["bg_tb"], height=26, relief="flat")
        sb.pack(fill="x", side="bottom")
        tk.Frame(sb, bg=C["accent"], width=4).pack(side="left", fill="y")
        self._status_var = tk.StringVar(value="Añade archivos .npy para comenzar.")
        tk.Label(
            sb, textvariable=self._status_var,
            bg=C["bg_tb"], fg=C["fg_dim"], font=FS,
            anchor="w", padx=10,
        ).pack(side="left")

    def _build_left(self, parent: tk.Frame) -> None:
        canvas = tk.Canvas(parent, bg=C["bg_panel"],
                           highlightthickness=0, bd=0)
        vsb = tk.Scrollbar(parent, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=vsb.set)
        vsb.pack(side="right", fill="y")
        canvas.pack(side="left", fill="both", expand=True)

        inner = tk.Frame(canvas, bg=C["bg_panel"])
        win_id = canvas.create_window((0, 0), window=inner, anchor="nw")

        inner.bind("<Configure>",
                   lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.bind("<Configure>",
                    lambda e: canvas.itemconfig(win_id, width=e.width))
        canvas.bind_all("<MouseWheel>",
                        lambda e: canvas.yview_scroll(
                            int(-1 * (e.delta / 120)), "units"))

        self._build_left_inner(inner)

    def _build_left_inner(self, p: tk.Frame) -> None:

        # ── ARCHIVOS ─────────────────────────────────────────────────────────
        _section_label(p, "ARCHIVOS .NPY")

        btn_row = tk.Frame(p, bg=C["bg_panel"])
        btn_row.pack(fill="x", padx=12, pady=(0, 6))
        _mk_btn(btn_row, "+ Añadir", self._add_files,
                C["accent"], padx=10, pady=5).pack(side="left", padx=(0, 6))
        _mk_btn(btn_row, "✕ Limpiar", self._clear_files,
                C["red"], padx=10, pady=5).pack(side="left")

        self._file_frame = tk.Frame(p, bg=C["bg_panel"])
        self._file_frame.pack(fill="x", padx=6)

        _sep(p)

        # ── OPERACIÓN ────────────────────────────────────────────────────────
        _section_label(p, "OPERACIÓN")

        self._op_var = tk.StringVar(value="mean")
        ops = [
            ("Media elemento a elemento¹",  "mean"),
            ("Concatenar todos",             "concat"),
        ]
        for label, val in ops:
            tk.Radiobutton(
                p, text=label, variable=self._op_var, value=val,
                bg=C["bg_panel"], fg=C["fg"], font=FS,
                selectcolor=C["sel_bg"],
                activebackground=C["bg_panel"],
                activeforeground=C["fg"],
            ).pack(anchor="w", padx=16, pady=1)

        tk.Label(
            p, text="  ¹ Requiere que todos los arrays\n  tengan el mismo número de elementos.",
            bg=C["bg_panel"], fg=C["fg_dim"], font=("Segoe UI", 8),
            justify="left", anchor="w",
        ).pack(fill="x", padx=16, pady=(2, 0))

        _sep(p)

        # ── SALIDA ───────────────────────────────────────────────────────────
        _section_label(p, "ARCHIVO DE SALIDA")

        prefix_row = tk.Frame(p, bg=C["bg_panel"])
        prefix_row.pack(fill="x", padx=12, pady=(0, 4))
        tk.Label(prefix_row, text="Prefijo:",
                 bg=C["bg_panel"], fg=C["fg_dim"], font=FS).pack(side="left")
        self._prefix_var = tk.StringVar(value="npy_avg")
        tk.Entry(
            prefix_row, textvariable=self._prefix_var,
            bg=C["entry_bg"], fg=C["fg"], font=FM,
            relief="solid", bd=1, width=14,
            highlightthickness=1,
            highlightbackground=C["border"],
            highlightcolor=C["accent"],
            insertbackground=C["fg"],
        ).pack(side="left", padx=6)

        dir_row = tk.Frame(p, bg=C["bg_panel"])
        dir_row.pack(fill="x", padx=12, pady=(0, 4))
        tk.Label(dir_row, text="Carpeta:",
                 bg=C["bg_panel"], fg=C["fg_dim"], font=FS).pack(side="left")
        self._dir_var = tk.StringVar(value=str(Path.cwd()))
        dir_entry = tk.Entry(
            dir_row, textvariable=self._dir_var,
            bg=C["entry_bg"], fg=C["fg"], font=FM,
            relief="solid", bd=1, width=20,
            highlightthickness=1,
            highlightbackground=C["border"],
            highlightcolor=C["accent"],
            insertbackground=C["fg"],
        )
        dir_entry.pack(side="left", padx=4, fill="x", expand=True)
        _mk_btn(dir_row, "…", self._browse_dir,
                C["fg_dim"], padx=8, pady=3).pack(side="left")

        _sep(p)

        # ── BOTONES ACCIÓN ───────────────────────────────────────────────────
        action_row = tk.Frame(p, bg=C["bg_panel"])
        action_row.pack(pady=(4, 16), padx=12, fill="x")
        _mk_btn(action_row, "📊  Analizar", self._analyze,
                C["amber"], padx=14, pady=10).pack(side="left", padx=(0, 8))
        _mk_btn(action_row, "💾  Guardar .npy", self._save,
                C["green"], padx=14, pady=10).pack(side="left")

    def _build_right(self, parent: tk.Frame) -> None:
        # cabecera
        hdr = tk.Frame(parent, bg=C["bg_tb"], height=36)
        hdr.pack(fill="x")
        tk.Frame(hdr, bg=C["accent"], width=4).pack(side="left", fill="y")
        tk.Label(hdr, text="LOG DE ESTADÍSTICAS",
                 bg=C["bg_tb"], fg=C["fg"], font=FB, padx=10,
                 ).pack(side="left", pady=6)
        _mk_btn(hdr, "🗑  Limpiar log", self._clear_log,
                C["bg_tb"], fg=C["fg_dim"], padx=10, pady=4,
                ).pack(side="right", padx=8)

        # área de texto
        log_frame = tk.Frame(parent, bg=C["bg_panel"])
        log_frame.pack(fill="both", expand=True, padx=0, pady=0)

        self._log = tk.Text(
            log_frame,
            bg=C["log_bg"], fg=C["fg"], font=FL,
            relief="flat", bd=0,
            state="disabled", wrap="none",
            selectbackground=C["sel_bg"],
        )
        vsb = tk.Scrollbar(log_frame, orient="vertical",
                           command=self._log.yview)
        hsb = tk.Scrollbar(log_frame, orient="horizontal",
                           command=self._log.xview)
        self._log.configure(yscrollcommand=vsb.set,
                            xscrollcommand=hsb.set)
        hsb.pack(side="bottom", fill="x")
        vsb.pack(side="right", fill="y")
        self._log.pack(fill="both", expand=True)

        # tags de color
        self._log.tag_config("head",  foreground=C["log_head"],  font=("Consolas", 10, "bold"))
        self._log.tag_config("ok",    foreground=C["log_ok"])
        self._log.tag_config("warn",  foreground=C["log_warn"])
        self._log.tag_config("err",   foreground=C["log_err"],   font=("Consolas", 10, "bold"))
        self._log.tag_config("val",   foreground=C["log_val"])
        self._log.tag_config("dim",   foreground=C["fg_dim"])
        self._log.tag_config("bold",  font=("Consolas", 10, "bold"))

        self._log_write("NPY Averager & Stats listo.\n\n", "dim")

    # ── LOG ───────────────────────────────────────────────────────────────────

    def _log_write(self, text: str, tag: str = "") -> None:
        self._log.config(state="normal")
        self._log.insert("end", text, tag)
        self._log.see("end")
        self._log.config(state="disabled")

    def _clear_log(self) -> None:
        self._log.config(state="normal")
        self._log.delete("1.0", "end")
        self._log.config(state="disabled")
        self._log_write("Log limpiado.\n\n", "dim")

    def _log_stats(self, name: str, s: dict, tag_name: str = "head") -> None:
        self._log_write(f"{'─' * 60}\n", "dim")
        self._log_write(f"  {name}\n", tag_name)
        self._log_write(f"{'─' * 60}\n", "dim")

        # columnas: etiqueta | valor
        col_w = 14
        for k, v in s.items():
            label = f"  {k:<{col_w}}"
            if isinstance(v, int):
                value = f"{v:>12d}"
            else:
                value = f"{v:>12.6f}"
            self._log_write(label, "dim")
            self._log_write(value + "\n", "val")

        self._log_write("\n")

    # ── ARCHIVOS ──────────────────────────────────────────────────────────────

    def _add_files(self) -> None:
        paths = filedialog.askopenfilenames(
            title="Selecciona archivos .npy",
            filetypes=[("NumPy array", "*.npy"), ("Todos", "*.*")],
        )
        for p in paths:
            path = Path(p)
            if path not in self._paths:
                self._paths.append(path)
                self._add_file_row(path)
        self._status_var.set(f"{len(self._paths)} archivo(s) en lista.")

    def _add_file_row(self, path: Path) -> None:
        row = tk.Frame(self._file_frame, bg=C["bg_panel"])
        row.pack(fill="x", padx=4, pady=2)
        tk.Frame(row, bg=C["border"], height=1).pack(fill="x")
        inner = tk.Frame(row, bg=C["bg_panel"])
        inner.pack(fill="x", pady=3)

        tk.Label(inner, text="📄", bg=C["bg_panel"], font=FS,
                 fg=C["fg_dim"]).pack(side="left", padx=(6, 4))
        tk.Label(inner, text=path.name,
                 bg=C["bg_panel"], fg=C["fg"], font=FM,
                 anchor="w").pack(side="left", fill="x", expand=True)

        def remove(p=path, r=row):
            self._paths.remove(p)
            r.destroy()
            self._status_var.set(f"{len(self._paths)} archivo(s) en lista.")

        _mk_btn(inner, "✕", remove, C["red"], padx=5, pady=1
                ).pack(side="right", padx=6)

    def _clear_files(self) -> None:
        self._paths.clear()
        for w in self._file_frame.winfo_children():
            w.destroy()
        self._status_var.set("Lista limpiada.")

    def _browse_dir(self) -> None:
        d = filedialog.askdirectory(title="Carpeta de salida")
        if d:
            self._dir_var.set(d)

    # ── CARGA ─────────────────────────────────────────────────────────────────

    def _load_arrays(self) -> List[np.ndarray] | None:
        if not self._paths:
            messagebox.showwarning("Sin archivos",
                                   "Añade al menos un archivo .npy.")
            return None
        arrays = []
        for path in self._paths:
            try:
                a = np.load(str(path)).astype(float).ravel()
                arrays.append(a)
            except Exception as exc:
                messagebox.showerror("Error de carga",
                                     f"No se pudo leer '{path.name}':\n{exc}")
                return None
        return arrays

    # ── ANALIZAR ──────────────────────────────────────────────────────────────

    def _analyze(self) -> None:
        arrays = self._load_arrays()
        if arrays is None:
            return

        self._log_write(
            f"{'═' * 60}\n"
            f"  ANÁLISIS  —  {len(arrays)} archivo(s)\n"
            f"{'═' * 60}\n\n",
            "bold",
        )

        shapes_ok = len({len(a) for a in arrays}) == 1

        # estadísticas por archivo
        for path, arr in zip(self._paths, arrays):
            s = compute_stats(arr)
            self._log_stats(path.name, s, tag_name="head")

        # advertencia si tamaños distintos
        if not shapes_ok:
            sizes = [len(a) for a in arrays]
            self._log_write(
                f"⚠  Tamaños distintos: {sizes}\n"
                f"   La operación 'media elemento a elemento' no estará disponible.\n\n",
                "warn",
            )
        else:
            # estadísticas de la media
            mean_arr = np.mean(np.stack(arrays, axis=0), axis=0)
            self._log_stats(
                f"RESULTADO  ·  media de {len(arrays)} arrays (N={len(arrays[0])})",
                compute_stats(mean_arr),
                tag_name="ok",
            )

        self._log_write("Análisis completado.\n\n", "dim")
        self._status_var.set("Análisis completado. Revisa el log.")

    # ── GUARDAR ───────────────────────────────────────────────────────────────

    def _save(self) -> None:
        arrays = self._load_arrays()
        if arrays is None:
            return

        op = self._op_var.get()

        if op == "mean":
            shapes = [len(a) for a in arrays]
            if len(set(shapes)) > 1:
                messagebox.showerror(
                    "Tamaños incompatibles",
                    f"Para la media elemento a elemento todos los arrays deben\n"
                    f"tener el mismo número de elementos.\n\n"
                    f"Tamaños encontrados: {shapes}\n\n"
                    f"Usa 'Concatenar todos' si los tamaños son distintos.",
                )
                return
            result = np.mean(np.stack(arrays, axis=0), axis=0)
            op_label = f"media de {len(arrays)} arrays"
        else:
            result = np.concatenate(arrays)
            op_label = f"concatenación de {len(arrays)} arrays"

        prefix    = self._prefix_var.get().strip() or "npy_avg"
        directory = self._dir_var.get().strip() or "."
        out_path  = next_npy_path(prefix, directory)

        try:
            np.save(out_path, result)
        except Exception as exc:
            messagebox.showerror("Error al guardar", str(exc))
            return

        # log del resultado guardado
        self._log_write(
            f"{'═' * 60}\n"
            f"  GUARDADO  —  {op_label}\n"
            f"{'═' * 60}\n\n",
            "bold",
        )
        self._log_stats(
            f"{Path(out_path).name}  (resultado)",
            compute_stats(result),
            tag_name="ok",
        )
        self._log_write(f"  Ruta: {out_path}\n\n", "dim")

        self._status_var.set(f"Guardado: {Path(out_path).name}")
        messagebox.showinfo("Guardado",
                            f"Resultado guardado en:\n{out_path}")


# ─── ENTRY POINT ─────────────────────────────────────────────────────────────

def main() -> None:
    root = tk.Tk()
    root.configure(bg=C["bg"])
    NpyAveragerApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()