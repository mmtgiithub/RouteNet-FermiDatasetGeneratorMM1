#!/usr/bin/env python3
from __future__ import annotations  # Compatibilidad con Python 3.8+
"""
Network Graph Editor — formato GML
========================================
Editor visual interactivo para crear topologías de red dirigidas (multigraph)
y exportarlas en formato GML compatible con simuladores de red.

Atributos de NODO  : id, label, queueSizes
Atributos de ENLACE: source, target, key, port, weight, bandwidth

Controles:
  S            — modo Seleccionar / arrastrar nodos
  N            — modo Añadir Nodo (clic en canvas)
  E            — modo Añadir Enlace (clic origen → destino)
  Rueda ratón  — zoom centrado en el cursor
  Botón medio  — arrastrar para hacer pan
  Ctrl+0       — resetear zoom y pan
  Supr / BackSpace — eliminar elemento seleccionado
  Doble clic en número de puerto → edición inline

Requiere: Python 3.8+ con tkinter (incluido en la instalación estándar).
"""

import tkinter as tk
import tkinter.font as tkfont
from tkinter import messagebox, filedialog
from collections import defaultdict
import math
import re
import random

# ════════════════════════════════════════════════════════════════════════════
#  Paleta de colores (tema oscuro estilo Windows 11)
# ════════════════════════════════════════════════════════════════════════════
C = {
    "bg_canvas":  "#0d0d1a",
    "bg_toolbar": "#181827",
    "bg_panel":   "#13132a",
    "bg_entry":   "#1f1f38",
    "bg_sep":     "#252540",
    "grid":       "#111122",
    "accent":     "#7c3aed",
    "accent_h":   "#9461f7",
    "green":      "#10b981",
    "amber":      "#f59e0b",
    "txt":        "#e2e8f0",
    "dim":        "#64748b",
    "edge_col":   "#5b5bd6",
    "node_fill":  "#1e1e40",
    "node_stk":   "#7c3aed",
    "port_lbl":   "#a0c4ff",
}
R = 26          # radio del nodo en coordenadas mundo
STEPS = 32      # pasos de la curva Bézier


# ════════════════════════════════════════════════════════════════════════════
#  Utilidades geométricas
# ════════════════════════════════════════════════════════════════════════════
def bw_fmt(s: str) -> str:
    try:
        v = int(s)
        for limit, unit in [(1_000_000_000, "G"), (1_000_000, "M"), (1_000, "K")]:
            if v >= limit:
                return f"{v // limit}{unit}"
        return str(v)
    except ValueError:
        return s


def bezier_pts(p0, cp, p1, steps=STEPS):
    out = []
    for i in range(steps + 1):
        t = i / steps
        s = 1 - t
        out += [
            s * s * p0[0] + 2 * s * t * cp[0] + t * t * p1[0],
            s * s * p0[1] + 2 * s * t * cp[1] + t * t * p1[1],
        ]
    return out


def border_pt(cx, cy, tx, ty, r):
    dx, dy = tx - cx, ty - cy
    d = math.hypot(dx, dy)
    if d < 1e-9:
        return cx, cy
    return cx + dx / d * r, cy + dy / d * r


# ════════════════════════════════════════════════════════════════════════════
#  Aplicación principal
# ════════════════════════════════════════════════════════════════════════════
class NetworkGraphApp:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("Network Graph Editor  ·  GML")
        self.root.geometry("1400x820")
        self.root.minsize(960, 600)
        self.root.configure(bg=C["bg_toolbar"])

        # ── Datos del grafo ──────────────────────────────────────────
        self.nodes: dict[int, dict] = {}
        self.edges: list[dict] = []
        self.nxt_id: int = 0

        # ── Estado de interacción ────────────────────────────────────
        self.mode = "select"
        self.sel = None
        self.edge_src: int | None = None
        self.drag_id:  int | None = None
        self.drag_ox = self.drag_oy = 0.0
        self.hov_id:   int | None = None

        # ── Zoom / Pan ───────────────────────────────────────────────
        self.zoom = 1.0
        self.pan_x = 0.0
        self.pan_y = 0.0
        self._pan_origin: tuple | None = None   # (ev.x, ev.y) al iniciar pan
        self._pan_base: tuple | None = None     # (pan_x, pan_y) al iniciar pan

        # ── Posiciones de puertos (canvas coords) para detección en _on_dbl
        self._port_hits: list = []   # [(cx, cy, edge_idx), ...]

        # ── Flag: hay un mini-editor de puerto abierto en el canvas
        self._inline_open: bool = False

        # Fuente monoespaciada disponible
        families = tkfont.families()
        self._mono = next(
            (f for f in ("Cascadia Code", "Consolas", "Courier New") if f in families),
            "Courier"
        )

        self._build_ui()
        self._redraw()
        self._gml_preview()

    # ── Utilidades de coordenadas ────────────────────────────────────────
    def _w2c(self, wx, wy):
        """Mundo → Canvas."""
        return wx * self.zoom + self.pan_x, wy * self.zoom + self.pan_y

    def _c2w(self, cx, cy):
        """Canvas → Mundo."""
        return (cx - self.pan_x) / self.zoom, (cy - self.pan_y) / self.zoom

    def _rc(self) -> float:
        """Radio del nodo en píxeles canvas."""
        return max(10.0, R * self.zoom)

    def _fs(self, base: int) -> int:
        """Tamaño de fuente escalado con zoom."""
        return max(6, int(base * self.zoom))

    def _canvas_cursor(self) -> str:
        return {"select": "arrow", "add_node": "crosshair",
                "add_edge": "tcross", "pan": "fleur"}.get(self.mode, "arrow")

    # ════════════════════════════════════════════════════════════════════
    #  CONSTRUCCIÓN DE LA INTERFAZ
    # ════════════════════════════════════════════════════════════════════
    def _build_ui(self):
        self._build_toolbar()
        mid = tk.Frame(self.root, bg=C["bg_toolbar"])
        mid.pack(fill="both", expand=True)
        self._build_canvas(mid)
        self._build_panel(mid)
        self._build_statusbar()

    # ── Toolbar ─────────────────────────────────────────────────────────
    def _build_toolbar(self):
        bar = tk.Frame(self.root, bg=C["bg_toolbar"], pady=6)
        bar.pack(fill="x", padx=8)

        def B(text, cmd, color=None, bold=False):
            bg = color or "#252540"
            font = ("Segoe UI", 10, "bold") if bold else ("Segoe UI", 10)
            b = tk.Button(bar, text=text, command=cmd,
                          bg=bg, fg=C["txt"],
                          activebackground=C["accent_h"], activeforeground="white",
                          relief="flat", bd=0, padx=14, pady=7,
                          cursor="hand2", font=font)
            b.pack(side="left", padx=3)
            return b

        B("🖱  Seleccionar", lambda: self.set_mode("select"))
        B("⬡  Nodo",        lambda: self.set_mode("add_node"))
        B("➜  Enlace",      lambda: self.set_mode("add_edge"))

        # Checkbox "2 enlaces simétricos"
        self._bilink_var = tk.BooleanVar(value=False)
        tk.Checkbutton(bar, text="⇄ Bienlace",
                       variable=self._bilink_var,
                       bg=C["bg_toolbar"], fg=C["txt"],
                       selectcolor="#2a1a5e",
                       activebackground=C["bg_toolbar"],
                       activeforeground=C["txt"],
                       font=("Segoe UI", 10),
                       relief="flat", bd=0,
                       cursor="hand2").pack(side="left", padx=(0, 8))

        B("🗑  Eliminar",    self.delete_sel)
        B("🔲  Limpiar",     self.clear_all)

        tk.Frame(bar, bg=C["bg_sep"], width=1).pack(
            side="left", padx=12, fill="y", pady=4)

        B("💾  Exportar GML", self.export_gml, color=C["accent"], bold=True)
        B("📂  Importar GML", self.import_gml)

        # Botones de zoom
        tk.Frame(bar, bg=C["bg_sep"], width=1).pack(
            side="left", padx=12, fill="y", pady=4)
        B("🔍+", lambda: self._zoom_step(1.2))
        B("🔍−", lambda: self._zoom_step(0.8))
        B("⌖",  self._zoom_reset)

        # Botón modo pan (arrastrar canvas con botón izquierdo)
        tk.Frame(bar, bg=C["bg_sep"], width=1).pack(
            side="left", padx=12, fill="y", pady=4)
        B("✥  Mover", lambda: self.set_mode("pan"))

        self._mode_var = tk.StringVar(value="● SELECCIONAR")
        tk.Label(bar, textvariable=self._mode_var, bg=C["bg_toolbar"],
                 fg=C["accent"], font=("Segoe UI", 9, "bold")).pack(
            side="right", padx=14)

        # Atajos de teclado
        for key, mode in [("s", "select"), ("n", "add_node"), ("e", "add_edge")]:
            self.root.bind(f"<{key}>", lambda _, m=mode: self.set_mode(m))
        self.root.bind("<Delete>",    lambda _: self.delete_sel())
        self.root.bind("<BackSpace>", lambda _: self.delete_sel())
        self.root.bind("<Control-0>", lambda _: self._zoom_reset())

    # ── Canvas ──────────────────────────────────────────────────────────
    def _build_canvas(self, parent):
        self.cv = tk.Canvas(parent, bg=C["bg_canvas"], highlightthickness=0)
        self.cv.pack(side="left", fill="both", expand=True)

        # Ratón principal
        self.cv.bind("<Button-1>",        self._on_click)
        self.cv.bind("<B1-Motion>",       self._on_drag)
        self.cv.bind("<ButtonRelease-1>", self._on_release)
        self.cv.bind("<Motion>",          self._on_hover)
        self.cv.bind("<Double-Button-1>", self._on_dbl)

        # Zoom con rueda
        self.cv.bind("<MouseWheel>", self._on_wheel)   # Windows / macOS
        self.cv.bind("<Button-4>",   self._on_wheel)   # Linux scroll ↑
        self.cv.bind("<Button-5>",   self._on_wheel)   # Linux scroll ↓

        # Pan con botón central
        self.cv.bind("<ButtonPress-2>",   self._pan_start)
        self.cv.bind("<B2-Motion>",       self._pan_move)
        self.cv.bind("<ButtonRelease-2>", self._pan_end)

    # ── Panel derecho ────────────────────────────────────────────────────
    def _build_panel(self, parent):
        p = tk.Frame(parent, bg=C["bg_panel"], width=300)
        p.pack(side="right", fill="y")
        p.pack_propagate(False)

        tk.Label(p, text="  Propiedades", bg=C["bg_panel"], fg=C["txt"],
                 font=("Segoe UI", 11, "bold"), pady=10, anchor="w").pack(fill="x")
        tk.Frame(p, bg=C["bg_sep"], height=1).pack(fill="x")

        self._pf = tk.Frame(p, bg=C["bg_panel"], padx=12)
        self._pf.pack(fill="both", expand=True, pady=8)
        self._empty_props()

        tk.Frame(p, bg=C["bg_sep"], height=1).pack(fill="x")
        tk.Label(p, text="  Vista previa GML", bg=C["bg_panel"], fg=C["dim"],
                 font=("Segoe UI", 9, "bold"), pady=5, anchor="w").pack(fill="x")

        self._gt = tk.Text(
            p, bg="#080814", fg="#98c379",
            font=(self._mono, 8),
            relief="flat", height=14, wrap="none",
            state="disabled", insertbackground="white",
            padx=6, pady=5, selectbackground=C["accent"])
        self._gt.pack(fill="both", expand=True, padx=6, pady=(0, 8))

    # ── Barra de estado ──────────────────────────────────────────────────
    def _build_statusbar(self):
        bar = tk.Frame(self.root, bg="#08080f", height=22)
        bar.pack(fill="x")
        bar.pack_propagate(False)
        self._sv = tk.StringVar(value="Listo  ·  Elige un modo en la barra superior")
        tk.Label(bar, textvariable=self._sv, bg="#08080f", fg=C["dim"],
                 font=("Segoe UI", 9), padx=10, anchor="w").pack(fill="x")

    # ════════════════════════════════════════════════════════════════════
    #  ZOOM / PAN
    # ════════════════════════════════════════════════════════════════════
    def _on_wheel(self, ev):
        # Windows/macOS: ev.delta > 0 → acercar
        # Linux: ev.num == 4 → acercar, ev.num == 5 → alejar
        if getattr(ev, "num", 0) == 5 or (hasattr(ev, "delta") and ev.delta < 0):
            factor = 0.9
        else:
            factor = 1.1
        self._apply_zoom(factor, ev.x, ev.y)

    def _zoom_step(self, factor: float):
        W = self.cv.winfo_width()  or 700
        H = self.cv.winfo_height() or 450
        self._apply_zoom(factor, W / 2, H / 2)

    def _apply_zoom(self, factor: float, cx: float, cy: float):
        old = self.zoom
        self.zoom = max(0.15, min(8.0, self.zoom * factor))
        real = self.zoom / old
        self.pan_x = cx - (cx - self.pan_x) * real
        self.pan_y = cy - (cy - self.pan_y) * real
        self._redraw()

    def _zoom_reset(self):
        self.zoom = 1.0
        self.pan_x = 0.0
        self.pan_y = 0.0
        self._redraw()

    def _pan_step(self, dx: float, dy: float):
        self.pan_x += dx
        self.pan_y += dy
        self._redraw()

    def _pan_start(self, ev):
        self._pan_origin = (ev.x, ev.y)
        self._pan_base   = (self.pan_x, self.pan_y)
        self.cv.configure(cursor="fleur")

    def _pan_move(self, ev):
        if not self._pan_origin:
            return
        dx = ev.x - self._pan_origin[0]
        dy = ev.y - self._pan_origin[1]
        self.pan_x = self._pan_base[0] + dx
        self.pan_y = self._pan_base[1] + dy
        self._redraw()

    def _pan_end(self, _ev):
        self._pan_origin = None
        try:
            self.cv.configure(cursor=self._canvas_cursor())
        except Exception:
            pass

    # ════════════════════════════════════════════════════════════════════
    #  PANEL DE PROPIEDADES
    # ════════════════════════════════════════════════════════════════════
    def _clr_props(self):
        for w in self._pf.winfo_children():
            w.destroy()

    def _field(self, parent, label: str, value, hint="", readonly=False):
        row = tk.Frame(parent, bg=C["bg_panel"])
        row.pack(fill="x", pady=2)
        tk.Label(row, text=label, bg=C["bg_panel"], fg=C["dim"],
                 font=("Segoe UI", 9), width=12, anchor="w").pack(side="left")
        var = tk.StringVar(value=str(value))
        bg    = "#161630" if readonly else C["bg_entry"]
        fg    = C["dim"]  if readonly else C["txt"]
        state = "readonly" if readonly else "normal"
        e = tk.Entry(row, textvariable=var, bg=bg, fg=fg,
                     relief="flat", font=(self._mono, 9),
                     insertbackground="white", readonlybackground="#161630",
                     highlightthickness=1,
                     highlightcolor=C["accent"],
                     highlightbackground=C["bg_sep"],
                     state=state)
        e.pack(side="left", fill="x", expand=True, padx=(4, 0))
        if hint:
            tk.Label(row, text=hint, bg=C["bg_panel"], fg="#333355",
                     font=("Segoe UI", 8), width=5).pack(side="left", padx=2)
        return var

    def _apply_btn(self, parent, text, cmd, color=None):
        tk.Button(parent, text=text, command=cmd,
                  bg=color or C["accent"], fg="white",
                  relief="flat", bd=0, padx=10, pady=6,
                  cursor="hand2", font=("Segoe UI", 10, "bold")
                  ).pack(fill="x", pady=(12, 0))

    def _sep(self):
        tk.Frame(self._pf, bg=C["bg_sep"], height=1).pack(fill="x", pady=6)

    def _empty_props(self):
        self._clr_props()
        tk.Label(self._pf,
                 text="\n\nSelecciona un nodo\no enlace del grafo\n\n"
                      "S  →  Seleccionar\n"
                      "N  →  Añadir Nodo\n"
                      "E  →  Añadir Enlace",
                 bg=C["bg_panel"], fg=C["dim"],
                 font=("Segoe UI", 10), justify="center").pack(expand=True)

    def _node_props(self, nid: int):
        self._clr_props()
        n = self.nodes[nid]
        tk.Label(self._pf, text="⬡  NODO", bg=C["bg_panel"],
                 fg=C["accent"], font=("Segoe UI", 10, "bold")).pack(anchor="w")
        self._sep()
        self._field(self._pf, "id",          nid,               readonly=True)
        vl = self._field(self._pf, "label",      n["label"])
        vq = self._field(self._pf, "queueSizes", n["queueSizes"], hint="pkt")

        def apply():
            self.nodes[nid]["label"]      = vl.get()
            self.nodes[nid]["queueSizes"] = vq.get()
            self._redraw(); self._gml_preview()
            self.status(f"Nodo {nid} actualizado")

        self._apply_btn(self._pf, "✓  Aplicar cambios", apply)

    def _edge_props(self, idx: int):
        self._clr_props()
        e = self.edges[idx]
        tk.Label(self._pf, text="➜  ENLACE", bg=C["bg_panel"],
                 fg=C["green"], font=("Segoe UI", 10, "bold")).pack(anchor="w")
        self._sep()
        self._field(self._pf, "source",    e["source"],    readonly=True)
        self._field(self._pf, "target",    e["target"],    readonly=True)
        vk  = self._field(self._pf, "key",       e["key"],       hint="≠ToS")
        vp  = self._field(self._pf, "port",      e["port"])
        vw  = self._field(self._pf, "weight",    e["weight"])
        vbw = self._field(self._pf, "bandwidth", e["bandwidth"], hint="bps")

        def apply():
            try:
                self.edges[idx]["key"]       = int(vk.get())
                self.edges[idx]["port"]      = int(vp.get())
                self.edges[idx]["weight"]    = int(vw.get())
                self.edges[idx]["bandwidth"] = vbw.get()
                self._redraw(); self._gml_preview()
                self.status(f"Enlace {idx} actualizado")
            except ValueError:
                messagebox.showerror("Error de formato",
                                     "key, port y weight deben ser enteros.")

        self._apply_btn(self._pf, "✓  Aplicar cambios", apply, color=C["green"])

    # ════════════════════════════════════════════════════════════════════
    #  EVENTOS DEL CANVAS
    # ════════════════════════════════════════════════════════════════════
    def _node_at(self, cx, cy) -> int | None:
        rc = self._rc()
        for nid, n in self.nodes.items():
            nx, ny = self._w2c(n["x"], n["y"])
            if math.hypot(cx - nx, cy - ny) <= rc + 5:
                return nid
        return None

    def _edge_at(self, cx, cy, tol=9) -> int | None:
        """Detección de clic sobre la curva Bézier real (espacio canvas)."""
        groups: dict = defaultdict(list)
        for i, e in enumerate(self.edges):
            groups[(e["source"], e["target"])].append((i, e))

        best_dist = float("inf")
        best_idx  = None
        rc = self._rc()

        for (sid, tid), lst in groups.items():
            sn = self.nodes.get(sid)
            tn = self.nodes.get(tid)
            if not sn or not tn:
                continue
            x1, y1 = self._w2c(sn["x"], sn["y"])
            x2, y2 = self._w2c(tn["x"], tn["y"])
            has_reverse = (tid, sid) in groups
            base_off = 22 * self.zoom if has_reverse else 0
            total = len(lst)

            for pos, (i, _e) in enumerate(lst):
                offset = (pos - (total - 1) / 2) * 30 * self.zoom + base_off
                dx, dy = x2 - x1, y2 - y1
                ln = math.hypot(dx, dy) or 1
                nx, ny = -dy / ln * offset, dx / ln * offset
                cpx = (x1 + x2) / 2 + nx
                cpy = (y1 + y2) / 2 + ny
                ps = border_pt(x1, y1, cpx, cpy, rc + 2)
                pe = border_pt(x2, y2, cpx, cpy, rc + 2)
                pts = bezier_pts(ps, (cpx, cpy), pe, steps=24)
                for k in range(0, len(pts) - 2, 2):
                    d = math.hypot(cx - pts[k], cy - pts[k + 1])
                    if d < best_dist:
                        best_dist = d
                        best_idx  = i

        return best_idx if best_dist <= tol else None

    def _on_click(self, ev):
        x, y = ev.x, ev.y

        if self.mode == "pan":
            self._pan_origin = (x, y)
            self._pan_base   = (self.pan_x, self.pan_y)
            return

        # Si hay editor inline abierto, no hacer NADA más:
        # el clic roba el foco del Entry → FocusOut commitea solo.
        # Llamar _redraw() aquí destruiría el Entry sin pasar por _close().
        if self._inline_open:
            return

        # Clic sobre número de puerto → abrir editor inline (un solo clic)
        if self.mode == "select":
            hit_r = max(20, int(18 * self.zoom))
            for (plx, ply, eidx) in self._port_hits:
                if math.hypot(x - plx, y - ply) <= hit_r:
                    if self.sel != ("edge", eidx):
                        self.sel = ("edge", eidx)
                        self._edge_props(eidx)
                    self._inline_edit_port(plx, ply, eidx)
                    return

        if self.mode == "add_node":
            wx, wy = self._c2w(x, y)
            self._add_node(wx, wy)
            return

        nid  = self._node_at(x, y)
        eidx = None if nid is not None else self._edge_at(x, y)

        if self.mode == "add_edge":
            if nid is not None:
                if self.edge_src is None:
                    self.edge_src = nid
                    self.status(f"Enlace: origen = nodo {nid}  ·  ahora clic en el destino")
                    self._redraw()
                else:
                    if nid != self.edge_src:
                        self._add_edge(self.edge_src, nid)
                        if self._bilink_var.get():
                            self._add_edge(nid, self.edge_src)
                    self.edge_src = None
                    self._redraw()
            return

        if nid is not None:
            self.sel = ("node", nid)
            self.drag_id = nid
            nx_c, ny_c = self._w2c(self.nodes[nid]["x"], self.nodes[nid]["y"])
            self.drag_ox = x - nx_c
            self.drag_oy = y - ny_c
            self._node_props(nid)
        elif eidx is not None:
            self.sel = ("edge", eidx)
            self.drag_id = None
            self._edge_props(eidx)
        else:
            self.sel = None
            self.drag_id = None
            self._empty_props()

        self._redraw()

    def _on_drag(self, ev):
        if self.mode == "pan" and self._pan_origin:
            dx = ev.x - self._pan_origin[0]
            dy = ev.y - self._pan_origin[1]
            self.pan_x = self._pan_base[0] + dx
            self.pan_y = self._pan_base[1] + dy
            self._redraw()
            return

        if self.mode == "select" and self.drag_id is not None:
            target_cx = ev.x - self.drag_ox
            target_cy = ev.y - self.drag_oy
            n = self.nodes[self.drag_id]
            n["x"], n["y"] = self._c2w(target_cx, target_cy)
            self._redraw()

    def _on_release(self, _ev):
        self.drag_id = None
        if self.mode == "pan":
            self._pan_origin = None

    def _on_hover(self, ev):
        x, y = ev.x, ev.y
        nid = self._node_at(x, y)
        if nid != self.hov_id:
            self.hov_id = nid
            if not self._inline_open:   # no redibujar si el mini-editor está abierto
                self._redraw()
        # Cursor mano si estamos cerca de un puerto
        hit_r = max(20, int(18 * self.zoom))
        near_port = any(math.hypot(x - plx, y - ply) <= hit_r
                        for (plx, ply, _) in self._port_hits)
        try:
            self.cv.configure(cursor="hand2" if near_port else self._canvas_cursor())
        except Exception:
            pass

    def _on_dbl(self, ev):
        nid = self._node_at(ev.x, ev.y)
        if nid is not None:
            self.sel = ("node", nid)
            self._node_props(nid)
            self._redraw()

    # ════════════════════════════════════════════════════════════════════
    #  EDICIÓN INLINE DEL PUERTO
    # ════════════════════════════════════════════════════════════════════
    def _inline_edit_port(self, cx: float, cy: float, edge_idx: int):
        """Entry embebido en el canvas para editar el puerto directamente."""
        e = self.edges[edge_idx]
        old_val = e["port"]
        var = tk.StringVar(value=str(old_val))

        entry = tk.Entry(self.cv, textvariable=var, width=4,
                         bg="#2a2a50", fg=C["port_lbl"],
                         font=("Segoe UI", 9, "bold"),
                         relief="flat", justify="center",
                         insertbackground=C["port_lbl"],
                         highlightthickness=1,
                         highlightcolor=C["accent"],
                         highlightbackground=C["accent"])

        win_id = self.cv.create_window(cx, cy, window=entry, width=38, height=20)
        self._inline_open = True
        entry.focus_set()
        entry.select_range(0, "end")

        closed = [False]

        def _close():
            if closed[0]:
                return
            closed[0] = True
            self._inline_open = False
            try:
                self.cv.delete(win_id)
                entry.destroy()
            except Exception:
                pass

        def try_commit(_=None):
            try:
                new_val = int(var.get())
            except ValueError:
                _close()
                self._redraw()
                return
            _close()
            self.edges[edge_idx]["port"] = new_val
            self.status(f"Puerto del enlace {edge_idx}: {old_val} → {new_val}")
            self._redraw()
            self._gml_preview()
            if self.sel == ("edge", edge_idx):
                self._edge_props(edge_idx)

        def cancel(_=None):
            _close()
            self._redraw()

        entry.bind("<Return>",   try_commit)
        entry.bind("<KP_Enter>", try_commit)
        entry.bind("<Escape>",   cancel)

        def _arm_focusout():
            if closed[0]:
                return          # ya cerrado antes del retardo → no hacer nada
            try:
                entry.bind("<FocusOut>", try_commit)
            except Exception:
                pass

        # FocusOut activo tras 150 ms para ignorar el FocusOut espúreo
        # que tkinter dispara al crear/enfocar el widget
        entry.after(150, _arm_focusout)

    # ════════════════════════════════════════════════════════════════════
    #  CREACIÓN DE ELEMENTOS
    # ════════════════════════════════════════════════════════════════════
    def _add_node(self, wx: float, wy: float):
        nid = self.nxt_id
        self.nxt_id += 1
        self.nodes[nid] = {
            "id": nid, "label": str(nid),
            "queueSizes": "32", "x": wx, "y": wy,
        }
        self.sel = ("node", nid)
        self._node_props(nid)
        self._redraw(); self._gml_preview()
        self.status(f"Nodo {nid} creado  ·  edita sus atributos en el panel derecho")

    def _add_edge(self, src: int, tgt: int):
        key = sum(1 for e in self.edges if e["source"] == src and e["target"] == tgt)
        idx = len(self.edges)
        self.edges.append({
            "source": src, "target": tgt, "key": key,
            "port": 0, "weight": 1, "bandwidth": "1000000",
        })
        self.sel = ("edge", idx)
        self._edge_props(idx)
        self._redraw(); self._gml_preview()
        self.status(f"Enlace {src} → {tgt} creado  ·  edita sus atributos")

    # ════════════════════════════════════════════════════════════════════
    #  DIBUJO DEL GRAFO
    # ════════════════════════════════════════════════════════════════════
    def _redraw(self):
        cv = self.cv
        cv.delete("all")
        self._port_hits = []   # resetear antes de redibujar
        self._draw_grid()
        self._draw_edges()
        self._draw_nodes()

        if not self.nodes:
            W = cv.winfo_width()  or 700
            H = cv.winfo_height() or 450
            cv.create_text(W // 2, H // 2 - 16,
                           text="Elige  ⬡ Nodo  en la barra y haz clic aquí para empezar",
                           fill=C["dim"], font=("Segoe UI", 14))
            cv.create_text(W // 2, H // 2 + 16,
                           text="[N] nodo   [E] enlace   [S] seleccionar   rueda = zoom   botón central = pan",
                           fill="#2d2d50", font=("Segoe UI", 10))

    def _draw_grid(self):
        cv = self.cv
        W = cv.winfo_width()  or 1000
        H = cv.winfo_height() or 700
        step = max(8.0, 40.0 * self.zoom)
        # Saltar si hay demasiadas líneas
        if step < 8:
            step *= 5
        ox = self.pan_x % step
        oy = self.pan_y % step
        x = ox
        while x <= W:
            cv.create_line(x, 0, x, H, fill=C["grid"])
            x += step
        y = oy
        while y <= H:
            cv.create_line(0, y, W, y, fill=C["grid"])
            y += step

    def _draw_edges(self):
        groups: dict = defaultdict(list)
        for i, e in enumerate(self.edges):
            groups[(e["source"], e["target"])].append((i, e))

        for (sid, tid), lst in groups.items():
            sn = self.nodes.get(sid)
            tn = self.nodes.get(tid)
            if not sn or not tn:
                continue
            has_reverse = (tid, sid) in groups
            base_off = 22 * self.zoom if has_reverse else 0
            total = len(lst)
            for pos, (i, e) in enumerate(lst):
                sel = self.sel == ("edge", i)
                col = C["amber"] if sel else C["edge_col"]
                self._draw_single_edge(sn, tn, pos, total, col, i, e, base_off)

    def _draw_single_edge(self, sn, tn, pos, total, col, idx, e, base_off=0.0):
        cv  = self.cv
        rc  = self._rc()
        x1, y1 = self._w2c(sn["x"], sn["y"])
        x2, y2 = self._w2c(tn["x"], tn["y"])

        # ── Auto-bucle ────────────────────────────────────────────────
        if abs(sn["x"] - tn["x"]) < 1 and abs(sn["y"] - tn["y"]) < 1:
            r2 = rc + (22 + pos * 14) * self.zoom
            cv.create_oval(x1, y1 - r2 * 2, x1 + r2 * 2, y1, outline=col, width=2)
            cv.create_text(x1 + r2 + 6, y1 - r2,
                           text=bw_fmt(e["bandwidth"]),
                           fill=C["dim"], font=("Segoe UI", self._fs(8)))
            return

        # ── Curva Bézier ─────────────────────────────────────────────
        offset = (pos - (total - 1) / 2) * 30 * self.zoom + base_off
        dx, dy = x2 - x1, y2 - y1
        ln = math.hypot(dx, dy) or 1
        nx, ny = -dy / ln * offset, dx / ln * offset
        cpx, cpy = (x1 + x2) / 2 + nx, (y1 + y2) / 2 + ny

        ps = border_pt(x1, y1, cpx, cpy, rc + 2)
        pe = border_pt(x2, y2, cpx, cpy, rc + 2)
        pts = bezier_pts(ps, (cpx, cpy), pe)

        lw = max(1, int(2 * self.zoom))
        cv.create_line(*pts, fill="#000000", width=lw + 2, smooth=True)
        cv.create_line(*pts, fill=col,       width=lw,     smooth=True)

        # ── Flecha ───────────────────────────────────────────────────
        if len(pts) >= 6:
            ax, ay = pts[-2], pts[-1]
            bx, by = pts[-4], pts[-3]
            ang = math.atan2(ay - by, ax - bx)
            al  = max(8, int(13 * self.zoom))
            for da in (+2.5, -2.5):
                cv.create_line(ax, ay,
                               ax - al * math.cos(ang + da),
                               ay - al * math.sin(ang + da),
                               fill=col, width=lw)

        # ── Etiqueta BW en punto medio ───────────────────────────────
        t = 0.5; s = 1 - t
        lx = s*s*ps[0] + 2*s*t*cpx + t*t*pe[0] + nx * 0.3
        ly = s*s*ps[1] + 2*s*t*cpy + t*t*pe[1] + ny * 0.3
        fs = self._fs(8)
        label = bw_fmt(e["bandwidth"])
        cv.create_text(lx + 1, ly + 1, text=label, fill="#000",    font=("Segoe UI", fs))
        cv.create_text(lx,     ly,     text=label, fill=C["dim"],  font=("Segoe UI", fs))

        # ── Número de puerto junto al borde del nodo origen ──────────
        ddx, ddy = cpx - x1, cpy - y1
        dln  = math.hypot(ddx, ddy) or 1
        pdist = rc + max(8, int(11 * self.zoom))
        plx  = x1 + ddx / dln * pdist
        ply  = y1 + ddy / dln * pdist

        port_str = str(e["port"])
        fs_p = self._fs(8)
        # Sombra
        cv.create_text(plx + 1, ply + 1, text=port_str,
                       fill="#000", font=("Segoe UI", fs_p, "bold"))
        # Texto visible
        cv.create_text(plx, ply, text=port_str,
                       fill=C["port_lbl"],
                       font=("Segoe UI", fs_p, "bold"))

        # Guardar posición para detección posterior en _on_dbl
        # (los bindings de canvas no sobreviven al _redraw del primer clic)
        self._port_hits.append((plx, ply, idx))

    def _draw_nodes(self):
        cv = self.cv
        rc = self._rc()
        for nid, n in self.nodes.items():
            x, y = self._w2c(n["x"], n["y"])
            sel = self.sel == ("node", nid)
            hov = self.hov_id == nid

            if sel:
                cv.create_oval(x-rc-7, y-rc-7, x+rc+7, y+rc+7,
                               fill="", outline=C["amber"], width=1, dash=(4, 3))
            elif hov:
                cv.create_oval(x-rc-5, y-rc-5, x+rc+5, y+rc+5,
                               fill="", outline=C["accent_h"], width=1)

            cv.create_oval(x-rc+3, y-rc+3, x+rc+3, y+rc+3,
                           fill="#000000", outline="")

            fill = "#2d2000" if sel else C["node_fill"]
            stk  = C["amber"] if sel else (C["accent_h"] if hov else C["node_stk"])
            cv.create_oval(x-rc, y-rc, x+rc, y+rc,
                           fill=fill, outline=stk, width=2)

            if self.edge_src == nid:
                cv.create_oval(x-rc-8, y-rc-8, x+rc+8, y+rc+8,
                               fill="", outline=C["green"], width=2, dash=(6, 3))

            fs  = self._fs(10)
            fss = self._fs(7)
            cv.create_text(x, y - max(3, int(5 * self.zoom)),
                           text=n["label"],
                           fill=C["txt"], font=("Segoe UI", fs, "bold"))
            if fss >= 6:
                cv.create_text(x, y + max(4, int(8 * self.zoom)),
                               text=f"q:{n['queueSizes']}",
                               fill=C["dim"], font=("Segoe UI", fss))

    # ════════════════════════════════════════════════════════════════════
    #  ACCIONES
    # ════════════════════════════════════════════════════════════════════
    def set_mode(self, mode: str):
        self.mode = mode
        self.edge_src = None
        labels = {"select": "● SELECCIONAR", "add_node": "● AÑADIR NODO",
                  "add_edge": "● AÑADIR ENLACE", "pan": "● MOVER"}
        self._mode_var.set(labels.get(mode, "●"))
        try:
            self.cv.configure(cursor=self._canvas_cursor())
        except Exception:
            pass
        self.status(f"Modo: {labels.get(mode,'').replace('● ', '')}")
        self._redraw()

    def delete_sel(self):
        focused = self.root.focus_get()
        if isinstance(focused, tk.Entry):
            return
        if self.sel is None:
            return
        kind, ref = self.sel
        if kind == "node":
            self.edges = [e for e in self.edges
                          if e["source"] != ref and e["target"] != ref]
            del self.nodes[ref]
        else:
            self.edges.pop(ref)
        self.sel = None
        self._empty_props()
        self._redraw(); self._gml_preview()
        self.status("Elemento eliminado")

    def clear_all(self):
        if messagebox.askyesno("Limpiar todo", "¿Deseas borrar todo el grafo?"):
            self.nodes.clear(); self.edges.clear()
            self.nxt_id = 0; self.sel = None
            self._empty_props(); self._redraw(); self._gml_preview()
            self.status("Grafo limpiado")

    # ════════════════════════════════════════════════════════════════════
    #  GML
    # ════════════════════════════════════════════════════════════════════
    def _build_gml(self) -> str:
        L = ["graph [", "  directed 1", "  multigraph 1"]
        for n in self.nodes.values():
            L += ["  node [",
                  f'    id {n["id"]}',
                  f'    label "{n["label"]}"',
                  f'    queueSizes "{n["queueSizes"]}"',
                  "  ]"]
        sorted_edges = sorted(self.edges, key=lambda e: (e["source"], e["target"]))
        for e in sorted_edges:
            L += ["  edge [",
                  f'    source {e["source"]}',
                  f'    target {e["target"]}',
                  f'    key {e["key"]}',
                  f'    port {e["port"]}',
                  f'    weight {e["weight"]}',
                  f'    bandwidth "{e["bandwidth"]}"',
                  "  ]"]
        L.append("]\n")
        return "\n".join(L)

    def _gml_preview(self):
        gml = self._build_gml()
        self._gt.configure(state="normal")
        self._gt.delete("1.0", "end")
        self._gt.insert("1.0", gml)
        self._gt.configure(state="disabled")

    def export_gml(self):
        gml = self._build_gml()
        path = filedialog.asksaveasfilename(
            defaultextension=".txt",
            filetypes=[("Text files", "*.txt"), ("GML files", "*.gml"), ("All files", "*.*")],
            title="Exportar topología GML")
        if not path:
            return
        with open(path, "w", encoding="utf-8", newline="\n") as f:
            f.write(gml)
        self.status(f"Exportado → {path}")
        messagebox.showinfo("✓ Exportado", f"Archivo guardado en:\n{path}")

    def import_gml(self):
        path = filedialog.askopenfilename(
            filetypes=[("GML files", "*.gml"), ("All files", "*.*")],
            title="Importar topología GML")
        if not path:
            return
        try:
            self._parse_gml(path)
            self._redraw(); self._gml_preview()
            self.status(f"Importado: {path}")
        except Exception as ex:
            messagebox.showerror("Error al importar", str(ex))

    def _parse_gml(self, path: str):
        txt = open(path, encoding="utf-8", errors="replace").read()
        self.nodes.clear(); self.edges.clear()
        W = max(200, (self.cv.winfo_width()  or 800) - 100)
        H = max(200, (self.cv.winfo_height() or 600) - 100)

        for blk in re.findall(r'node\s*\[([^\]]*)\]', txt):
            m = re.search(r'\bid\s+(\d+)', blk)
            if not m:
                continue
            nid = int(m.group(1))
            lbl = re.search(r'label\s+"([^"]*)"', blk)
            qs  = re.search(r'queueSizes\s+"([^"]*)"', blk)
            self.nodes[nid] = {
                "id": nid,
                "label":      lbl.group(1) if lbl else str(nid),
                "queueSizes": qs.group(1)  if qs  else "32",
                "x": random.randint(80, W),
                "y": random.randint(80, H),
            }
            self.nxt_id = max(self.nxt_id, nid + 1)

        # Layout circular
        nids = list(self.nodes.keys())
        cx, cy = W // 2, H // 2
        radius = min(W, H) // 3
        for i, nid in enumerate(nids):
            angle = 2 * math.pi * i / max(len(nids), 1)
            self.nodes[nid]["x"] = cx + radius * math.cos(angle)
            self.nodes[nid]["y"] = cy + radius * math.sin(angle)

        def gi(pat, blk, d=0):
            m = re.search(pat, blk); return int(m.group(1)) if m else d

        def gs(pat, blk, d=""):
            m = re.search(pat, blk); return m.group(1) if m else d

        for blk in re.findall(r'edge\s*\[([^\]]*)\]', txt):
            self.edges.append({
                "source":    gi(r'source\s+(\d+)',          blk),
                "target":    gi(r'target\s+(\d+)',          blk),
                "key":       gi(r'\bkey\s+(\d+)',           blk),
                "port":      gi(r'port\s+(\d+)',            blk),
                "weight":    gi(r'weight\s+(\d+)',          blk, 1),
                "bandwidth": gs(r'bandwidth\s+"([^"]*)"',   blk, "1000000"),
            })
        self.sel = None; self._empty_props()

    # ── Status ───────────────────────────────────────────────────────────
    def status(self, msg: str):
        n = len(self.nodes); e = len(self.edges)
        zp = f"zoom {self.zoom:.1f}×"
        self._sv.set(f"{msg}   ·   {n} nodo{'s' if n!=1 else ''}  /  {e} enlace{'s' if e!=1 else ''}   ·   {zp}")


# ════════════════════════════════════════════════════════════════════════════
#  Entry point
# ════════════════════════════════════════════════════════════════════════════
def main():
    root = tk.Tk()
    try:
        root.tk.call("tk", "scaling", 1.25)
    except Exception:
        pass
    NetworkGraphApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()