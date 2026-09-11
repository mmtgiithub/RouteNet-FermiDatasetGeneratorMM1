#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
npy_plotter.py
Visualizador de distribuciones (PDF/CDF) a partir de archivos .npy.
- PDF: traza KDE por archivo, color y leyenda personalizables.
- CDF: curva empírica acumulada.
Requiere: numpy, scipy, matplotlib
"""
from __future__ import annotations

import tkinter as tk
from tkinter import filedialog, colorchooser, messagebox
from pathlib import Path
from typing import List, Optional
from dataclasses import dataclass, field

import numpy as np

try:
    from scipy.stats import gaussian_kde
except ImportError:
    import subprocess, sys
    subprocess.check_call([sys.executable, "-m", "pip", "install", "scipy", "--quiet"])
    from scipy.stats import gaussian_kde

try:
    import matplotlib
    matplotlib.use("TkAgg")
    import matplotlib.pyplot as plt
    from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg, NavigationToolbar2Tk
    from matplotlib.figure import Figure
except ImportError:
    import subprocess, sys
    subprocess.check_call([sys.executable, "-m", "pip", "install", "matplotlib", "--quiet"])
    import matplotlib
    matplotlib.use("TkAgg")
    import matplotlib.pyplot as plt
    from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg, NavigationToolbar2Tk
    from matplotlib.figure import Figure

# ─── PALETA LIGHT ────────────────────────────────────────────────────────────
C = {
    "bg":        "#f5f6f8",       # fondo ventana
    "bg_panel":  "#ffffff",       # panel izquierdo
    "bg_tb":     "#f0f1f3",       # barra título / toolbar
    "entry_bg":  "#ffffff",
    "border":    "#d1d5db",
    "accent":    "#2563eb",       # azul
    "accent_h":  "#1d4ed8",
    "green":     "#16a34a",
    "red":       "#dc2626",
    "fg":        "#111827",
    "fg_dim":    "#6b7280",
    "sel_bg":    "#dbeafe",
    # matplotlib
    "plot_bg":   "#ffffff",
    "plot_fg":   "#111827",
    "grid":      "#e5e7eb",       # gris muy suave
}

DEFAULT_COLORS = [
    "#2563eb", "#16a34a", "#dc2626", "#d97706",
    "#7c3aed", "#0891b2", "#db2777", "#65a30d",
]

F  = ("Segoe UI", 10)
FB = ("Segoe UI", 10, "bold")
FH = ("Segoe UI", 11, "bold")
FS = ("Segoe UI",  9)
FM = ("Consolas",  9)


def _mk_btn(parent, text, command, bg, fg=None, **kw):
    fg = fg or "#ffffff"
    return tk.Button(
        parent, text=text, command=command,
        bg=bg, fg=fg, font=FB,
        relief="flat", bd=0, cursor="hand2",
        activebackground=bg, activeforeground=fg,
        **kw,
    )


def _label(parent, text, **kw):
    return tk.Label(parent, text=text, bg=C["bg_panel"],
                    fg=C["fg_dim"], font=FS, **kw)


def _entry(parent, textvariable, width=10):
    return tk.Entry(
        parent, textvariable=textvariable,
        bg=C["entry_bg"], fg=C["fg"], font=FM,
        relief="solid", bd=1, width=width,
        highlightthickness=1,
        highlightbackground=C["border"],
        highlightcolor=C["accent"],
        insertbackground=C["fg"],
    )


def _sep(parent):
    tk.Frame(parent, bg=C["border"], height=1).pack(fill="x", padx=10, pady=6)


def _section_label(parent, text):
    tk.Label(
        parent, text=text,
        bg=C["bg_panel"], fg=C["accent"], font=FB, anchor="w",
    ).pack(fill="x", padx=12, pady=(10, 4))


# ─── MODELO DE DATOS ─────────────────────────────────────────────────────────

@dataclass
class Trace:
    path: Path
    data: np.ndarray
    color: str
    label: str
    color_var: tk.StringVar  = field(default=None)   # type: ignore
    label_var: tk.StringVar  = field(default=None)   # type: ignore
    alpha_var: tk.StringVar  = field(default=None)   # type: ignore


# ─── APLICACIÓN ──────────────────────────────────────────────────────────────

class NpyPlotterApp:

    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        root.title("NPY Distribution Plotter")
        root.configure(bg=C["bg"])
        root.minsize(1100, 680)
        root.geometry("1340x760")

        self._traces: List[Trace] = []
        self._color_idx = 0

        self._build_ui()

    # ── UI ────────────────────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        # barra de título
        tb = tk.Frame(self.root, bg=C["bg_tb"], height=44,
                      relief="flat", bd=0)
        tb.pack(fill="x", side="top")
        tk.Frame(tb, bg=C["accent"], width=4).pack(side="left", fill="y")
        tk.Label(
            tb, text="📈  NPY Distribution Plotter  —  PDF / CDF",
            bg=C["bg_tb"], fg=C["fg"], font=FH, padx=12,
        ).pack(side="left", pady=8)

        # contenedor principal
        pw = tk.PanedWindow(
            self.root, orient="horizontal",
            bg=C["border"], sashwidth=5, sashrelief="flat",
        )
        pw.pack(fill="both", expand=True)

        # panel izquierdo
        left_outer = tk.Frame(pw, bg=C["bg_panel"], width=400)
        pw.add(left_outer, minsize=360)
        self._build_left(left_outer)

        # panel derecho (matplotlib)
        right = tk.Frame(pw, bg=C["bg"])
        pw.add(right, minsize=600)
        self._build_plot(right)

        # status bar
        sb = tk.Frame(self.root, bg=C["bg_tb"], height=26, relief="flat")
        sb.pack(fill="x", side="bottom")
        tk.Frame(sb, bg=C["accent"], width=4).pack(side="left", fill="y")
        self._status_var = tk.StringVar(value="Añade archivos .npy y pulsa Graficar.")
        tk.Label(
            sb, textvariable=self._status_var,
            bg=C["bg_tb"], fg=C["fg_dim"], font=FS,
            anchor="w", padx=10,
        ).pack(side="left")

    def _build_left(self, parent: tk.Frame) -> None:
        # scroll del panel izquierdo completo
        canvas = tk.Canvas(parent, bg=C["bg_panel"],
                           highlightthickness=0, bd=0)
        vsb = tk.Scrollbar(parent, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=vsb.set)
        vsb.pack(side="right", fill="y")
        canvas.pack(side="left", fill="both", expand=True)

        inner = tk.Frame(canvas, bg=C["bg_panel"])
        win_id = canvas.create_window((0, 0), window=inner, anchor="nw")

        def _on_inner_cfg(_e):
            canvas.configure(scrollregion=canvas.bbox("all"))

        def _on_canvas_cfg(e):
            canvas.itemconfig(win_id, width=e.width)

        def _on_wheel(e):
            canvas.yview_scroll(int(-1 * (e.delta / 120)), "units")

        inner.bind("<Configure>", _on_inner_cfg)
        canvas.bind("<Configure>", _on_canvas_cfg)
        canvas.bind_all("<MouseWheel>", _on_wheel)

        self._build_left_inner(inner)

    def _build_left_inner(self, p: tk.Frame) -> None:
        # ── TIPO DE GRÁFICO ──────────────────────────────────────────────────
        _section_label(p, "TIPO DE GRÁFICO")
        self._plot_type = tk.StringVar(value="PDF")
        type_row = tk.Frame(p, bg=C["bg_panel"])
        type_row.pack(fill="x", padx=12, pady=(0, 6))
        for val in ("PDF", "CDF"):
            tk.Radiobutton(
                type_row, text=val,
                variable=self._plot_type, value=val,
                bg=C["bg_panel"], fg=C["fg"], font=FB,
                selectcolor=C["sel_bg"],
                activebackground=C["bg_panel"],
                activeforeground=C["fg"],
            ).pack(side="left", padx=(0, 16))

        _sep(p)

        # ── OPCIONES PDF (bandwidth) ─────────────────────────────────────────
        _section_label(p, "ANCHO DE BANDA KDE")
        self._pdf_opts = tk.Frame(p, bg=C["bg_panel"])
        self._pdf_opts.pack(fill="x", padx=12, pady=(0, 4))
        _label(self._pdf_opts, "Método:").pack(side="left")
        self._bw_var = tk.StringVar(value="scott")
        bw_menu = tk.OptionMenu(self._pdf_opts, self._bw_var,
                                "scott", "silverman", "manual")
        bw_menu.config(
            bg=C["entry_bg"], fg=C["fg"], font=FS,
            relief="solid", bd=1, highlightthickness=0,
            activebackground=C["sel_bg"], activeforeground=C["fg"],
        )
        bw_menu["menu"].config(bg=C["entry_bg"], fg=C["fg"], font=FS)
        bw_menu.pack(side="left", padx=6)
        self._bw_manual_var = tk.StringVar(value="0.5")
        self._bw_manual_entry = _entry(self._pdf_opts, self._bw_manual_var, 6)
        self._bw_var.trace_add("write", self._on_bw_change)
        self._on_bw_change()

        _sep(p)

        # ── ESTADÍSTICOS AUTOMÁTICOS ─────────────────────────────────────────
        _section_label(p, "ESTADÍSTICOS")
        stats_f = tk.Frame(p, bg=C["bg_panel"])
        stats_f.pack(fill="x", padx=12, pady=(0, 6))

        self._show_mode_var = tk.BooleanVar(value=False)
        self._show_p50_var  = tk.BooleanVar(value=False)
        self._show_p90_var  = tk.BooleanVar(value=False)
        self._show_p95_var  = tk.BooleanVar(value=False)

        for i, (txt, var) in enumerate([
            ("Moda  (PDF)",          self._show_mode_var),
            ("Mediana / P50  (CDF)", self._show_p50_var),
            ("P90  (CDF)",           self._show_p90_var),
            ("P95  (CDF)",           self._show_p95_var),
        ]):
            tk.Checkbutton(stats_f, text=txt, variable=var,
                           bg=C["bg_panel"], fg=C["fg"], font=FS,
                           selectcolor=C["sel_bg"],
                           activebackground=C["bg_panel"],
                           ).grid(row=i, column=0, sticky="w", pady=1)

        _sep(p)

        # ── ARCHIVOS .NPY ────────────────────────────────────────────────────
        _section_label(p, "ARCHIVOS .NPY")
        add_row = tk.Frame(p, bg=C["bg_panel"])
        add_row.pack(fill="x", padx=12, pady=(0, 6))
        _mk_btn(add_row, "+ Añadir .npy", self._add_files,
                C["green"], padx=10, pady=5).pack(side="left", padx=(0, 6))
        _mk_btn(add_row, "✕ Limpiar todo", self._clear_all,
                C["red"], padx=10, pady=5).pack(side="left")

        # contenedor de trazas
        self._trace_container = tk.Frame(p, bg=C["bg_panel"])
        self._trace_container.pack(fill="x", padx=6, pady=(0, 4))

        _sep(p)

        # ── CONFIGURACIÓN DE EJES ────────────────────────────────────────────
        _section_label(p, "EJES")

        axes_grid = tk.Frame(p, bg=C["bg_panel"])
        axes_grid.pack(fill="x", padx=12, pady=(0, 6))

        def row(parent, label_text, var, r):
            tk.Label(parent, text=label_text,
                     bg=C["bg_panel"], fg=C["fg_dim"], font=FS,
                     anchor="w", width=12,
                     ).grid(row=r, column=0, sticky="w", pady=3)
            e = _entry(parent, var, width=18)
            e.grid(row=r, column=1, sticky="w", padx=(4, 0), pady=3)

        self._ax_xlabel_var = tk.StringVar(value="")
        self._ax_ylabel_var = tk.StringVar(value="")
        self._ax_title_var  = tk.StringVar(value="")
        self._ax_xmin_var   = tk.StringVar(value="")
        self._ax_xmax_var   = tk.StringVar(value="")
        self._ax_ymin_var   = tk.StringVar(value="")
        self._ax_ymax_var   = tk.StringVar(value="")
        self._ax_fontsize_var     = tk.StringVar(value="11")
        self._ax_font_var         = tk.StringVar(value="DejaVu Sans")
        self._ax_legend_font_var  = tk.StringVar(value="DejaVu Sans")
        self._ax_legend_fs_var    = tk.StringVar(value="9")

        self._ax_xtick_pos_var = tk.StringVar(value="")
        self._ax_xtick_lbl_var = tk.StringVar(value="")
        self._ax_xtick_rot_var = tk.StringVar(value="0")
        self._ax_ytick_pos_var = tk.StringVar(value="")
        self._ax_ytick_lbl_var = tk.StringVar(value="")
        self._ax_ytick_rot_var = tk.StringVar(value="0")

        row(axes_grid, "Título:",      self._ax_title_var,  0)
        row(axes_grid, "Eje X:",       self._ax_xlabel_var, 1)
        row(axes_grid, "Eje Y:",       self._ax_ylabel_var, 2)
        row(axes_grid, "X mín:",       self._ax_xmin_var,   3)
        row(axes_grid, "X máx:",       self._ax_xmax_var,   4)
        row(axes_grid, "Y mín:",       self._ax_ymin_var,   5)
        row(axes_grid, "Y máx:",       self._ax_ymax_var,   6)
        row(axes_grid, "Tamaño (pt):", self._ax_fontsize_var, 7)

        # ── selectores de fuente ──────────────────────────────────────────────
        _FONTS = [
            "DejaVu Sans", "DejaVu Serif", "Arial", "Helvetica",
            "Times New Roman", "Georgia", "Palatino",
            "Calibri", "Verdana", "Trebuchet MS",
            "Courier New", "Consolas", "Comic Sans MS",
        ]

        def _font_menu(parent, var, label_text, r):
            tk.Label(parent, text=label_text,
                     bg=C["bg_panel"], fg=C["fg_dim"], font=FS,
                     anchor="w", width=12,
                     ).grid(row=r, column=0, sticky="w", pady=3)
            m = tk.OptionMenu(parent, var, *_FONTS)
            m.config(bg=C["entry_bg"], fg=C["fg"], font=FS,
                     relief="solid", bd=1, highlightthickness=0,
                     activebackground=C["sel_bg"], activeforeground=C["fg"],
                     width=16)
            m["menu"].config(bg=C["entry_bg"], fg=C["fg"], font=FS)
            m.grid(row=r, column=1, sticky="w", padx=(4, 0), pady=3)

        _font_menu(axes_grid, self._ax_font_var,        "Fuente ejes:",    8)
        _font_menu(axes_grid, self._ax_legend_font_var, "Fuente leyenda:", 9)
        row(axes_grid, "Tamaño ley.:", self._ax_legend_fs_var, 10)

        # ── tick labels ───────────────────────────────────────────────────────
        tk.Frame(axes_grid, bg=C["border"], height=1).grid(
            row=11, column=0, columnspan=2, sticky="ew", pady=6)
        tk.Label(axes_grid, text="── TICK LABELS ──",
                 bg=C["bg_panel"], fg=C["accent"], font=FS, anchor="w",
                 ).grid(row=12, column=0, columnspan=2, sticky="w", pady=(0, 4))

        row(axes_grid, "X ticks:",     self._ax_xtick_pos_var, 13)
        row(axes_grid, "X etiquetas:", self._ax_xtick_lbl_var, 14)
        row(axes_grid, "X rotación°:", self._ax_xtick_rot_var, 15)
        row(axes_grid, "Y ticks:",     self._ax_ytick_pos_var, 16)
        row(axes_grid, "Y etiquetas:", self._ax_ytick_lbl_var, 17)
        row(axes_grid, "Y rotación°:", self._ax_ytick_rot_var, 18)

        tk.Label(axes_grid,
                 text="(valores separados por comas · etiquetas opcionales)",
                 bg=C["bg_panel"], fg=C["fg_dim"],
                 font=("Segoe UI", 8), anchor="w", wraplength=240, justify="left",
                 ).grid(row=19, column=0, columnspan=2, sticky="w", pady=(0, 4))

        _sep(p)

        # ── ASPECTO DE LA FIGURA ─────────────────────────────────────────────
        _section_label(p, "FIGURA")

        fig_row1 = tk.Frame(p, bg=C["bg_panel"])
        fig_row1.pack(fill="x", padx=12, pady=(0, 4))
        _label(fig_row1, "Ancho (in):").pack(side="left")
        self._fig_w_var = tk.StringVar(value="8")
        _entry(fig_row1, self._fig_w_var, 5).pack(side="left", padx=4)
        _label(fig_row1, "Alto (in):").pack(side="left", padx=(8, 0))
        self._fig_h_var = tk.StringVar(value="5")
        _entry(fig_row1, self._fig_h_var, 5).pack(side="left", padx=4)

        fig_row2 = tk.Frame(p, bg=C["bg_panel"])
        fig_row2.pack(fill="x", padx=12, pady=(0, 8))
        _label(fig_row2, "DPI:").pack(side="left")
        self._fig_dpi_var = tk.StringVar(value="120")
        _entry(fig_row2, self._fig_dpi_var, 5).pack(side="left", padx=4)
        self._legend_var = tk.BooleanVar(value=True)
        tk.Checkbutton(
            fig_row2, text="Leyenda",
            variable=self._legend_var,
            bg=C["bg_panel"], fg=C["fg"], font=FS,
            selectcolor=C["sel_bg"],
            activebackground=C["bg_panel"],
        ).pack(side="left", padx=(16, 0))

        _sep(p)

        # ── BOTONES ACCIÓN ───────────────────────────────────────────────────
        btn_row = tk.Frame(p, bg=C["bg_panel"])
        btn_row.pack(pady=(4, 16), padx=12, fill="x")
        _mk_btn(btn_row, "📈  Graficar", self._plot,
                C["accent"], padx=18, pady=10).pack(side="left", padx=(0, 8))
        _mk_btn(btn_row, "💾  Guardar PNG", self._save_png,
                C["fg_dim"], padx=12, pady=10).pack(side="left")

    def _build_plot(self, parent: tk.Frame) -> None:
        self._fig = Figure(facecolor=C["plot_bg"])
        self._ax  = self._fig.add_subplot(111)
        self._style_axes()

        toolbar_frame = tk.Frame(parent, bg=C["bg_tb"])
        toolbar_frame.pack(fill="x", side="bottom")

        self._canvas = FigureCanvasTkAgg(self._fig, master=parent)
        self._canvas.draw()

        toolbar = NavigationToolbar2Tk(self._canvas, toolbar_frame)
        toolbar.config(bg=C["bg_tb"])
        toolbar.update()

        self._canvas.get_tk_widget().pack(fill="both", expand=True)

    # ── ESTILO MATPLOTLIB ─────────────────────────────────────────────────────

    def _style_axes(self) -> None:
        ax = self._ax
        ax.set_facecolor(C["plot_bg"])
        ax.tick_params(colors=C["plot_fg"], labelsize=9,
                       direction="in", top=True, right=True)
        ax.xaxis.label.set_color(C["plot_fg"])
        ax.yaxis.label.set_color(C["plot_fg"])
        ax.title.set_color(C["plot_fg"])
        for spine in ax.spines.values():
            spine.set_color(C["border"])
            spine.set_linewidth(0.8)
        # cuadrícula suave
        ax.grid(True, color=C["grid"], linewidth=0.7,
                linestyle="--", alpha=0.9)
        ax.set_axisbelow(True)          # grid detrás de las curvas

    # ── TRAZAS ────────────────────────────────────────────────────────────────

    def _next_color(self) -> str:
        c = DEFAULT_COLORS[self._color_idx % len(DEFAULT_COLORS)]
        self._color_idx += 1
        return c

    def _add_files(self) -> None:
        paths = filedialog.askopenfilenames(
            title="Selecciona archivos .npy",
            filetypes=[("NumPy array", "*.npy"), ("Todos", "*.*")],
        )
        for p in paths:
            path = Path(p)
            try:
                data = np.load(str(path)).astype(float).ravel()
            except Exception as exc:
                messagebox.showerror("Error",
                                     f"No se pudo cargar '{path.name}':\n{exc}")
                continue
            color     = self._next_color()
            color_var = tk.StringVar(value=color)
            label_var = tk.StringVar(value=path.stem)
            alpha_var = tk.StringVar(value="1.0")
            t = Trace(path=path, data=data, color=color, label=path.stem,
                      color_var=color_var, label_var=label_var,
                      alpha_var=alpha_var)
            self._traces.append(t)
            self._add_trace_row(t)
        self._status_var.set(f"{len(self._traces)} archivo(s) cargado(s).")

    def _add_trace_row(self, t: Trace) -> None:
        row = tk.Frame(self._trace_container, bg=C["bg_panel"],
                       relief="flat", bd=0)
        row.pack(fill="x", padx=4, pady=2)

        # separador superior suave
        tk.Frame(row, bg=C["border"], height=1).pack(fill="x")

        inner = tk.Frame(row, bg=C["bg_panel"])
        inner.pack(fill="x", pady=4)

        # color swatch
        swatch = tk.Label(inner, bg=t.color_var.get(),
                          width=3, cursor="hand2", relief="flat")
        swatch.pack(side="left", padx=(6, 6))

        def pick_color(tr=t, sw=swatch):
            result = colorchooser.askcolor(color=tr.color_var.get(),
                                           title="Elige color")
            if result and result[1]:
                tr.color_var.set(result[1])
                tr.color = result[1]
                sw.config(bg=result[1])

        swatch.bind("<Button-1>", lambda e, tr=t, sw=swatch: pick_color(tr, sw))

        # nombre de archivo
        tk.Label(
            inner, text=t.path.name,
            bg=C["bg_panel"], fg=C["fg_dim"], font=FS,
            anchor="w", width=16,
        ).pack(side="left", padx=(0, 4))

        # leyenda
        tk.Label(inner, text="Leyenda:", bg=C["bg_panel"],
                 fg=C["fg_dim"], font=FS).pack(side="left")
        tk.Entry(
            inner, textvariable=t.label_var,
            bg=C["entry_bg"], fg=C["fg"], font=FM,
            relief="solid", bd=1, width=12,
            highlightthickness=1,
            highlightbackground=C["border"],
            highlightcolor=C["accent"],
            insertbackground=C["fg"],
        ).pack(side="left", padx=4)

        # opacidad
        tk.Label(inner, text="α:", bg=C["bg_panel"],
                 fg=C["fg_dim"], font=FS).pack(side="left")
        alpha_e = tk.Entry(
            inner, textvariable=t.alpha_var,
            bg=C["entry_bg"], fg=C["fg"], font=FM,
            relief="solid", bd=1, width=4,
            highlightthickness=1,
            highlightbackground=C["border"],
            highlightcolor=C["accent"],
            insertbackground=C["fg"],
        )
        alpha_e.pack(side="left", padx=(2, 6))

        # slider de opacidad (0–1)
        alpha_scale = tk.Scale(
            inner, from_=0.0, to=1.0, resolution=0.05,
            orient="horizontal", length=80,
            variable=t.alpha_var,
            bg=C["bg_panel"], fg=C["fg_dim"],
            troughcolor=C["border"], highlightthickness=0,
            relief="flat", bd=0, font=FS,
            showvalue=False,
        )
        alpha_scale.pack(side="left", padx=(0, 4))

        # quitar
        def remove(tr=t, r=row):
            self._traces.remove(tr)
            r.destroy()
            self._status_var.set(f"{len(self._traces)} archivo(s) cargado(s).")

        _mk_btn(inner, "✕", remove, C["red"], padx=6, pady=2).pack(
            side="right", padx=6
        )

    def _clear_all(self) -> None:
        self._traces.clear()
        for w in self._trace_container.winfo_children():
            w.destroy()
        self._color_idx = 0
        self._status_var.set("Lista limpiada.")

    def _on_bw_change(self, *_) -> None:
        if self._bw_var.get() == "manual":
            self._bw_manual_entry.pack(side="left")
        else:
            self._bw_manual_entry.pack_forget()

    # ── GRAFICADO ─────────────────────────────────────────────────────────────

    def _parse_float(self, var: tk.StringVar) -> Optional[float]:
        """Devuelve float o None si el campo está vacío / inválido."""
        s = var.get().strip()
        if not s:
            return None
        try:
            return float(s)
        except ValueError:
            return None

    def _parse_csv_floats(self, var: tk.StringVar) -> Optional[list]:
        s = var.get().strip()
        if not s:
            return None
        try:
            return [float(x.strip()) for x in s.split(",") if x.strip()]
        except ValueError:
            return None

    def _parse_csv_strings(self, var: tk.StringVar) -> Optional[list]:
        s = var.get().strip()
        if not s:
            return None
        return [x.strip() for x in s.split(",")]

    def _apply_axes_config(self) -> None:
        ax   = self._ax
        mode = self._plot_type.get()

        # etiquetas (usa el valor del campo si no está vacío, si no uno por defecto)
        title  = self._ax_title_var.get().strip()
        xlabel = self._ax_xlabel_var.get().strip()
        ylabel = self._ax_ylabel_var.get().strip()

        default_ylabel = ("Densidad (KDE)" if mode == "PDF"
                          else "Probabilidad acumulada")

        if title:
            ax.set_title(title, color=C["plot_fg"])
        ax.set_xlabel(xlabel or "Valor", color=C["plot_fg"])
        ax.set_ylabel(ylabel or default_ylabel, color=C["plot_fg"])

        # límites
        xmin = self._parse_float(self._ax_xmin_var)
        xmax = self._parse_float(self._ax_xmax_var)
        ymin = self._parse_float(self._ax_ymin_var)
        ymax = self._parse_float(self._ax_ymax_var)

        if xmin is not None or xmax is not None:
            cur_xmin, cur_xmax = ax.get_xlim()
            ax.set_xlim(
                xmin if xmin is not None else cur_xmin,
                xmax if xmax is not None else cur_xmax,
            )
        if ymin is not None or ymax is not None:
            cur_ymin, cur_ymax = ax.get_ylim()
            ax.set_ylim(
                ymin if ymin is not None else cur_ymin,
                ymax if ymax is not None else cur_ymax,
            )

        # ── TICK LABELS X ────────────────────────────────────────────────────
        xtick_pos = self._parse_csv_floats(self._ax_xtick_pos_var)
        xtick_lbl = self._parse_csv_strings(self._ax_xtick_lbl_var)
        xtick_rot = self._parse_float(self._ax_xtick_rot_var) or 0.0

        if xtick_pos is not None:
            ax.set_xticks(xtick_pos)
            if xtick_lbl is not None:
                lbls = xtick_lbl + [str(v) for v in xtick_pos[len(xtick_lbl):]]
                ax.set_xticklabels(lbls[:len(xtick_pos)], rotation=xtick_rot,
                                   ha="right" if xtick_rot != 0 else "center")
            elif xtick_rot != 0:
                ax.tick_params(axis="x", labelrotation=xtick_rot)
        elif xtick_rot != 0:
            ax.tick_params(axis="x", labelrotation=xtick_rot)

        # ── TICK LABELS Y ────────────────────────────────────────────────────
        ytick_pos = self._parse_csv_floats(self._ax_ytick_pos_var)
        ytick_lbl = self._parse_csv_strings(self._ax_ytick_lbl_var)
        ytick_rot = self._parse_float(self._ax_ytick_rot_var) or 0.0

        if ytick_pos is not None:
            ax.set_yticks(ytick_pos)
            if ytick_lbl is not None:
                lbls = ytick_lbl + [str(v) for v in ytick_pos[len(ytick_lbl):]]
                ax.set_yticklabels(lbls[:len(ytick_pos)], rotation=ytick_rot,
                                   va="center")
            elif ytick_rot != 0:
                ax.tick_params(axis="y", labelrotation=ytick_rot)
        elif ytick_rot != 0:
            ax.tick_params(axis="y", labelrotation=ytick_rot)

        # ── FUENTE Y TAMAÑO ───────────────────────────────────────────────────
        fs       = self._parse_float(self._ax_fontsize_var)
        font_fam = self._ax_font_var.get()
        axis_items = ([ax.title, ax.xaxis.label, ax.yaxis.label]
                      + ax.get_xticklabels() + ax.get_yticklabels())
        for item in axis_items:
            if font_fam:
                item.set_fontfamily(font_fam)
            if fs is not None:
                item.set_fontsize(fs)

    # ── ESTADÍSTICOS ─────────────────────────────────────────────────────────

    def _draw_mode_pdf(self, x_kde: np.ndarray, y_kde: np.ndarray,
                       color: str) -> None:
        """Dibuja la moda del KDE: línea horizontal, marca X y anotación."""
        ax     = self._ax
        idx    = int(np.argmax(y_kde))
        x_mode = float(x_kde[idx])
        y_mode = float(y_kde[idx])

        # línea horizontal discontinua a la altura de la densidad máxima
        ax.axhline(y=y_mode, color=color, linestyle="--",
                   linewidth=1.2, alpha=0.75, zorder=3)

        # marca X en el pico (zorder bajo para que quede bajo el texto)
        ax.plot(x_mode, y_mode, marker="x", color=C["plot_fg"],
                markersize=10, markeredgewidth=2.2, zorder=4)

        # texto encima o debajo de la X según si hay espacio arriba
        ylim    = ax.get_ylim()
        near_top = y_mode > ylim[0] + 0.72 * (ylim[1] - ylim[0])
        yt_off  = (8, -8) if near_top else (8, 6)
        va_txt  = "top"   if near_top else "bottom"
        ax.annotate(
            f"({x_mode:.4g}, {y_mode:.3g})",
            xy=(x_mode, y_mode),
            xytext=yt_off,
            textcoords="offset points",
            fontsize=11, color="black",
            fontfamily="Times New Roman",
            va=va_txt, ha="left",
            zorder=10,
        )

    def _draw_cdf_stats(self, data: np.ndarray, color: str) -> None:
        """Dibuja líneas verticales para P50/P90/P95 con etiquetas rotadas."""
        ax   = self._ax
        # pequeño desplazamiento horizontal para la etiqueta
        xlim = ax.get_xlim()
        dx   = (xlim[1] - xlim[0]) * 0.008

        specs: list = []
        if self._show_p50_var.get():
            specs.append((50,  float(np.percentile(data, 50)),  0.04))
        if self._show_p90_var.get():
            specs.append((90,  float(np.percentile(data, 90)),  0.18))
        if self._show_p95_var.get():
            specs.append((95,  float(np.percentile(data, 95)),  0.32))

        for perc, val, y_frac in specs:
            ax.axvline(x=val, color=color, linestyle="--",
                       linewidth=1.2, alpha=0.75, zorder=3)
            xlim2   = ax.get_xlim()
            near_right = val > xlim2[0] + 0.82 * (xlim2[1] - xlim2[0])
            x_off   = val - dx if near_right else val + dx
            ha_txt  = "right"  if near_right else "left"
            ax.text(
                x_off, y_frac,
                f"$P_{{{perc}}}$ = {val:.4g}",
                transform=ax.get_xaxis_transform(),
                fontsize=11, color="black",
                fontfamily="Times New Roman",
                rotation=90, va="bottom", ha=ha_txt,
                zorder=10,
            )

    def _plot(self) -> None:
        if not self._traces:
            self._status_var.set("Añade al menos un archivo .npy.")
            return

        # tamaño de figura
        try:
            fw  = float(self._fig_w_var.get())
            fh  = float(self._fig_h_var.get())
            dpi = float(self._fig_dpi_var.get())
        except ValueError:
            messagebox.showerror("Error",
                                 "Ancho, alto y DPI deben ser números.")
            return

        self._fig.set_size_inches(fw, fh)
        self._fig.set_dpi(dpi)

        self._ax.cla()
        self._style_axes()
        mode = self._plot_type.get()

        bw_method: object = self._bw_var.get()
        if bw_method == "manual":
            try:
                bw_method = float(self._bw_manual_var.get())
            except ValueError:
                messagebox.showerror("Error",
                                     "El ancho de banda manual debe ser un número.")
                return

        for t in self._traces:
            data  = t.data
            color = t.color_var.get()
            label = t.label_var.get() or t.path.stem
            try:
                alpha = max(0.0, min(1.0, float(t.alpha_var.get())))
            except ValueError:
                alpha = 1.0

            if mode == "PDF":
                try:
                    kde = gaussian_kde(data, bw_method=bw_method)
                    x   = np.linspace(data.min(), data.max(), 512)
                    y   = kde(x)
                    self._ax.plot(x, y, color=color, label=label,
                                  linewidth=2, alpha=alpha)
                    if self._show_mode_var.get():
                        self._draw_mode_pdf(x, y, color)
                except Exception as exc:
                    self._status_var.set(
                        f"Error KDE '{t.path.name}': {exc}")
                    return
            else:
                sorted_data = np.sort(data)
                cdf = np.arange(1, len(sorted_data) + 1) / len(sorted_data)
                self._ax.plot(sorted_data, cdf, color=color, label=label,
                              linewidth=2, alpha=alpha)
                if (self._show_p50_var.get() or self._show_p90_var.get()
                        or self._show_p95_var.get()):
                    self._draw_cdf_stats(data, color)

        # aplicar configuración de ejes (etiquetas + límites)
        self._apply_axes_config()

        # leyenda
        if self._legend_var.get():
            leg_fs   = self._parse_float(self._ax_legend_fs_var) or 9
            leg_font = self._ax_legend_font_var.get() or "DejaVu Sans"
            leg = self._ax.legend(fontsize=leg_fs, framealpha=0.9,
                                  edgecolor=C["border"],
                                  prop={"family": leg_font, "size": leg_fs})

        self._fig.tight_layout()
        self._canvas.draw()
        self._status_var.set(
            f"{mode} graficado — {len(self._traces)} traza(s)."
        )

    def _save_png(self) -> None:
        path = filedialog.asksaveasfilename(
            title="Guardar figura",
            defaultextension=".png",
            filetypes=[("PNG", "*.png"), ("PDF", "*.pdf"),
                       ("SVG", "*.svg"), ("Todos", "*.*")],
        )
        if not path:
            return
        try:
            self._fig.savefig(path, dpi=self._fig.get_dpi(),
                              bbox_inches="tight")
            self._status_var.set(f"Guardado: {Path(path).name}")
        except Exception as exc:
            messagebox.showerror("Error", str(exc))


# ─── ENTRY POINT ─────────────────────────────────────────────────────────────

def main() -> None:
    root = tk.Tk()
    root.configure(bg=C["bg"])
    NpyPlotterApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()