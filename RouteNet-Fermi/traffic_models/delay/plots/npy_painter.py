#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
npy_painter.py
Pinta los valores de archivos .npy directamente (serie / señal).
- Color, leyenda, opacidad, estilo de línea y marcador por traza.
- Con 2 archivos: opción de dibujar también la diferencia A − B.
Requiere: numpy, matplotlib
"""
from __future__ import annotations

import tkinter as tk
from tkinter import filedialog, colorchooser, messagebox
from pathlib import Path
from typing import List, Optional
from dataclasses import dataclass, field

import numpy as np

try:
    import matplotlib
    matplotlib.use("TkAgg")
    from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg, NavigationToolbar2Tk
    from matplotlib.figure import Figure
except ImportError:
    import subprocess, sys
    subprocess.check_call([sys.executable, "-m", "pip", "install", "matplotlib", "--quiet"])
    import matplotlib
    matplotlib.use("TkAgg")
    from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg, NavigationToolbar2Tk
    from matplotlib.figure import Figure

# ─── PALETA ──────────────────────────────────────────────────────────────────
C = {
    "bg":       "#f5f6f8",
    "bg_panel": "#ffffff",
    "bg_tb":    "#f0f1f3",
    "entry_bg": "#ffffff",
    "border":   "#d1d5db",
    "accent":   "#2563eb",
    "green":    "#16a34a",
    "red":      "#dc2626",
    "amber":    "#d97706",
    "fg":       "#111827",
    "fg_dim":   "#6b7280",
    "sel_bg":   "#dbeafe",
    "plot_bg":  "#ffffff",
    "plot_fg":  "#111827",
    "grid":     "#e5e7eb",
}

DEFAULT_COLORS = [
    "#2563eb", "#dc2626", "#16a34a", "#d97706",
    "#7c3aed", "#0891b2", "#db2777", "#65a30d",
]

DIFF_COLOR   = "#9f1239"
LINE_STYLES  = ["solid", "dashed", "dashdot", "dotted"]
MARKER_OPTS  = ["ninguno", ".", "o", "x", "+", "^", "s", "D"]
MARKER_MAP   = {"ninguno": None}   # None (NoneType) = sin marcador

F  = ("Segoe UI", 10)
FB = ("Segoe UI", 10, "bold")
FH = ("Segoe UI", 11, "bold")
FS = ("Segoe UI",  9)
FM = ("Consolas",  9)

_FONTS = [
    "DejaVu Sans", "DejaVu Serif", "Arial", "Helvetica",
    "Times New Roman", "Georgia", "Palatino",
    "Calibri", "Verdana", "Trebuchet MS",
    "Courier New", "Consolas", "Comic Sans MS",
]


def _mk_btn(parent, text, command, bg, fg="#ffffff", **kw):
    return tk.Button(
        parent, text=text, command=command,
        bg=bg, fg=fg, font=FB, relief="flat", bd=0, cursor="hand2",
        activebackground=bg, activeforeground=fg, **kw,
    )


def _sep(parent):
    tk.Frame(parent, bg=C["border"], height=1).pack(fill="x", padx=10, pady=6)


def _section(parent, text):
    tk.Label(parent, text=text, bg=C["bg_panel"],
             fg=C["accent"], font=FB, anchor="w",
             ).pack(fill="x", padx=12, pady=(10, 4))


def _lbl(parent, text, **kw):
    return tk.Label(parent, text=text, bg=C["bg_panel"],
                    fg=C["fg_dim"], font=FS, **kw)


def _entry(parent, var, width=10):
    return tk.Entry(
        parent, textvariable=var,
        bg=C["entry_bg"], fg=C["fg"], font=FM,
        relief="solid", bd=1, width=width,
        highlightthickness=1,
        highlightbackground=C["border"],
        highlightcolor=C["accent"],
        insertbackground=C["fg"],
    )


def _optmenu(parent, var, *choices):
    m = tk.OptionMenu(parent, var, *choices)
    m.config(bg=C["entry_bg"], fg=C["fg"], font=FS,
             relief="solid", bd=1, highlightthickness=0,
             activebackground=C["sel_bg"], activeforeground=C["fg"])
    m["menu"].config(bg=C["entry_bg"], fg=C["fg"], font=FS)
    return m


# ─── MODELO ──────────────────────────────────────────────────────────────────

@dataclass
class Trace:
    path: Path
    data: np.ndarray
    color: str
    label: str
    color_var:    tk.StringVar = field(default=None)   # type: ignore
    label_var:    tk.StringVar = field(default=None)   # type: ignore
    alpha_var:    tk.StringVar = field(default=None)   # type: ignore
    linestyle_var: tk.StringVar = field(default=None)  # type: ignore
    marker_var:   tk.StringVar = field(default=None)   # type: ignore
    lw_var:       tk.StringVar  = field(default=None)  # type: ignore
    ms_var:       tk.StringVar  = field(default=None)  # type: ignore
    connect_var:  tk.BooleanVar = field(default=None)  # type: ignore


# ─── APP ─────────────────────────────────────────────────────────────────────

class NpyPainterApp:

    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        root.title("NPY Painter")
        root.configure(bg=C["bg"])
        root.minsize(1100, 640)
        root.geometry("1360x760")

        self._traces: List[Trace] = []
        self._color_idx = 0
        self._build_ui()

    # ── UI ────────────────────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        tb = tk.Frame(self.root, bg=C["bg_tb"], height=44)
        tb.pack(fill="x", side="top")
        tk.Frame(tb, bg=C["accent"], width=4).pack(side="left", fill="y")
        tk.Label(tb, text="📉  NPY Painter  —  serie / señal",
                 bg=C["bg_tb"], fg=C["fg"], font=FH, padx=12,
                 ).pack(side="left", pady=8)

        pw = tk.PanedWindow(self.root, orient="horizontal",
                            bg=C["border"], sashwidth=5, sashrelief="flat")
        pw.pack(fill="both", expand=True)

        left = tk.Frame(pw, bg=C["bg_panel"], width=420)
        pw.add(left, minsize=380)
        self._build_left(left)

        right = tk.Frame(pw, bg=C["bg_panel"])
        pw.add(right, minsize=580)
        self._build_plot(right)

        sb = tk.Frame(self.root, bg=C["bg_tb"], height=26)
        sb.pack(fill="x", side="bottom")
        tk.Frame(sb, bg=C["accent"], width=4).pack(side="left", fill="y")
        self._status_var = tk.StringVar(value="Añade archivos .npy y pulsa Graficar.")
        tk.Label(sb, textvariable=self._status_var,
                 bg=C["bg_tb"], fg=C["fg_dim"], font=FS,
                 anchor="w", padx=10).pack(side="left")

    # ── PANEL IZQUIERDO ───────────────────────────────────────────────────────

    def _build_left(self, parent: tk.Frame) -> None:
        canvas = tk.Canvas(parent, bg=C["bg_panel"],
                           highlightthickness=0, bd=0)
        vsb = tk.Scrollbar(parent, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=vsb.set)
        vsb.pack(side="right", fill="y")
        canvas.pack(side="left", fill="both", expand=True)

        inner = tk.Frame(canvas, bg=C["bg_panel"])
        wid = canvas.create_window((0, 0), window=inner, anchor="nw")

        inner.bind("<Configure>",
                   lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.bind("<Configure>",
                    lambda e: canvas.itemconfig(wid, width=e.width))
        canvas.bind_all("<MouseWheel>",
                        lambda e: canvas.yview_scroll(
                            int(-1 * (e.delta / 120)), "units"))

        self._build_left_inner(inner)

    def _build_left_inner(self, p: tk.Frame) -> None:

        # ── ARCHIVOS ─────────────────────────────────────────────────────────
        _section(p, "ARCHIVOS .NPY")
        br = tk.Frame(p, bg=C["bg_panel"])
        br.pack(fill="x", padx=12, pady=(0, 6))
        _mk_btn(br, "+ Añadir .npy",  self._add_files, C["accent"],
                padx=10, pady=5).pack(side="left", padx=(0, 6))
        _mk_btn(br, "✕ Limpiar todo", self._clear_all, C["red"],
                padx=10, pady=5).pack(side="left")

        self._trace_container = tk.Frame(p, bg=C["bg_panel"])
        self._trace_container.pack(fill="x", padx=6)

        _sep(p)

        # ── EJE X ────────────────────────────────────────────────────────────
        _section(p, "EJE X")
        xr = tk.Frame(p, bg=C["bg_panel"])
        xr.pack(fill="x", padx=12, pady=(0, 6))
        self._xmode_var = tk.StringVar(value="índice")
        for val, txt in [("índice", "Índice (0, 1, 2…)"),
                         ("custom", "Archivo .npy externo")]:
            tk.Radiobutton(xr, text=txt, variable=self._xmode_var, value=val,
                           bg=C["bg_panel"], fg=C["fg"], font=FS,
                           selectcolor=C["sel_bg"],
                           activebackground=C["bg_panel"],
                           command=self._on_xmode_change,
                           ).pack(anchor="w")

        self._xfile_frame = tk.Frame(p, bg=C["bg_panel"])
        self._xfile_frame.pack(fill="x", padx=12, pady=(0, 4))
        self._xfile_var = tk.StringVar(value="")
        self._xfile_entry = _entry(self._xfile_frame, self._xfile_var, width=22)
        self._xfile_entry.pack(side="left", padx=(0, 4))
        _mk_btn(self._xfile_frame, "…", self._browse_xfile,
                C["fg_dim"], padx=8, pady=3).pack(side="left")
        self._xfile_frame.pack_forget()   # oculto por defecto

        _sep(p)

        # ── DIFERENCIA (visible solo con 2 trazas) ────────────────────────────
        self._diff_frame = tk.Frame(p, bg=C["bg_panel"])
        self._diff_frame.pack(fill="x", padx=12, pady=(0, 4))
        _section_lbl = tk.Label(
            self._diff_frame, text="DIFERENCIA  A − B",
            bg=C["bg_panel"], fg=C["accent"], font=FB, anchor="w")
        _section_lbl.pack(fill="x")

        diff_opts = tk.Frame(self._diff_frame, bg=C["bg_panel"])
        diff_opts.pack(fill="x", pady=(2, 4))
        self._diff_var = tk.BooleanVar(value=False)
        tk.Checkbutton(diff_opts, text="Dibujar A − B",
                       variable=self._diff_var,
                       bg=C["bg_panel"], fg=C["fg"], font=FS,
                       selectcolor=C["sel_bg"],
                       activebackground=C["bg_panel"],
                       ).pack(side="left")

        # color de la diferencia
        self._diff_color = DIFF_COLOR
        self._diff_color_swatch = tk.Label(
            diff_opts, bg=self._diff_color, width=3, cursor="hand2")
        self._diff_color_swatch.pack(side="left", padx=(10, 4))
        self._diff_color_swatch.bind("<Button-1>", self._pick_diff_color)

        _lbl(diff_opts, "Leyenda:").pack(side="left", padx=(6, 2))
        self._diff_label_var = tk.StringVar(value="A − B")
        _entry(diff_opts, self._diff_label_var, width=10).pack(side="left")

        self._diff_frame.pack_forget()   # oculto hasta tener 2 trazas

        # ── EJES ─────────────────────────────────────────────────────────────
        _section(p, "EJES")
        ag = tk.Frame(p, bg=C["bg_panel"])
        ag.pack(fill="x", padx=12, pady=(0, 6))

        self._ax_title_var    = tk.StringVar(value="")
        self._ax_xlabel_var   = tk.StringVar(value="")
        self._ax_ylabel_var   = tk.StringVar(value="")
        self._ax_xmin_var     = tk.StringVar(value="")
        self._ax_xmax_var     = tk.StringVar(value="")
        self._ax_ymin_var     = tk.StringVar(value="")
        self._ax_ymax_var     = tk.StringVar(value="")
        self._ax_fontsize_var = tk.StringVar(value="11")
        self._ax_font_var     = tk.StringVar(value="DejaVu Sans")
        self._ax_leg_font_var = tk.StringVar(value="DejaVu Sans")
        self._ax_leg_fs_var   = tk.StringVar(value="9")

        def _row(parent, lbl, var, r):
            tk.Label(parent, text=lbl, bg=C["bg_panel"], fg=C["fg_dim"],
                     font=FS, anchor="w", width=13,
                     ).grid(row=r, column=0, sticky="w", pady=2)
            _entry(parent, var, width=17).grid(
                row=r, column=1, sticky="w", padx=(4, 0), pady=2)

        def _fmenu(parent, var, lbl, r):
            tk.Label(parent, text=lbl, bg=C["bg_panel"], fg=C["fg_dim"],
                     font=FS, anchor="w", width=13,
                     ).grid(row=r, column=0, sticky="w", pady=2)
            m = _optmenu(parent, var, *_FONTS)
            m.config(width=15)
            m.grid(row=r, column=1, sticky="w", padx=(4, 0), pady=2)

        self._ax_xtick_pos_var = tk.StringVar(value="")
        self._ax_xtick_lbl_var = tk.StringVar(value="")
        self._ax_xtick_rot_var = tk.StringVar(value="0")
        self._ax_ytick_pos_var = tk.StringVar(value="")
        self._ax_ytick_lbl_var = tk.StringVar(value="")
        self._ax_ytick_rot_var = tk.StringVar(value="0")

        _row(ag, "Título:",       self._ax_title_var,    0)
        _row(ag, "Eje X:",        self._ax_xlabel_var,   1)
        _row(ag, "Eje Y:",        self._ax_ylabel_var,   2)
        _row(ag, "X mín:",        self._ax_xmin_var,     3)
        _row(ag, "X máx:",        self._ax_xmax_var,     4)
        _row(ag, "Y mín:",        self._ax_ymin_var,     5)
        _row(ag, "Y máx:",        self._ax_ymax_var,     6)
        _row(ag, "Tamaño (pt):",  self._ax_fontsize_var, 7)
        _fmenu(ag, self._ax_font_var,     "Fuente ejes:",    8)
        _fmenu(ag, self._ax_leg_font_var, "Fuente leyenda:", 9)
        _row(ag, "Tamaño ley.:",  self._ax_leg_fs_var,   10)

        # ── separador tick labels ──────────────────────────────────────────────
        tk.Frame(ag, bg=C["border"], height=1).grid(
            row=11, column=0, columnspan=2, sticky="ew", pady=6)

        tk.Label(ag, text="── TICK LABELS ──", bg=C["bg_panel"],
                 fg=C["accent"], font=FS, anchor="w",
                 ).grid(row=12, column=0, columnspan=2, sticky="w", pady=(0, 4))

        _row(ag, "X ticks:",      self._ax_xtick_pos_var, 13)
        _row(ag, "X etiquetas:",  self._ax_xtick_lbl_var, 14)
        _row(ag, "X rotación°:",  self._ax_xtick_rot_var, 15)
        _row(ag, "Y ticks:",      self._ax_ytick_pos_var, 16)
        _row(ag, "Y etiquetas:",  self._ax_ytick_lbl_var, 17)
        _row(ag, "Y rotación°:",  self._ax_ytick_rot_var, 18)

        # hints
        tk.Label(ag, text="(valores separados por comas  · etiquetas opcionales)",
                 bg=C["bg_panel"], fg=C["fg_dim"], font=("Segoe UI", 8),
                 anchor="w", wraplength=240, justify="left",
                 ).grid(row=19, column=0, columnspan=2, sticky="w", pady=(0, 4))

        _sep(p)

        # ── FIGURA ───────────────────────────────────────────────────────────
        _section(p, "FIGURA")
        fr1 = tk.Frame(p, bg=C["bg_panel"])
        fr1.pack(fill="x", padx=12, pady=(0, 4))
        _lbl(fr1, "Ancho (in):").pack(side="left")
        self._fig_w_var = tk.StringVar(value="9")
        _entry(fr1, self._fig_w_var, 5).pack(side="left", padx=4)
        _lbl(fr1, "Alto (in):").pack(side="left", padx=(8, 0))
        self._fig_h_var = tk.StringVar(value="5")
        _entry(fr1, self._fig_h_var, 5).pack(side="left", padx=4)

        fr2 = tk.Frame(p, bg=C["bg_panel"])
        fr2.pack(fill="x", padx=12, pady=(0, 8))
        _lbl(fr2, "DPI:").pack(side="left")
        self._fig_dpi_var = tk.StringVar(value="120")
        _entry(fr2, self._fig_dpi_var, 5).pack(side="left", padx=4)
        self._legend_var = tk.BooleanVar(value=True)
        tk.Checkbutton(fr2, text="Leyenda", variable=self._legend_var,
                       bg=C["bg_panel"], fg=C["fg"], font=FS,
                       selectcolor=C["sel_bg"],
                       activebackground=C["bg_panel"],
                       ).pack(side="left", padx=(14, 0))
        self._grid_var = tk.BooleanVar(value=True)
        tk.Checkbutton(fr2, text="Cuadrícula", variable=self._grid_var,
                       bg=C["bg_panel"], fg=C["fg"], font=FS,
                       selectcolor=C["sel_bg"],
                       activebackground=C["bg_panel"],
                       ).pack(side="left", padx=(10, 0))

        fr3 = tk.Frame(p, bg=C["bg_panel"])
        fr3.pack(fill="x", padx=12, pady=(0, 6))
        _lbl(fr3, "Ordenar datos:").pack(side="left")
        self._sort_var = tk.StringVar(value="ninguno")
        _optmenu(fr3, self._sort_var,
                 "ninguno", "ascendente", "descendente"
                 ).pack(side="left", padx=6)

        fr4 = tk.Frame(p, bg=C["bg_panel"])
        fr4.pack(fill="x", padx=12, pady=(0, 8))
        _lbl(fr4, "Tipo de gráfica:").pack(side="left")
        self._plot_mode_var = tk.StringVar(value="línea")
        _optmenu(fr4, self._plot_mode_var,
                 "línea", "puntos", "línea + puntos", "escalera"
                 ).pack(side="left", padx=6)

        # ── ESCALA EJES ──────────────────────────────────────────────────────
        fr5 = tk.Frame(p, bg=C["bg_panel"])
        fr5.pack(fill="x", padx=12, pady=(0, 4))
        _lbl(fr5, "Escala X:").pack(side="left")
        self._xscale_var = tk.StringVar(value="lineal")
        _optmenu(fr5, self._xscale_var, "lineal", "log").pack(side="left", padx=4)
        _lbl(fr5, "Base X:").pack(side="left", padx=(8, 2))
        self._xscale_base_var = tk.StringVar(value="10")
        _entry(fr5, self._xscale_base_var, 4).pack(side="left")

        fr6 = tk.Frame(p, bg=C["bg_panel"])
        fr6.pack(fill="x", padx=12, pady=(0, 8))
        _lbl(fr6, "Escala Y:").pack(side="left")
        self._yscale_var = tk.StringVar(value="lineal")
        _optmenu(fr6, self._yscale_var, "lineal", "log").pack(side="left", padx=4)
        _lbl(fr6, "Base Y:").pack(side="left", padx=(8, 2))
        self._yscale_base_var = tk.StringVar(value="10")
        _entry(fr6, self._yscale_base_var, 4).pack(side="left")

        _sep(p)

        # ── BOTONES ──────────────────────────────────────────────────────────
        ar = tk.Frame(p, bg=C["bg_panel"])
        ar.pack(pady=(4, 16), padx=12, fill="x")
        _mk_btn(ar, "📉  Graficar",   self._plot,     C["accent"],
                padx=18, pady=10).pack(side="left", padx=(0, 8))
        _mk_btn(ar, "💾  Guardar PNG", self._save_png, C["fg_dim"],
                padx=12, pady=10).pack(side="left")

    # ── PANEL DERECHO ─────────────────────────────────────────────────────────

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
        ax.set_axisbelow(True)

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
            color = self._next_color()
            t = Trace(
                path=path, data=data, color=color, label=path.stem,
                color_var=tk.StringVar(value=color),
                label_var=tk.StringVar(value=path.stem),
                alpha_var=tk.StringVar(value="1.0"),
                linestyle_var=tk.StringVar(value="solid"),
                marker_var=tk.StringVar(value="ninguno"),
                lw_var=tk.StringVar(value="1.5"),
                ms_var=tk.StringVar(value="4"),
                connect_var=tk.BooleanVar(value=False),
            )
            self._traces.append(t)
            self._add_trace_row(t)

        self._refresh_diff_panel()
        self._status_var.set(f"{len(self._traces)} archivo(s) cargado(s).")

    def _add_trace_row(self, t: Trace) -> None:
        row = tk.Frame(self._trace_container, bg=C["bg_panel"])
        row.pack(fill="x", padx=4, pady=2)
        tk.Frame(row, bg=C["border"], height=1).pack(fill="x")

        top = tk.Frame(row, bg=C["bg_panel"])
        top.pack(fill="x", pady=(4, 1))

        # color swatch
        swatch = tk.Label(top, bg=t.color_var.get(),
                          width=3, cursor="hand2")
        swatch.pack(side="left", padx=(6, 6))

        def pick(tr=t, sw=swatch):
            res = colorchooser.askcolor(color=tr.color_var.get(),
                                        title="Elige color")
            if res and res[1]:
                tr.color_var.set(res[1])
                tr.color = res[1]
                sw.config(bg=res[1])

        swatch.bind("<Button-1>", lambda e, tr=t, sw=swatch: pick(tr, sw))

        tk.Label(top, text=t.path.name, bg=C["bg_panel"],
                 fg=C["fg_dim"], font=FS, anchor="w", width=18,
                 ).pack(side="left", padx=(0, 4))

        _lbl(top, "Leyenda:").pack(side="left")
        _entry(top, t.label_var, width=11).pack(side="left", padx=4)

        def remove(tr=t, r=row):
            self._traces.remove(tr)
            r.destroy()
            self._refresh_diff_panel()
            self._status_var.set(f"{len(self._traces)} archivo(s) cargado(s).")

        _mk_btn(top, "✕", remove, C["red"], padx=6, pady=2
                ).pack(side="right", padx=6)

        # segunda fila: opciones de trazo
        bot = tk.Frame(row, bg=C["bg_panel"])
        bot.pack(fill="x", pady=(1, 4), padx=6)

        _lbl(bot, "α:").pack(side="left", padx=(6, 2))
        _entry(bot, t.alpha_var, 4).pack(side="left")
        tk.Scale(bot, from_=0.0, to=1.0, resolution=0.05,
                 orient="horizontal", length=70,
                 variable=t.alpha_var,
                 bg=C["bg_panel"], fg=C["fg_dim"],
                 troughcolor=C["border"], highlightthickness=0,
                 relief="flat", bd=0, font=FS, showvalue=False,
                 ).pack(side="left", padx=(2, 8))

        _lbl(bot, "Lw:").pack(side="left")
        _entry(bot, t.lw_var, 4).pack(side="left", padx=(2, 8))

        _lbl(bot, "Línea:").pack(side="left")
        _optmenu(bot, t.linestyle_var, *LINE_STYLES
                 ).pack(side="left", padx=4)

        _lbl(bot, "Marcador:").pack(side="left")
        _optmenu(bot, t.marker_var, *MARKER_OPTS
                 ).pack(side="left", padx=(4, 0))

        _lbl(bot, "Ms:").pack(side="left", padx=(4, 0))
        _entry(bot, t.ms_var, 3).pack(side="left", padx=(2, 6))

        _lbl(bot, "Unir pts:").pack(side="left", padx=(4, 0))
        tk.Checkbutton(bot, variable=t.connect_var,
                       bg=C["bg_panel"], fg=C["fg"], font=FS,
                       selectcolor=C["sel_bg"],
                       activebackground=C["bg_panel"],
                       ).pack(side="left", padx=(0, 4))

    def _clear_all(self) -> None:
        self._traces.clear()
        for w in self._trace_container.winfo_children():
            w.destroy()
        self._color_idx = 0
        self._refresh_diff_panel()
        self._status_var.set("Lista limpiada.")

    def _refresh_diff_panel(self) -> None:
        if len(self._traces) == 2:
            self._diff_frame.pack(fill="x", padx=12, pady=(0, 4),
                                  before=self._trace_container.master
                                  if False else None)
            # repack en posición correcta
            self._diff_frame.pack(fill="x", padx=0, pady=0)
        else:
            self._diff_var.set(False)
            self._diff_frame.pack_forget()

    # ── EJE X EXTERNO ────────────────────────────────────────────────────────

    def _on_xmode_change(self) -> None:
        if self._xmode_var.get() == "custom":
            self._xfile_frame.pack(fill="x", padx=12, pady=(0, 4))
        else:
            self._xfile_frame.pack_forget()

    def _browse_xfile(self) -> None:
        p = filedialog.askopenfilename(
            title="Archivo .npy para el eje X",
            filetypes=[("NumPy array", "*.npy"), ("Todos", "*.*")],
        )
        if p:
            self._xfile_var.set(p)

    def _get_x(self, n: int) -> Optional[np.ndarray]:
        if self._xmode_var.get() == "índice":
            return np.arange(n)
        p = self._xfile_var.get().strip()
        if not p:
            messagebox.showerror("Eje X", "Especifica un archivo .npy para el eje X.")
            return None
        try:
            x = np.load(p).astype(float).ravel()
        except Exception as exc:
            messagebox.showerror("Eje X", f"No se pudo cargar el eje X:\n{exc}")
            return None
        if len(x) != n:
            messagebox.showerror(
                "Eje X",
                f"El eje X tiene {len(x)} elementos pero la primera traza tiene {n}.\n"
                "Deben coincidir.",
            )
            return None
        return x

    # ── COLOR DIFERENCIA ─────────────────────────────────────────────────────

    def _pick_diff_color(self, _e=None) -> None:
        res = colorchooser.askcolor(color=self._diff_color,
                                    title="Color de la diferencia")
        if res and res[1]:
            self._diff_color = res[1]
            self._diff_color_swatch.config(bg=res[1])

    # ── GRAFICAR ──────────────────────────────────────────────────────────────

    def _parse_float(self, var: tk.StringVar) -> Optional[float]:
        s = var.get().strip()
        if not s:
            return None
        try:
            return float(s)
        except ValueError:
            return None

    def _plot(self) -> None:
        if not self._traces:
            self._status_var.set("Añade al menos un archivo .npy.")
            return

        try:
            fw  = float(self._fig_w_var.get())
            fh  = float(self._fig_h_var.get())
            dpi = float(self._fig_dpi_var.get())
        except ValueError:
            messagebox.showerror("Error", "Ancho, alto y DPI deben ser números.")
            return

        self._fig.set_size_inches(fw, fh)
        self._fig.set_dpi(dpi)
        self._ax.cla()
        self._style_axes()

        # ── escala de ejes ───────────────────────────────────────────────────
        def _apply_scale(axis: str, scale_var: tk.StringVar,
                         base_var: tk.StringVar) -> None:
            if scale_var.get() == "log":
                try:
                    base = float(base_var.get())
                except ValueError:
                    base = 10.0
                self._ax.set_scale = None  # no-op placeholder
                if axis == "x":
                    self._ax.set_xscale("log", base=base)
                else:
                    self._ax.set_yscale("log", base=base)
            else:
                if axis == "x":
                    self._ax.set_xscale("linear")
                else:
                    self._ax.set_yscale("linear")

        _apply_scale("x", self._xscale_var, self._xscale_base_var)
        _apply_scale("y", self._yscale_var, self._yscale_base_var)

        # cuadrícula
        if self._grid_var.get():
            self._ax.grid(True, color=C["grid"], linewidth=0.7,
                          linestyle="--", alpha=0.9)

        # determinar X (basado en la longitud de la primera traza)
        n0 = len(self._traces[0].data)
        x_arr = self._get_x(n0)
        if x_arr is None:
            return

        sort_mode = self._sort_var.get()

        for t in self._traces:
            alpha  = max(0.0, min(1.0, self._parse_float(t.alpha_var) or 1.0))
            lw     = self._parse_float(t.lw_var) or 1.5
            ms     = self._parse_float(t.ms_var) or 4
            ls     = t.linestyle_var.get()
            mk_raw = t.marker_var.get()
            mk     = MARKER_MAP.get(mk_raw, mk_raw)
            color  = t.color_var.get()
            label  = t.label_var.get() or t.path.stem

            y = t.data.copy()
            if sort_mode == "ascendente":
                y = np.sort(y)
            elif sort_mode == "descendente":
                y = np.sort(y)[::-1]

            x = x_arr if len(y) == len(x_arr) else np.arange(len(y))

            mode    = self._plot_mode_var.get()
            connect = t.connect_var.get()

            # ── Filtrar NaN ──────────────────────────────────────────────────
            # Si la traza tiene NaN (p.ej. validation_loss con checkpoints
            # dispersos), los eliminamos y usamos sus posiciones reales en x.
            # Así ax.plot() conecta los checkpoints en vez de crear gaps.
            nan_mask = ~np.isnan(y)
            xp = x[nan_mask]
            yp = y[nan_mask]
            if len(xp) == 0:
                continue   # traza vacía, no pintamos nada

            # Marcador efectivo: si el modo exige puntos y no hay marcador,
            # usamos "o" como fallback.
            needs_pts = mode in ("puntos", "línea + puntos") or connect
            eff_mk = mk if mk is not None else ("o" if needs_pts else None)

            if mode == "puntos":
                if connect:
                    # puntos conectados con línea
                    self._ax.plot(xp, yp, color=color, label=label,
                                  linewidth=lw, linestyle=ls,
                                  marker=eff_mk, markersize=ms,
                                  alpha=alpha)
                else:
                    # solo marcadores, sin línea
                    self._ax.scatter(xp, yp, color=color, label=label,
                                     s=ms ** 2,
                                     marker=eff_mk or "o",
                                     alpha=alpha)
            elif mode == "línea + puntos":
                self._ax.plot(xp, yp, color=color, label=label,
                              linewidth=lw, linestyle=ls,
                              marker=eff_mk, markersize=ms,
                              alpha=alpha)
            elif mode == "escalera":
                self._ax.step(xp, yp, color=color, label=label,
                              linewidth=lw, linestyle=ls,
                              where="mid", alpha=alpha)
                if connect:
                    self._ax.plot(xp, yp, color=color, linestyle="None",
                                  marker=eff_mk or "o", markersize=ms,
                                  alpha=alpha, label="_nolegend_")
            else:  # "línea" — conecta todos los puntos no-NaN con una línea
                # Si la línea es discontinua o "Unir pts" está marcado,
                # también fuerza marcadores en cada punto (visible para
                # checkpoints o series cortas).
                linea_discontinua = ls in ("dashed", "dashdot", "dotted")
                final_mk = eff_mk if (connect or linea_discontinua) else mk
                self._ax.plot(xp, yp, color=color, label=label,
                              linewidth=lw, linestyle=ls,
                              marker=final_mk, markersize=ms,
                              alpha=alpha)

        # diferencia A − B
        if len(self._traces) == 2 and self._diff_var.get():
            yA = self._traces[0].data.copy()
            yB = self._traces[1].data.copy()
            if sort_mode == "ascendente":
                yA = np.sort(yA)
                yB = np.sort(yB)
            elif sort_mode == "descendente":
                yA = np.sort(yA)[::-1]
                yB = np.sort(yB)[::-1]
            if len(yA) != len(yB):
                messagebox.showwarning(
                    "Diferencia",
                    f"Las dos trazas tienen distinto número de elementos "
                    f"({len(yA)} vs {len(yB)}). No se puede calcular A − B.",
                )
            else:
                diff = yA - yB
                dlbl = self._diff_label_var.get() or "A − B"
                self._ax.plot(x_arr, diff, color=self._diff_color,
                              label=dlbl, linewidth=1.5,
                              linestyle="dashed", alpha=0.85)

        # etiquetas y límites
        self._apply_axes()

        if self._legend_var.get():
            leg_fs  = self._parse_float(self._ax_leg_fs_var) or 9
            leg_fam = self._ax_leg_font_var.get() or "DejaVu Sans"
            self._ax.legend(fontsize=leg_fs, framealpha=0.9,
                            edgecolor=C["border"],
                            prop={"family": leg_fam, "size": leg_fs})

        try:
            self._fig.tight_layout()
        except Exception:
            pass  # tight_layout puede fallar en algunos sistemas; se ignora

        self._canvas.draw()
        self._canvas.flush_events()
        self._status_var.set(
            f"Graficado — {len(self._traces)} traza(s).")

    # ── HELPERS TICKS ─────────────────────────────────────────────────────────

    def _parse_csv_floats(self, var: tk.StringVar) -> Optional[list]:
        """Parsea "1, 2.5, 10" → [1.0, 2.5, 10.0] o None si vacío/error."""
        s = var.get().strip()
        if not s:
            return None
        try:
            return [float(x.strip()) for x in s.split(",") if x.strip()]
        except ValueError:
            return None

    def _parse_csv_strings(self, var: tk.StringVar) -> Optional[list]:
        """Parsea "Ene, Feb, Mar" → ['Ene', 'Feb', 'Mar'] o None si vacío."""
        s = var.get().strip()
        if not s:
            return None
        return [x.strip() for x in s.split(",")]

    def _apply_axes(self) -> None:
        ax = self._ax
        fs      = self._parse_float(self._ax_fontsize_var)
        font_f  = self._ax_font_var.get()

        title  = self._ax_title_var.get().strip()
        xlabel = self._ax_xlabel_var.get().strip()
        ylabel = self._ax_ylabel_var.get().strip()

        ax.set_title(title or "", color=C["plot_fg"])
        ax.set_xlabel(xlabel or "Índice", color=C["plot_fg"])
        ax.set_ylabel(ylabel or "Valor",  color=C["plot_fg"])

        xmin = self._parse_float(self._ax_xmin_var)
        xmax = self._parse_float(self._ax_xmax_var)
        ymin = self._parse_float(self._ax_ymin_var)
        ymax = self._parse_float(self._ax_ymax_var)

        if xmin is not None or xmax is not None:
            cx0, cx1 = ax.get_xlim()
            ax.set_xlim(xmin if xmin is not None else cx0,
                        xmax if xmax is not None else cx1)
        if ymin is not None or ymax is not None:
            cy0, cy1 = ax.get_ylim()
            ax.set_ylim(ymin if ymin is not None else cy0,
                        ymax if ymax is not None else cy1)

        # ── TICK LABELS X ────────────────────────────────────────────────────
        xtick_pos  = self._parse_csv_floats(self._ax_xtick_pos_var)
        xtick_lbl  = self._parse_csv_strings(self._ax_xtick_lbl_var)
        xtick_rot  = self._parse_float(self._ax_xtick_rot_var) or 0.0

        if xtick_pos is not None:
            ax.set_xticks(xtick_pos)
            if xtick_lbl is not None:
                # si hay menos etiquetas que posiciones, rellenamos con str(pos)
                lbls = xtick_lbl + [str(v) for v in xtick_pos[len(xtick_lbl):]]
                ax.set_xticklabels(lbls[:len(xtick_pos)], rotation=xtick_rot,
                                   ha="right" if xtick_rot != 0 else "center")
            elif xtick_rot != 0:
                ax.tick_params(axis="x", labelrotation=xtick_rot)
        elif xtick_rot != 0:
            ax.tick_params(axis="x", labelrotation=xtick_rot)

        # ── TICK LABELS Y ────────────────────────────────────────────────────
        ytick_pos  = self._parse_csv_floats(self._ax_ytick_pos_var)
        ytick_lbl  = self._parse_csv_strings(self._ax_ytick_lbl_var)
        ytick_rot  = self._parse_float(self._ax_ytick_rot_var) or 0.0

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

        # ── FUENTE Y TAMAÑO DE TODOS LOS ELEMENTOS ───────────────────────────
        axis_items = ([ax.title, ax.xaxis.label, ax.yaxis.label]
                      + ax.get_xticklabels() + ax.get_yticklabels())
        for item in axis_items:
            if font_f:
                item.set_fontfamily(font_f)
            if fs is not None:
                item.set_fontsize(fs)

    # ── GUARDAR ───────────────────────────────────────────────────────────────

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
    NpyPainterApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()