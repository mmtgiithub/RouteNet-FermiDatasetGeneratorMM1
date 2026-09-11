#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
GENERATE_DATASET_SAMPLES_GUI.py
Interfaz gráfica para generar muestras RouteNet-Fermi.
Mismo tema oscuro que network_graph_editor.py.
Llama a los scripts externos hermanos para producir los TXT.
Compatible con Python 3.8+.
"""
from __future__ import annotations
import contextlib
import importlib.util
import io
import itertools
import math
import os
import queue
import re
import random
import shutil
import sys
import tarfile
import tempfile
import threading
import time
import tkinter as tk
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import Any, Dict, List, Mapping, Optional, Sequence, Set, Tuple
import networkx as nx
# ─── PALETA ──────────────────────────────────────────────────────────────────
C = {
    "bg":       "#0d0d1a",
    "bg_panel": "#13132a",
    "bg_tb":    "#181827",
    "entry_bg": "#1e1e40",
    "border":   "#2d2d5a",
    "accent":   "#7c3aed",
    "accent_h": "#6d28d9",
    "green":    "#10b981",
    "amber":    "#f59e0b",
    "red":      "#ef4444",
    "fg":       "#e2e8f0",
    "fg_dim":   "#64748b",
    "sel_bg":   "#312e81",
}
F  = ("Segoe UI", 10)
FB = ("Segoe UI", 10, "bold")
FM = ("Consolas",  9)
FH = ("Segoe UI", 11, "bold")
FS = ("Segoe UI",  9)
# ─── CONSTANTES DEL BACKEND ──────────────────────────────────────────────────
EXTERNAL_GENERATOR_NAMES: Dict[str, str] = {
    "input_files":        "GENERATE_INPUT_FILES.py",
    "traffic":            "NODES_2_GENERATE_TRAFFIC.py",
    "link_usage":         "NODES_2_GENERATE_LINK_USAGE.py",
    "simulation_results": "NODES_2_GENERATE_SIMULATIONRESULTS.py",
    "stability":          "generate_stability.py",
}
GENERATED_FILENAMES = (
    "input_files.txt",
    "traffic.txt",
    "linkUsage.txt",
    "simulationResults.txt",
    "stability.txt",
)
RHO_DELAY_CAP  = 0.99
RHO_OCC_CAP    = 0.99
EULER_GAMMA    = 0.5772156649015329
PERCENTILES    = (0.10, 0.20, 0.50, 0.80, 0.90)
Pair    = Tuple[int, int]
QueueId = Tuple[int, int, int]
# ─── DATA CLASSES ─────────────────────────────────────────────────────────────
@dataclass
class Flow:
    src: int
    dst: int
    packet_rate: float
    bit_rate: float
    avg_packet_size: float
    path: List[int]       = field(default_factory=list)
    queues: List[QueueId] = field(default_factory=list)
@dataclass
class QueueLoad:
    u: int
    v: int
    port: int
    bandwidth: float
    packet_rate: float = 0.0
    bit_rate: float    = 0.0
    flow_count: int    = 0
    @property
    def avg_packet_size(self) -> float:
        return self.bit_rate / self.packet_rate if self.packet_rate > 0 else 0.0
    @property
    def service_rate(self) -> float:
        a = self.avg_packet_size
        return self.bandwidth / a if a > 0 else math.inf
    @property
    def rho(self) -> float:
        mu = self.service_rate
        return self.packet_rate / mu if math.isfinite(mu) and mu > 0 else 0.0
    @property
    def loss_fraction(self) -> float:
        lam, mu = self.packet_rate, self.service_rate
        if lam <= 0 or not math.isfinite(mu):
            return 0.0
        if mu <= 0:
            return 1.0
        return min(1.0, max(0.0, 1.0 - mu / lam)) if lam >= mu else 0.0
    @property
    def mean_delay(self) -> float:
        mu, lam = self.service_rate, self.packet_rate
        if not math.isfinite(mu):
            return 0.0
        if lam >= mu:
            lam = RHO_DELAY_CAP * mu
        return 1.0 / (mu - lam)
# ─── FUNCIONES BACKEND ───────────────────────────────────────────────────────
def fmt(v: float, d: int = 9) -> str:
    t = ("{0:." + str(d) + "f}").format(v).rstrip("0").rstrip(".")
    return t or "0"
def parse_pairs(text: str) -> List[Pair]:
    pairs: List[Pair] = []
    seen:  Set[Pair]  = set()
    for raw in text.split(","):
        item = raw.strip()
        if not item:
            continue
        parts = item.split(":")
        if len(parts) != 2:
            raise ValueError(f"Par inválido '{item}'. Usa src:dst.")
        try:
            s, d = int(parts[0]), int(parts[1])
        except ValueError:
            raise ValueError(f"Par no entero '{item}'.")
        if s == d:
            raise ValueError(f"Origen y destino iguales: {s}:{d}.")
        p = (s, d)
        if p in seen:
            raise ValueError(f"Par repetido {s}:{d}.")
        seen.add(p)
        pairs.append(p)
    if not pairs:
        raise ValueError("Debe haber al menos un par OD.")
    return pairs
def parse_lambda_axis(spec: str) -> List[float]:
    """
    Formatos aceptados:
      valor            -> un unico valor fijo
      inicio:fin       -> rango con step 1 (o -1)
      inicio:fin:paso  -> rango con step explicito
      inicio:fin:rN    -> N valores aleatorios uniformes en [inicio, fin]
    """
    spec = spec.strip()
    if not spec:
        raise ValueError("Especificacion de lambda vacia.")
    pieces = [p.strip() for p in spec.split(":")]
    if len(pieces) not in {1, 2, 3}:
        raise ValueError(
            f"Especificacion invalida '{spec}'. "
            "Usa: valor | inicio:fin | inicio:fin:paso | inicio:fin:rN."
        )
    # ── modo aleatorio: inicio:fin:rN ─────────────────────────────────────────
    if len(pieces) == 3 and pieces[2].lower().startswith("r"):
        count_str = pieces[2][1:]
        try:
            count = int(count_str)
        except ValueError:
            raise ValueError(
                f"Numero de muestras invalido en '{spec}'. Usa rN, p.ej. r10."
            )
        if count < 1:
            raise ValueError("El numero de muestras aleatorias debe ser >= 1.")
        try:
            lo = float(Decimal(pieces[0]))
            hi = float(Decimal(pieces[1]))
        except InvalidOperation:
            raise ValueError(f"Valor no numerico en '{spec}'.")
        if lo <= 0 or hi <= 0:
            raise ValueError("Los extremos de lambda deben ser > 0.")
        if lo >= hi:
            raise ValueError(
                f"Para modo aleatorio el inicio ({lo}) debe ser "
                f"menor que el fin ({hi})."
            )
        return [random.uniform(lo, hi) for _ in range(count)]
    # ── modo determinista (codigo existente) ──────────────────────────────────
    try:
        nums = [Decimal(p) for p in pieces]
    except InvalidOperation:
        raise ValueError(f"Valor no numerico en '{spec}'.")
    if len(nums) == 1:
        v = nums[0]
        if v <= 0:
            raise ValueError("Lambda debe ser > 0.")
        return [float(v)]
    start, end = nums[0], nums[1]
    if start <= 0 or end <= 0:
        raise ValueError("Los extremos de lambda deben ser > 0.")
    step = (
        nums[2] if len(nums) == 3
        else (Decimal("1") if start <= end else Decimal("-1"))
    )
    if step == 0:
        raise ValueError("El step no puede ser cero.")
    vals: List[float] = []
    cur = start
    if step > 0:
        while cur <= end:
            vals.append(float(cur))
            cur += step
    else:
        while cur >= end:
            vals.append(float(cur))
            cur += step
    if not vals:
        raise ValueError(f"El rango '{spec}' no contiene valores.")
    return vals
def parse_lambda_ranges(text: str, n: int) -> List[List[float]]:
    """
    text tiene el formato: "spec1,spec2,...,specN"
    donde cada spec puede ser "valor", "inicio:fin" o "inicio:fin:step".
    La coma que separa specs NO es la misma que la que hay dentro de ellas
    (no la hay), así que basta con split(",") una vez para obtener las specs.
    """
    specs = [s.strip() for s in text.split(",") if s.strip()]
    if len(specs) != n:
        raise ValueError(
            f"Se esperaban {n} especificación(es), se recibieron {len(specs)}."
        )
    return [parse_lambda_axis(s) for s in specs]
def count_combinations(axes: Sequence[Sequence[float]]) -> int:
    r = 1
    for a in axes:
        r *= len(a)
    return r
def list_candidate_files(directory: Path, suffixes: Set[str]) -> List[Path]:
    if not directory.is_dir():
        raise FileNotFoundError(f"No existe el directorio '{directory}'.")
    return sorted(
        p for p in directory.iterdir()
        if p.is_file() and p.suffix.lower() in suffixes
    )
def iter_edges(graph: nx.Graph):
    if graph.is_multigraph():
        return (
            (int(u), int(v), d)
            for u, v, _k, d in graph.edges(keys=True, data=True)
        )
    return ((int(u), int(v), d) for u, v, d in graph.edges(data=True))
def edge_records(graph: nx.Graph, u: int, v: int) -> List[Dict[str, Any]]:
    if not graph.has_edge(u, v):
        return []
    if graph.is_multigraph():
        return list(graph.get_edge_data(u, v, default={}).values())
    d = graph.get_edge_data(u, v)
    return [d] if d is not None else []
def build_port_map(
    graph: nx.Graph,
) -> Dict[int, Dict[int, Tuple[int, Dict[str, Any]]]]:
    pm: Dict[int, Dict[int, Tuple[int, Dict[str, Any]]]] = {}
    for u, v, data in iter_edges(graph):
        if "port" not in data:
            raise ValueError(f"Arista {u}->{v} sin atributo 'port'.")
        if "bandwidth" not in data:
            raise ValueError(f"Arista {u}->{v} sin atributo 'bandwidth'.")
        port = int(data["port"])
        bw   = float(data["bandwidth"])
        if bw <= 0:
            raise ValueError(f"Bandwidth no positivo en {u}->{v}.")
        sp = pm.setdefault(u, {})
        if port in sp:
            raise ValueError(f"Puerto {port} duplicado en nodo {u}.")
        sp[port] = (v, data)
    return pm
def read_routing(path: Path, n: int) -> List[List[int]]:
    routing: List[List[int]] = []
    with path.open("r", encoding="utf-8-sig") as f:
        for ln, raw in enumerate(f, 1):
            line = raw.strip()
            if not line:
                continue
            try:
                routing.append(
                    [int(v.strip()) for v in line.split(",") if v.strip()]
                )
            except ValueError:
                raise ValueError(
                    f"Valor no entero en '{path}', línea {ln}."
                )
    if len(routing) != n:
        raise ValueError(
            f"'{path.name}' tiene {len(routing)} filas; se esperaban {n}."
        )
    return routing
def load_and_validate(graph_path: Path, routing_path: Path):
    try:
        graph = nx.read_gml(str(graph_path), label=None)
    except Exception as e:
        raise ValueError(f"No se pudo leer '{graph_path.name}': {e}")
    n = graph.number_of_nodes()
    if n < 2:
        raise ValueError("El grafo necesita al menos 2 nodos.")
    actual = sorted(int(node) for node in graph.nodes())
    if actual != list(range(n)):
        raise ValueError(f"Los nodos deben ser 0..{n - 1}. Encontrados: {actual}.")
    routing = read_routing(routing_path, n)
    pm = build_port_map(graph)
    return graph, n, routing, pm
def reconstruct_route(
    src: int, dst: int, n: int, routing: Sequence, pm: Mapping
) -> Tuple[List[int], List[QueueId]]:
    if src == dst:
        raise ValueError("Origen y destino coinciden.")
    if not (0 <= src < n and 0 <= dst < n):
        raise ValueError(
            f"El par {src}->{dst} queda fuera del rango 0..{n - 1}."
        )
    cur = src
    visited = {cur}
    path: List[int]      = [cur]
    queues: List[QueueId] = []
    for _ in range(n):
        port = routing[cur][dst]
        if port == -1:
            raise ValueError(
                f"La ruta {src}->{dst} termina sin llegar al destino (nodo {cur})."
            )
        info = pm.get(cur, {}).get(port)
        if info is None:
            raise ValueError(
                f"Puerto {port} del nodo {cur} no existe en el grafo."
            )
        nxt, _ = info
        if nxt in visited:
            raise ValueError(f"Bucle detectado en la ruta {src}->{dst}.")
        queues.append((cur, nxt, port))
        path.append(nxt)
        if nxt == dst:
            return path, queues
        visited.add(nxt)
        cur = nxt
    raise ValueError(f"No se completó la ruta {src}->{dst}.")
def edge_data_for_queue(
    graph: nx.Graph, qid: QueueId
) -> Dict[str, Any]:
    u, v, port = qid
    candidates = [
        d for d in edge_records(graph, u, v)
        if int(d.get("port", -1)) == port
    ]
    if len(candidates) != 1:
        raise ValueError(
            f"Se esperaba 1 arista {u}->{v} port {port}, "
            f"encontradas {len(candidates)}."
        )
    return candidates[0]
def build_flows(
    pairs: Sequence[Pair],
    rates: Sequence[float],
    avg_pkt: float,
    n: int,
    routing: Sequence,
    pm: Mapping,
) -> List[Flow]:
    flows: List[Flow] = []
    for (s, d), r in zip(pairs, rates):
        path, queues = reconstruct_route(s, d, n, routing, pm)
        flows.append(
            Flow(
                src=s, dst=d,
                packet_rate=r,
                bit_rate=r * avg_pkt,
                avg_packet_size=avg_pkt,
                path=path,
                queues=queues,
            )
        )
    return flows
def build_queue_loads(
    graph: nx.Graph, flows: Sequence[Flow]
) -> Dict[QueueId, QueueLoad]:
    loads: Dict[QueueId, QueueLoad] = {}
    for flow in flows:
        for qid in flow.queues:
            data = edge_data_for_queue(graph, qid)
            bw   = float(data["bandwidth"])
            if qid not in loads:
                loads[qid] = QueueLoad(
                    u=qid[0], v=qid[1], port=qid[2], bandwidth=bw
                )
            loads[qid].packet_rate += flow.packet_rate
            loads[qid].bit_rate    += flow.bit_rate
            loads[qid].flow_count  += 1
    return loads
def next_result_index(output_dir: Path) -> int:
    output_dir.mkdir(parents=True, exist_ok=True)
    used: Set[int] = set()
    pat = re.compile(r"^results_(\d+)(?:\.tar\.gz)?$")
    for c in output_dir.iterdir():
        m = pat.match(c.name)
        if m:
            used.add(int(m.group(1)))
    return max(used) + 1 if used else 0
def create_result_package(
    output_dir: Path, idx: int, contents: Mapping[str, str]
) -> Tuple[Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    name = f"results_{idx}"
    rdir = output_dir / name
    arch = output_dir / f"{name}.tar.gz"
    if rdir.exists():
        raise FileExistsError(f"Ya existe la carpeta '{rdir}'.")
    if arch.exists():
        raise FileExistsError(f"Ya existe el archivo '{arch}'.")
    rdir.mkdir()
    try:
        for fname, content in contents.items():
            # write_bytes evita que Windows convierta \n → \r\n
            (rdir / fname).write_bytes(content.encode("utf-8"))
        with tarfile.open(str(arch), "w:gz") as tf:
            tf.add(str(rdir), arcname=name, recursive=True)
    except Exception:
        # limpiar ambos artefactos para no dejar carpetas huérfanas
        if arch.exists():
            arch.unlink()
        if rdir.exists():
            shutil.rmtree(str(rdir), ignore_errors=True)
        raise
    return rdir, arch
def simulation_delays_from_content(
    content: str, n: int, pairs: Sequence[Pair]
) -> Tuple[float, List[float]]:
    line = content.strip()
    header, payload = line.split("|", 1)
    global_delay = float(header.split(",")[2])
    blocks = payload.split(";")
    while blocks and not blocks[-1]:
        blocks.pop()
    flow_delays: List[float] = []
    for s, d in pairs:
        pos = s * n + d
        flow_delays.append(float(blocks[pos].split(",")[3]))
    return global_delay, flow_delays
def copy_dataset_resource(source: Path, dest_dir: Path) -> Path:
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / source.name
    if source.resolve() != dest.resolve():
        shutil.copy2(str(source), str(dest))
    return dest
def prepare_partition_resources(
    output_dir: Path, graph_path: Path, routing_path: Path
) -> Tuple[Path, Path]:
    gc = copy_dataset_resource(graph_path,   output_dir / "graphs")
    rc = copy_dataset_resource(routing_path, output_dir / "routings")
    return gc, rc
def locate_external_generator(canonical_name: str, script_dir: Path) -> Path:
    direct = script_dir / canonical_name
    if direct.is_file():
        return direct.resolve()
    stem = Path(canonical_name).stem.lower()
    candidates = sorted(
        p for p in script_dir.glob("*.py")
        if p.stem.lower() == stem
        or p.stem.lower().startswith(stem + "(")
    )
    if len(candidates) == 1:
        return candidates[0].resolve()
    if not candidates:
        raise FileNotFoundError(
            f"No se encontró '{canonical_name}' en '{script_dir}'."
        )
    raise ValueError(
        f"Varias copias de '{canonical_name}': "
        f"{[p.name for p in candidates]}. Conserva solo una."
    )
def resolve_external_generators(script_dir: Path) -> Dict[str, Path]:
    return {
        key: locate_external_generator(name, script_dir)
        for key, name in EXTERNAL_GENERATOR_NAMES.items()
    }
def normalize_lf(path: Path) -> None:
    """Convierte CRLF → LF en binario para que datanetAPI no lea \\r en las rutas."""
    try:
        data = path.read_bytes()
        fixed = data.replace(b"\r\n", b"\n").replace(b"\r", b"\n")
        if fixed != data:
            path.write_bytes(fixed)
    except Exception:
        pass  # si falla no interrumpimos la generación
def load_external_module(key: str, script_path: Path) -> Any:
    mname = f"_routenet_gui_{key}_{abs(hash(str(script_path)))}"
    # Eliminar módulo previo para garantizar estado limpio en cada ejecución
    sys.modules.pop(mname, None)
    spec = importlib.util.spec_from_file_location(mname, str(script_path))
    if spec is None or spec.loader is None:
        raise ImportError(f"No se pudo cargar '{script_path}'.")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[mname] = mod
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    if not hasattr(mod, "main"):
        raise AttributeError(f"'{script_path.name}' no contiene main().")
    return mod
def execute_external_main(
    module: Any, arguments: Sequence[str], working_directory: Path
) -> str:
    prev_argv = list(sys.argv)
    prev_dir  = Path.cwd()
    out_buf   = io.StringIO()
    err_buf   = io.StringIO()
    try:
        sys.argv = list(arguments)
        os.chdir(str(working_directory))
        with contextlib.redirect_stdout(out_buf):
            with contextlib.redirect_stderr(err_buf):
                try:
                    module.main()
                except SystemExit as e:
                    code = e.code
                    if code not in (None, 0):
                        raise RuntimeError(
                            f"El generador terminó con código {code}."
                        )
    except Exception as e:
        out, err = out_buf.getvalue(), err_buf.getvalue()
        details = "\n".join(p.strip() for p in (out, err) if p.strip())
        if details:
            raise RuntimeError(f"{e}\nSalida:\n{details}") from e
        raise
    finally:
        sys.argv = prev_argv
        os.chdir(str(prev_dir))
    return out_buf.getvalue() + err_buf.getvalue()
def configure_external_modules(
    modules: Mapping[str, Any], workspace_root: Path
) -> None:
    infile = workspace_root / "input_files.txt"
    gdir   = workspace_root / "graphs"
    rdir   = workspace_root / "routings"
    tfile  = workspace_root / "traffic.txt"
    lufile = workspace_root / "linkUsage.txt"
    srfile = workspace_root / "simulationResults.txt"
    stfile = workspace_root / "stability.txt"
    m = modules
    m["input_files"].GRAPH_PATH   = str(gdir)
    m["input_files"].ROUTING_PATH = str(rdir)
    m["traffic"].INPUT_FILES  = infile
    m["traffic"].GRAPHS_DIR   = gdir
    m["traffic"].ROUTINGS_DIR = rdir
    m["traffic"].OUTPUT_FILE  = tfile
    m["link_usage"].INPUT_FILES  = infile
    m["link_usage"].GRAPHS_DIR   = gdir
    m["link_usage"].ROUTINGS_DIR = rdir
    m["link_usage"].TRAFFIC_FILE = tfile
    m["link_usage"].OUTPUT_FILE  = lufile
    m["simulation_results"].INPUT_FILES  = infile
    m["simulation_results"].GRAPHS_DIR   = gdir
    m["simulation_results"].ROUTINGS_DIR = rdir
    m["simulation_results"].TRAFFIC_FILE = tfile
    m["simulation_results"].OUTPUT_FILE  = srfile
    m["stability"].INPUT_FILES    = str(infile)
    m["stability"].STABILITY_FILE = str(stfile)
def prepare_external_workspace(
    workspace_root: Path,
    graph_path: Path,
    routing_path: Path,
    script_paths: Mapping[str, Path],
) -> Dict[str, Any]:
    gdir = workspace_root / "graphs"
    rdir = workspace_root / "routings"
    sdir = workspace_root / "scripts"
    for d in (gdir, rdir, sdir):
        d.mkdir(parents=True, exist_ok=True)
    shutil.copy2(str(graph_path),   str(gdir / graph_path.name))
    shutil.copy2(str(routing_path), str(rdir / routing_path.name))
    modules = {k: load_external_module(k, p) for k, p in script_paths.items()}
    configure_external_modules(modules, workspace_root)
    execute_external_main(
        modules["input_files"],
        [str(script_paths["input_files"])],
        sdir,
    )
    normalize_lf(workspace_root / "input_files.txt")
    return modules
def generate_one_sample_externally(
    modules: Mapping[str, Any],
    script_paths: Mapping[str, Path],
    workspace_root: Path,
    pairs: Sequence[Pair],
    rates: Sequence[float],
    pkt1: int,
    pkt2: int,
) -> Dict[str, str]:
    sdir      = workspace_root / "scripts"
    pairs_arg = ",".join(f"{s}:{d}" for s, d in pairs)
    rates_arg = ",".join(fmt(r) for r in rates)
    execute_external_main(
        modules["traffic"],
        [str(script_paths["traffic"]), str(pkt1), str(pkt2),
         "--pairs", pairs_arg, "--rates", rates_arg],
        sdir,
    )
    execute_external_main(
        modules["link_usage"],
        [str(script_paths["link_usage"])],
        sdir,
    )
    execute_external_main(
        modules["simulation_results"],
        [str(script_paths["simulation_results"])],
        sdir,
    )
    execute_external_main(
        modules["stability"],
        [str(script_paths["stability"])],
        sdir,
    )
    contents: Dict[str, str] = {}
    for fname in GENERATED_FILENAMES:
        p = workspace_root / fname
        if not p.is_file():
            raise FileNotFoundError(
                f"El generador no produjo '{fname}'."
            )
        normalize_lf(p)
        content = p.read_text(encoding="utf-8-sig")
        if not content:
            raise ValueError(f"'{fname}' está vacío.")
        lines = [ln for ln in content.splitlines() if ln.strip()]
        if len(lines) != 1:
            raise ValueError(
                f"'{fname}' debía tener 1 línea, tiene {len(lines)}."
            )
        contents[fname] = content
    return contents
def _fmt_eta(secs: float) -> str:
    """Formatea segundos restantes como '2h 04m 30s', '5m 12s' o '45s'."""
    s = max(0, int(secs))
    h, rem = divmod(s, 3600)
    m, ss  = divmod(rem, 60)
    if h:
        return f"{h}h {m:02d}m {ss:02d}s"
    if m:
        return f"{m}m {ss:02d}s"
    return f"{ss}s"
# ─── WIDGETS AUXILIARES ───────────────────────────────────────────────────────
class ToggleSwitch(tk.Canvas):
    """Interruptor ON/OFF estilo iOS vinculado a un BooleanVar."""
    TW, TH = 44, 24   # ancho y alto de la pastilla

    def __init__(self, parent, variable: tk.BooleanVar, **kw):
        bg = kw.pop("bg", C["bg_panel"])
        super().__init__(
            parent, width=self.TW, height=self.TH,
            bg=bg, highlightthickness=0, bd=0, cursor="hand2",
        )
        self._var = variable
        self._draw()
        self.bind("<Button-1>", self._toggle)
        variable.trace_add("write", lambda *_: self.after_idle(self._draw))

    def _draw(self):
        self.delete("all")
        on = self._var.get()
        tw, th = self.TW, self.TH
        pad = 3
        r   = th // 2          # radio de las semiesferas de la pastilla
        kr  = r - pad          # radio del knob

        track = C["accent"] if on else "#44445a"
        # pastilla: dos óvalos + rectángulo central
        self.create_oval(0, 0, th, th, fill=track, outline=track)
        self.create_oval(tw - th, 0, tw, th, fill=track, outline=track)
        self.create_rectangle(r, 0, tw - r, th, fill=track, outline=track)
        # knob
        kx = tw - r - pad if on else r + pad - 1
        ky = th // 2
        self.create_oval(kx - kr, ky - kr, kx + kr, ky + kr,
                         fill="white", outline="")

    def _toggle(self, _event=None):
        self._var.set(not self._var.get())

def _mk_btn(parent, text, command, bg, **kw):
    return tk.Button(
        parent, text=text, command=command,
        bg=bg, fg=C["fg"], font=FB,
        relief="flat", bd=0, cursor="hand2",
        activebackground=bg, activeforeground=C["fg"],
        **kw,
    )
def _mk_lbl(parent, text, fg=None, font=None, **kw):
    return tk.Label(
        parent, text=text,
        bg=C["bg_panel"],
        fg=fg or C["fg_dim"],
        font=font or FS,
        **kw,
    )
def _mk_sep(parent):
    tk.Frame(parent, bg=C["border"], height=1).pack(
        fill="x", padx=10, pady=6
    )
# ─── APLICACIÓN PRINCIPAL ─────────────────────────────────────────────────────
class DatasetGeneratorApp:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        root.title("Dataset Generator  —  RouteNet-Fermi")
        root.configure(bg=C["bg"])
        root.minsize(900, 640)
        root.geometry("1200x760")
        self._log_q:      queue.Queue = queue.Queue()
        self._stop_event: threading.Event = threading.Event()
        self._skip_var:   tk.BooleanVar  = tk.BooleanVar(value=False)
        self._running:    bool = False
        self._pairs: List[Pair] = []
        self._lambda_vars:    List[tk.StringVar] = []
        self._lambda_entries: List[tk.Entry]     = []
        # Modo de generación (cartesiano / aleatorio)
        self._mode_var:      tk.StringVar      = tk.StringVar(value="cartesian")
        self._nsamples_var:  tk.StringVar      = tk.StringVar(value="100")
        self._lambda_min_vars: List[tk.StringVar] = []
        self._lambda_max_vars: List[tk.StringVar] = []
        self._viz_selected_flow: Optional[int] = None   # flujo seleccionado en visualización
        self._viz_selected_node: Optional[int] = None   # nodo seleccionado en visualización
        self._build_ui()
        self._set_default_dirs()
    # ── CONSTRUCCIÓN DE LA UI ─────────────────────────────────────────────────
    def _build_ui(self) -> None:
        # barra de título
        tb = tk.Frame(self.root, bg=C["bg_tb"], height=44)
        tb.pack(fill="x", side="top")
        tk.Label(
            tb, text="⚙  Dataset Generator  —  RouteNet-Fermi",
            bg=C["bg_tb"], fg=C["fg"], font=FH, padx=14,
        ).pack(side="left", pady=8)
        # barra de estado (empaquetada antes del notebook para fijarse abajo)
        sb = tk.Frame(self.root, bg=C["bg_tb"], height=28)
        sb.pack(fill="x", side="bottom")
        self._status_var = tk.StringVar(value="Listo.")
        tk.Label(
            sb, textvariable=self._status_var,
            bg=C["bg_tb"], fg=C["fg_dim"],
            font=FS, anchor="w", padx=10,
        ).pack(side="left", fill="x", expand=True)
        self._progress_var = tk.StringVar(value="")
        tk.Label(
            sb, textvariable=self._progress_var,
            bg=C["bg_tb"], fg=C["accent"],
            font=FS, padx=10,
        ).pack(side="right")
        # ── estilar el Notebook para el tema oscuro ───────────────────────
        style = ttk.Style()
        try:
            style.theme_use("clam")
        except Exception:
            pass
        style.configure("Dark.TNotebook",
            background=C["bg_tb"], borderwidth=0, tabmargins=0)
        style.configure("Dark.TNotebook.Tab",
            background=C["bg_tb"], foreground=C["fg_dim"],
            font=FB, padding=[18, 6], borderwidth=0)
        style.map("Dark.TNotebook.Tab",
            background=[("selected", C["bg_panel"]),
                        ("active",   C["bg"])],
            foreground=[("selected", C["fg"]),
                        ("active",   C["fg"])])
        # ── Notebook principal ────────────────────────────────────────────
        nb = ttk.Notebook(self.root, style="Dark.TNotebook")
        nb.pack(fill="both", expand=True)
        # ── pestaña Generación ────────────────────────────────────────────
        gen_frame = tk.Frame(nb, bg=C["bg"])
        nb.add(gen_frame, text="  ⚙  Generación  ")
        pw = tk.PanedWindow(
            gen_frame, orient="horizontal",
            bg=C["border"], sashwidth=4, sashrelief="flat",
        )
        pw.pack(fill="both", expand=True)
        left_outer = tk.Frame(pw, bg=C["bg_panel"], width=400)
        pw.add(left_outer, minsize=340)
        self._build_left_panel(left_outer)
        right = tk.Frame(pw, bg=C["bg"])
        pw.add(right, minsize=380)
        self._build_right_panel(right)
        # ── pestaña Visualización ─────────────────────────────────────────
        viz_frame = tk.Frame(nb, bg=C["bg"])
        nb.add(viz_frame, text="  🔍  Visualización  ")
        self._build_viz_tab(viz_frame)
    # ── PANEL IZQUIERDO ───────────────────────────────────────────────────────
    def _build_left_panel(self, parent: tk.Frame) -> None:
        canvas = tk.Canvas(
            parent, bg=C["bg_panel"], highlightthickness=0, bd=0
        )
        vsb = tk.Scrollbar(parent, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=vsb.set)
        vsb.pack(side="right", fill="y")
        canvas.pack(side="left", fill="both", expand=True)
        inner = tk.Frame(canvas, bg=C["bg_panel"])
        win_id = canvas.create_window((0, 0), window=inner, anchor="nw")
        def _on_inner_configure(_e: Any) -> None:
            canvas.configure(scrollregion=canvas.bbox("all"))
        def _on_canvas_configure(e: Any) -> None:
            canvas.itemconfig(win_id, width=e.width)
        def _on_mousewheel(e: Any) -> None:
            canvas.yview_scroll(int(-1 * (e.delta / 120)), "units")
        inner.bind("<Configure>", _on_inner_configure)
        canvas.bind("<Configure>", _on_canvas_configure)
        # bind_all para que el scroll funcione sobre cualquier widget
        # del panel izquierdo. Los widgets con scroll propio (log,
        # listboxes) bloquean el evento con "break" en sus propios bind.
        canvas.bind_all("<MouseWheel>", _on_mousewheel)
        self._canvas_scroll = canvas  # guardamos ref para desbindear si fuera necesario
        p = inner  # alias corto
        # ── sección helper ───────────────────────────────────────────────────
        def section(label: str) -> None:
            tk.Label(
                p, text=label,
                bg=C["bg_panel"], fg=C["accent"],
                font=FB, anchor="w",
            ).pack(fill="x", padx=12, pady=(12, 2))
        def lbl(text: str) -> None:
            tk.Label(
                p, text=text,
                bg=C["bg_panel"], fg=C["fg_dim"],
                font=FS, anchor="w",
            ).pack(fill="x", padx=14, pady=(3, 0))
        def dir_row(var: tk.StringVar, label_text: str) -> None:
            lbl(label_text)
            row = tk.Frame(p, bg=C["bg_panel"])
            row.pack(fill="x", padx=12, pady=(1, 4))
            tk.Entry(
                row, textvariable=var,
                bg=C["entry_bg"], fg=C["fg"], font=F,
                insertbackground=C["fg"], relief="flat", bd=4,
            ).pack(side="left", fill="x", expand=True)
            def browse(v=var):
                d = filedialog.askdirectory(title=label_text)
                if d:
                    v.set(d)
            _mk_btn(row, "…", browse, C["accent"], padx=8).pack(
                side="left", padx=(4, 0)
            )
        # ── SCRIPTS ──────────────────────────────────────────────────────────
        section("SCRIPTS")
        self._scripts_var = tk.StringVar()
        dir_row(self._scripts_var, "Directorio de scripts generadores")
        _mk_sep(p)
        # ── ENTRADA ──────────────────────────────────────────────────────────
        section("ENTRADA")
        self._graphs_var = tk.StringVar()
        self._graphs_var.trace_add(
            "write", lambda *_: self.root.after(50, self._reload_graph_list)
        )
        dir_row(self._graphs_var, "Carpeta de grafos")
        lbl("Grafo")
        gf = tk.Frame(p, bg=C["bg_panel"])
        gf.pack(fill="x", padx=12, pady=(1, 6))
        gsb = tk.Scrollbar(gf)
        gsb.pack(side="right", fill="y")
        self._graph_lb = tk.Listbox(
            gf, yscrollcommand=gsb.set,
            bg=C["entry_bg"], fg=C["fg"], font=FM,
            selectbackground=C["sel_bg"], selectforeground=C["fg"],
            height=5, relief="flat", bd=2,
            exportselection=False,
        )
        self._graph_lb.pack(side="left", fill="x", expand=True)
        gsb.config(command=self._graph_lb.yview)
        self._graph_lb.bind("<MouseWheel>", lambda e: "break")
        self._graph_lb.bind(
            "<<ListboxSelect>>",
            lambda e: self.root.after(80, self._draw_graph),
        )
        self._routings_var = tk.StringVar()
        self._routings_var.trace_add(
            "write", lambda *_: self.root.after(50, self._reload_routing_list)
        )
        dir_row(self._routings_var, "Carpeta de routings")
        lbl("Routing")
        rf = tk.Frame(p, bg=C["bg_panel"])
        rf.pack(fill="x", padx=12, pady=(1, 6))
        rsb = tk.Scrollbar(rf)
        rsb.pack(side="right", fill="y")
        self._routing_lb = tk.Listbox(
            rf, yscrollcommand=rsb.set,
            bg=C["entry_bg"], fg=C["fg"], font=FM,
            selectbackground=C["sel_bg"], selectforeground=C["fg"],
            height=5, relief="flat", bd=2,
            exportselection=False,
        )
        self._routing_lb.pack(side="left", fill="x", expand=True)
        rsb.config(command=self._routing_lb.yview)
        self._routing_lb.bind("<MouseWheel>", lambda e: "break")
        self._routing_lb.bind(
            "<<ListboxSelect>>",
            lambda e: self.root.after(80, self._draw_graph),
        )
        _mk_sep(p)
        # ── SALIDA ───────────────────────────────────────────────────────────
        section("SALIDA")
        self._split_var = tk.StringVar(value="train")
        split_row = tk.Frame(p, bg=C["bg_panel"])
        split_row.pack(fill="x", padx=12, pady=(2, 4))
        for val, ltext in [
            ("train",      "train"),
            ("validation", "validation"),
            ("test",       "test"),
            ("custom",     "personalizado"),
        ]:
            tk.Radiobutton(
                split_row, text=ltext,
                variable=self._split_var, value=val,
                bg=C["bg_panel"], fg=C["fg"], font=F,
                selectcolor=C["entry_bg"],
                activebackground=C["bg_panel"],
                activeforeground=C["fg"],
                command=self._on_split_change,
            ).pack(side="left", padx=4)
        self._custom_out_var   = tk.StringVar()
        self._custom_out_frame = tk.Frame(p, bg=C["bg_panel"])
        # (se muestra/oculta según radio)
        coe = tk.Entry(
            self._custom_out_frame, textvariable=self._custom_out_var,
            bg=C["entry_bg"], fg=C["fg"], font=F,
            insertbackground=C["fg"], relief="flat", bd=4,
        )
        coe.pack(side="left", fill="x", expand=True)
        def browse_custom():
            d = filedialog.askdirectory(title="Directorio de salida")
            if d:
                self._custom_out_var.set(d)
        _mk_btn(
            self._custom_out_frame, "…", browse_custom, C["accent"], padx=8
        ).pack(side="left", padx=(4, 0))
        _mk_sep(p)
        # ── FLUJOS OD ────────────────────────────────────────────────────────
        section("FLUJOS OD")
        lbl("Pares  (ej: 0:5,2:8,6:1)")
        pairs_row = tk.Frame(p, bg=C["bg_panel"])
        pairs_row.pack(fill="x", padx=12, pady=(1, 4))
        self._pairs_var = tk.StringVar()
        self._pairs_entry = tk.Entry(
            pairs_row, textvariable=self._pairs_var,
            bg=C["entry_bg"], fg=C["fg"], font=F,
            insertbackground=C["fg"], relief="flat", bd=4,
        )
        self._pairs_entry.pack(side="left", fill="x", expand=True)
        _mk_btn(
            pairs_row, "✓ Parsear",
            self._parse_pairs_and_build_lambda_fields,
            C["green"], padx=10,
        ).pack(side="left", padx=(6, 0))
        # ── Modo de generación ────────────────────────────────────────────
        lbl("Modo de generación")
        mode_row = tk.Frame(p, bg=C["bg_panel"])
        mode_row.pack(fill="x", padx=12, pady=(2, 4))
        for _mv, _ml in [("cartesian", "Barrido cartesiano"),
                          ("random",   "Aleatorio total")]:
            tk.Radiobutton(
                mode_row, text=_ml,
                variable=self._mode_var, value=_mv,
                bg=C["bg_panel"], fg=C["fg"], font=F,
                selectcolor=C["entry_bg"],
                activebackground=C["bg_panel"],
                activeforeground=C["fg"],
                command=self._on_mode_change,
            ).pack(side="left", padx=4)
        # ── frame nº muestras (solo modo aleatorio, oculto por defecto) ──
        self._nsamples_frame = tk.Frame(p, bg=C["bg_panel"])
        ns_row = tk.Frame(self._nsamples_frame, bg=C["bg_panel"])
        ns_row.pack(fill="x", padx=12, pady=(0, 4))
        tk.Label(
            ns_row, text="Nº muestras:",
            bg=C["bg_panel"], fg=C["fg_dim"], font=FS,
        ).pack(side="left", padx=(0, 6))
        tk.Entry(
            ns_row, textvariable=self._nsamples_var,
            bg=C["entry_bg"], fg=C["fg"], font=F,
            insertbackground=C["fg"], relief="flat", bd=4,
            width=10,
        ).pack(side="left")
        # ── hint cartesiano (visible por defecto) ─────────────────────────
        self._cartesian_hint_frame = tk.Frame(p, bg=C["bg_panel"])
        tk.Label(
            self._cartesian_hint_frame,
            text="Lambdas por par  (ej: 100:900:50 | 200 | 50:800:r20)",
            bg=C["bg_panel"], fg=C["fg_dim"], font=FS, anchor="w",
        ).pack(fill="x")
        tk.Label(
            self._cartesian_hint_frame,
            text="  fijo: valor    rango: ini:fin:paso    aleatorio: ini:fin:rN",
            bg=C["bg_panel"], fg=C["fg_dim"],
            font=("Consolas", 8), anchor="w",
        ).pack(fill="x")
        self._cartesian_hint_frame.pack(fill="x", padx=14, pady=(3, 0))
        # ── hint aleatorio (oculto por defecto) ───────────────────────────
        self._random_hint_frame = tk.Frame(p, bg=C["bg_panel"])
        tk.Label(
            self._random_hint_frame,
            text="Rango λ por par  (mín y máx, distribución uniforme)",
            bg=C["bg_panel"], fg=C["fg_dim"], font=FS, anchor="w",
        ).pack(fill="x")
        # ── contenedor dinámico de campos lambda ──────────────────────────
        self._lambda_frame = tk.Frame(p, bg=C["bg_panel"])
        self._lambda_frame.pack(fill="x", padx=12, pady=(0, 4))
        _mk_sep(p)
        # ── PARÁMETROS ───────────────────────────────────────────────────────
        section("PARÁMETROS")
        pkt_row = tk.Frame(p, bg=C["bg_panel"])
        pkt_row.pack(fill="x", padx=12, pady=(2, 6))
        tk.Label(
            pkt_row, text="PKT1 (bits):",
            bg=C["bg_panel"], fg=C["fg_dim"], font=FS,
        ).pack(side="left")
        self._pkt1_var = tk.StringVar(value="300")
        tk.Spinbox(
            pkt_row, textvariable=self._pkt1_var,
            from_=1, to=999999, width=8,
            bg=C["entry_bg"], fg=C["fg"], font=F,
            buttonbackground=C["bg_tb"],
            insertbackground=C["fg"], relief="flat",
        ).pack(side="left", padx=(4, 18))
        tk.Label(
            pkt_row, text="PKT2 (bits):",
            bg=C["bg_panel"], fg=C["fg_dim"], font=FS,
        ).pack(side="left")
        self._pkt2_var = tk.StringVar(value="1700")
        tk.Spinbox(
            pkt_row, textvariable=self._pkt2_var,
            from_=1, to=999999, width=8,
            bg=C["entry_bg"], fg=C["fg"], font=F,
            buttonbackground=C["bg_tb"],
            insertbackground=C["fg"], relief="flat",
        ).pack(side="left", padx=(4, 0))
        _mk_sep(p)
        # ── ACCIONES ─────────────────────────────────────────────────────────
        section("ACCIONES")
        self._plan_var = tk.StringVar(value="")
        tk.Label(
            p, textvariable=self._plan_var,
            bg=C["bg_panel"], fg=C["amber"],
            font=FB, anchor="w",
        ).pack(fill="x", padx=14, pady=(0, 4))
        btn_row = tk.Frame(p, bg=C["bg_panel"])
        btn_row.pack(fill="x", padx=12, pady=(0, 16))
        self._btn_plan = _mk_btn(
            btn_row, "📋 Planificar",
            self._plan, C["bg_tb"], padx=12, pady=6,
        )
        self._btn_plan.pack(side="left", padx=(0, 6))
        self._btn_gen = _mk_btn(
            btn_row, "▶ Generar",
            self._generate, C["accent"], padx=12, pady=6,
        )
        self._btn_gen.pack(side="left", padx=(0, 6))
        self._btn_cancel = _mk_btn(
            btn_row, "■ Cancelar",
            self._cancel, C["red"], padx=12, pady=6,
        )
        self._btn_cancel.config(state="disabled")
        self._btn_cancel.pack(side="left", padx=(0, 6))
        # ── opción: no guardar ────────────────────────────────────────────────
        skip_row = tk.Frame(p, bg=C["bg_panel"])
        skip_row.pack(fill="x", padx=12, pady=(4, 8))
        ToggleSwitch(skip_row, variable=self._skip_var, bg=C["bg_panel"]).pack(side="left")
        tk.Label(
            skip_row, text="  Descartar muestras inestables (ρ ≥ 1)",
            bg=C["bg_panel"], fg=C["fg"], font=F,
        ).pack(side="left")
        # ── LIMPIEZA ─────────────────────────────────────────────────────────
        _mk_sep(p)
        section("LIMPIEZA")
        _mk_lbl(p, "Borra todos los results_* de la carpeta elegida.")
        clean_row = tk.Frame(p, bg=C["bg_panel"])
        clean_row.pack(fill="x", padx=12, pady=(4, 4))
        for split_name in ("train", "validation", "test"):
            _mk_btn(
                clean_row,
                f"🗑 {split_name}",
                lambda sn=split_name: self._clean_split(sn),
                C["red"],
                padx=10, pady=5,
            ).pack(side="left", padx=(0, 6))
        _mk_btn(
            clean_row,
            "🗑 personalizado",
            lambda: self._clean_split(None),
            C["red"],
            padx=10, pady=5,
        ).pack(side="left")
        # padding inferior
        tk.Frame(p, bg=C["bg_panel"], height=24).pack()
    # ── PANEL DERECHO (LOG) ───────────────────────────────────────────────────
    def _build_right_panel(self, parent: tk.Frame) -> None:
        hdr = tk.Frame(parent, bg=C["bg_tb"], height=36)
        hdr.pack(fill="x")
        tk.Label(
            hdr, text="LOG DE GENERACIÓN",
            bg=C["bg_tb"], fg=C["fg_dim"], font=FS, padx=12,
        ).pack(side="left", pady=8)
        _mk_btn(hdr, "Limpiar", self._clear_log, C["bg_tb"],
                padx=10, pady=4).pack(side="right", padx=8, pady=4)
        self._log = tk.Text(
            parent,
            bg=C["bg"], fg=C["fg"], font=FM,
            state="disabled", relief="flat", bd=0,
            wrap="char", pady=8, padx=10,
        )
        vsb = tk.Scrollbar(parent, command=self._log.yview)
        self._log.configure(yscrollcommand=vsb.set)
        vsb.pack(side="right", fill="y")
        self._log.pack(fill="both", expand=True)
        self._log.bind("<MouseWheel>", lambda e: "break")
        # etiquetas de color
        self._log.tag_config("ok",   foreground=C["green"])
        self._log.tag_config("err",  foreground=C["red"])
        self._log.tag_config("warn", foreground=C["amber"])
        self._log.tag_config("info", foreground=C["fg"])
        self._log.tag_config("dim",  foreground=C["fg_dim"])
        self._log.tag_config("head", foreground=C["accent"])
    # ── LÓGICA DE UI ─────────────────────────────────────────────────────────
    def _set_default_dirs(self) -> None:
        base = Path(
            r"C:/Users/Mario/Desktop/TFG GITHUB"
            r"/RouteNet-Fermi/CREACION_DE_DATOS/generador_mk2"
        )
        self._scripts_var.set(str(base / "NODES_2_MULTIFLUX"))
        self._graphs_var.set(str(base / "graphs"))
        self._routings_var.set(str(base / "routings"))
        self._reload_graph_list()
        self._reload_routing_list()
    def _reload_graph_list(self) -> None:
        d = Path(self._graphs_var.get())
        self._graph_lb.delete(0, "end")
        try:
            files = list_candidate_files(d, {".txt", ".gml"})
            for f in files:
                self._graph_lb.insert("end", f.name)
            if files:
                self._graph_lb.selection_set(0)
        except Exception:
            pass
    def _reload_routing_list(self) -> None:
        d = Path(self._routings_var.get())
        self._routing_lb.delete(0, "end")
        try:
            files = list_candidate_files(d, {".txt"})
            for f in files:
                self._routing_lb.insert("end", f.name)
            if files:
                self._routing_lb.selection_set(0)
        except Exception:
            pass
    def _on_split_change(self) -> None:
        if self._split_var.get() == "custom":
            self._custom_out_frame.pack(fill="x", padx=12, pady=(0, 4))
        else:
            self._custom_out_frame.pack_forget()

    def _on_mode_change(self) -> None:
        """Muestra u oculta los frames según el modo seleccionado."""
        if self._mode_var.get() == "random":
            self._nsamples_frame.pack(fill="x", after=None)  # will re-pack below
            self._cartesian_hint_frame.pack_forget()
            self._random_hint_frame.pack(fill="x", padx=14, pady=(3, 0))
        else:
            self._nsamples_frame.pack_forget()
            self._random_hint_frame.pack_forget()
            self._cartesian_hint_frame.pack(fill="x", padx=14, pady=(3, 0))
        # re-ordenar: nsamples_frame va antes que random_hint_frame
        if self._mode_var.get() == "random":
            self._nsamples_frame.pack(fill="x", padx=0, pady=(0, 0),
                                      before=self._random_hint_frame)
        # reconstruir campos lambda si ya hay pares
        if self._pairs:
            self._build_lambda_fields()
        self._plan_var.set("")

    def _parse_pairs_and_build_lambda_fields(self) -> None:
        try:
            pairs = parse_pairs(self._pairs_var.get())
        except ValueError as e:
            self._log_append(f"[!] {e}\n", "err")
            return
        self._pairs = pairs
        self._build_lambda_fields()
        self._log_append(
            f"[OK] {len(pairs)} par(es) parseados: "
            + ", ".join(f"{s}→{d}" for s, d in pairs) + "\n",
            "ok",
        )
        self._plan_var.set("")
        self.root.after(80, self._draw_graph)

    def _build_lambda_fields(self) -> None:
        """Construye los campos lambda en _lambda_frame según el modo actual."""
        for w in self._lambda_frame.winfo_children():
            w.destroy()
        self._lambda_vars     = []
        self._lambda_entries  = []
        self._lambda_min_vars = []
        self._lambda_max_vars = []
        if self._mode_var.get() == "cartesian":
            for s, d in self._pairs:
                row = tk.Frame(self._lambda_frame, bg=C["bg_panel"])
                row.pack(fill="x", pady=2)
                tk.Label(
                    row, text=f"λ {s}→{d}:",
                    bg=C["bg_panel"], fg=C["fg_dim"],
                    font=FS, width=10, anchor="e",
                ).pack(side="left")
                var = tk.StringVar()
                e = tk.Entry(
                    row, textvariable=var,
                    bg=C["entry_bg"], fg=C["fg"], font=FM,
                    insertbackground=C["fg"], relief="flat", bd=4,
                )
                e.pack(side="left", fill="x", expand=True, padx=(4, 0))
                self._lambda_vars.append(var)
                self._lambda_entries.append(e)
        else:  # random mode
            for s, d in self._pairs:
                row = tk.Frame(self._lambda_frame, bg=C["bg_panel"])
                row.pack(fill="x", pady=2)
                tk.Label(
                    row, text=f"λ {s}→{d}:",
                    bg=C["bg_panel"], fg=C["fg_dim"],
                    font=FS, width=10, anchor="e",
                ).pack(side="left")
                min_var = tk.StringVar()
                max_var = tk.StringVar()
                tk.Label(
                    row, text="mín:",
                    bg=C["bg_panel"], fg=C["fg_dim"], font=FS,
                ).pack(side="left", padx=(4, 2))
                tk.Entry(
                    row, textvariable=min_var,
                    bg=C["entry_bg"], fg=C["fg"], font=FM,
                    insertbackground=C["fg"], relief="flat", bd=4,
                    width=9,
                ).pack(side="left")
                tk.Label(
                    row, text="máx:",
                    bg=C["bg_panel"], fg=C["fg_dim"], font=FS,
                ).pack(side="left", padx=(8, 2))
                tk.Entry(
                    row, textvariable=max_var,
                    bg=C["entry_bg"], fg=C["fg"], font=FM,
                    insertbackground=C["fg"], relief="flat", bd=4,
                    width=9,
                ).pack(side="left")
                self._lambda_min_vars.append(min_var)
                self._lambda_max_vars.append(max_var)
    def _collect_config(self) -> Dict[str, Any]:
        # directorio de scripts
        scripts_dir = Path(self._scripts_var.get())
        if not scripts_dir.is_dir():
            raise ValueError(
                f"El directorio de scripts no existe: '{scripts_dir}'."
            )
        # grafo
        sel = self._graph_lb.curselection()
        if not sel:
            raise ValueError("Selecciona un grafo de la lista.")
        graph_name = self._graph_lb.get(sel[0])
        graph_path = Path(self._graphs_var.get()) / graph_name
        if not graph_path.is_file():
            raise ValueError(f"Grafo no encontrado: '{graph_path}'.")
        # routing
        sel = self._routing_lb.curselection()
        if not sel:
            raise ValueError("Selecciona un routing de la lista.")
        routing_name = self._routing_lb.get(sel[0])
        routing_path = Path(self._routings_var.get()) / routing_name
        if not routing_path.is_file():
            raise ValueError(f"Routing no encontrado: '{routing_path}'.")
        # directorio de salida
        split = self._split_var.get()
        base  = scripts_dir.parent
        if split == "train":
            output_dir = base / "train"
        elif split == "validation":
            output_dir = base / "validation"
        elif split == "test":
            output_dir = base / "test"
        else:
            custom = self._custom_out_var.get().strip()
            if not custom:
                raise ValueError(
                    "Indica el directorio de salida personalizado."
                )
            output_dir = Path(custom)
        # pares OD
        if not self._pairs:
            raise ValueError("Primero parsea los pares OD.")
        gen_mode = self._mode_var.get()
        if gen_mode == "random":
            # ── Modo aleatorio total ──────────────────────────────────────
            if not self._lambda_min_vars:
                raise ValueError(
                    "Parsea los pares OD para que aparezcan los campos de lambda."
                )
            try:
                nsamples = int(self._nsamples_var.get())
            except ValueError:
                raise ValueError("El nº de muestras debe ser un entero.")
            if nsamples < 1:
                raise ValueError("El nº de muestras debe ser >= 1.")
            ranges: List[Tuple[float, float]] = []
            for i, (mn_v, mx_v) in enumerate(
                zip(self._lambda_min_vars, self._lambda_max_vars)
            ):
                try:
                    lo = float(mn_v.get())
                    hi = float(mx_v.get())
                except ValueError:
                    s, d = self._pairs[i]
                    raise ValueError(
                        f"Mín/máx no numérico para λ {s}→{d}."
                    )
                if lo <= 0 or hi <= 0:
                    s, d = self._pairs[i]
                    raise ValueError(
                        f"Los extremos de λ {s}→{d} deben ser > 0."
                    )
                if lo > hi:
                    lo, hi = hi, lo
                ranges.append((lo, hi))
            return {
                "scripts_dir":  scripts_dir,
                "graph_path":   graph_path,
                "routing_path": routing_path,
                "output_dir":   output_dir,
                "pairs":        self._pairs,
                "gen_mode":     "random",
                "nsamples":     nsamples,
                "lambda_ranges": ranges,
                "pkt1":         int(self._pkt1_var.get()),
                "pkt2":         int(self._pkt2_var.get()),
            }
        else:
            # ── Modo cartesiano (comportamiento original) ─────────────────
            if not self._lambda_vars:
                raise ValueError(
                    "Parsea los pares OD para que aparezcan los campos de lambda."
                )
            specs = [v.get().strip() for v in self._lambda_vars]
            lambda_axes = parse_lambda_ranges(",".join(specs), len(self._pairs))
            # tamaños de paquete
            try:
                pkt1 = int(self._pkt1_var.get())
                pkt2 = int(self._pkt2_var.get())
            except ValueError:
                raise ValueError("Los tamaños de paquete deben ser enteros.")
            if pkt1 <= 0 or pkt2 <= 0:
                raise ValueError("Los tamaños de paquete deben ser > 0.")
            return {
                "scripts_dir":  scripts_dir,
                "graph_path":   graph_path,
                "routing_path": routing_path,
                "output_dir":   output_dir,
                "pairs":        self._pairs,
                "gen_mode":     "cartesian",
                "lambda_axes":  lambda_axes,
                "pkt1":         pkt1,
                "pkt2":         pkt2,
            }
    def _plan(self) -> None:
        try:
            cfg = self._collect_config()
        except ValueError as e:
            self._log_append(f"[!] {e}\n", "err")
            return
        self._log_append("─" * 58 + "\n", "dim")
        self._log_append("PLAN DE GENERACIÓN\n", "head")
        if cfg["gen_mode"] == "random":
            total  = cfg["nsamples"]
            pairs  = cfg["pairs"]
            ranges = cfg["lambda_ranges"]
            self._plan_var.set(f"→ {total} muestra(s) aleatorias a generar")
            for i, ((s, d), (lo, hi)) in enumerate(zip(pairs, ranges), 1):
                self._log_append(
                    f"  λ{i}: {s}→{d} | uniforme [{fmt(lo)}, {fmt(hi)}]\n",
                    "info",
                )
            self._log_append(
                f"  Muestras aleatorias independientes: {total}\n", "ok"
            )
        else:
            axes  = cfg["lambda_axes"]
            pairs = cfg["pairs"]
            total = count_combinations(axes)
            self._plan_var.set(f"→ {total} combinación(es) a generar")
            for i, ((s, d), axis) in enumerate(zip(pairs, axes), 1):
                step_txt = fmt(axis[1] - axis[0]) if len(axis) >= 2 else "N/A"
                self._log_append(
                    f"  λ{i}: {s}→{d} | {len(axis)} valor(es) | "
                    f"inicio={fmt(axis[0])} | fin={fmt(axis[-1])} | step={step_txt}\n",
                    "info",
                )
            self._log_append(
                f"  Producto cartesiano: {total} muestra(s)\n", "ok"
            )
        self._log_append(
            f"  Salida: {cfg['output_dir']}\n", "dim"
        )
        self._log_append("─" * 58 + "\n", "dim")
    def _generate(self) -> None:
        if self._running:
            return
        try:
            cfg = self._collect_config()
        except ValueError as e:
            self._log_append(f"[!] {e}\n", "err")
            return
        self._running = True
        self._stop_event.clear()
        self._btn_gen.config(state="disabled")
        self._btn_cancel.config(state="normal")
        self._status_var.set("Generando…")
        self._log_append("─" * 58 + "\n", "dim")
        self._log_append("▶ INICIANDO GENERACIÓN\n", "head")
        t = threading.Thread(
            target=self._generation_thread, args=(cfg,), daemon=True
        )
        t.start()
        self.root.after(100, self._poll_log_queue)
    # ── VISUALIZACIÓN DE GRAFO ────────────────────────────────────────────
    FLOW_PALETTE = [
        # primera vuelta: colores primarios muy diferenciados
        "#00d4ff",   # cian eléctrico
        "#f59e0b",   # ámbar
        "#22c55e",   # verde lima
        "#ef4444",   # rojo
        "#818cf8",   # índigo
        "#f97316",   # naranja
        "#06b6d4",   # turquesa
        "#ec4899",   # rosa fuerte
        "#84cc16",   # verde amarillento
        "#8b5cf6",   # violeta
        "#14b8a6",   # teal
        "#fbbf24",   # amarillo dorado
        "#e879f9",   # fucsia
        "#4ade80",   # verde menta
        "#fb7185",   # salmón
        "#38bdf8",   # celeste
        "#a3e635",   # lima neón
        "#c084fc",   # lavanda
        "#fdba74",   # melocotón
        "#2dd4bf",   # agua marina
    ]

    def _build_viz_tab(self, parent: tk.Frame) -> None:
        hdr = tk.Frame(parent, bg=C["bg_tb"], height=36)
        hdr.pack(fill="x")
        self._viz_title_var = tk.StringVar(value="Sin grafo cargado")
        tk.Label(
            hdr, textvariable=self._viz_title_var,
            bg=C["bg_tb"], fg=C["fg_dim"], font=FS, padx=12,
        ).pack(side="left", pady=8)
        _mk_btn(
            hdr, "⟳ Redibujar",
            lambda: self.root.after(0, self._draw_graph),
            C["bg_tb"], padx=10, pady=4,
        ).pack(side="right", padx=8, pady=4)
        _mk_btn(
            hdr, "📷 Exportar PNG",
            self._export_viz_png,
            C["bg_tb"], padx=10, pady=4,
        ).pack(side="right", padx=4, pady=4)
        self._viz_transparent_var = tk.BooleanVar(value=False)
        tk.Checkbutton(
            hdr, text="Fondo transparente",
            variable=self._viz_transparent_var,
            bg=C["bg_tb"], fg=C["fg_dim"], selectcolor=C["bg"],
            activebackground=C["bg_tb"], activeforeground=C["fg"],
            font=FS, bd=0, highlightthickness=0,
        ).pack(side="right", padx=6, pady=4)
        self._viz_canvas = tk.Canvas(
            parent, bg=C["bg"], highlightthickness=0, bd=0,
        )
        self._viz_canvas.pack(fill="both", expand=True)
        self._viz_canvas.bind(
            "<Configure>",
            lambda e: self.root.after(60, self._draw_graph),
        )
        self._viz_canvas.bind("<Button-1>", self._on_viz_click)

    def _export_viz_png(self) -> None:
        """Renderiza el grafo con matplotlib y lo guarda como PNG de alta resolución."""
        try:
            import matplotlib
            matplotlib.use("Agg")          # sin ventana; solo archivo
            import matplotlib.pyplot as plt
            import matplotlib.patches as mpatches
            from matplotlib.patches import FancyArrowPatch
        except ImportError:
            messagebox.showerror(
                "matplotlib no instalado",
                "Instala matplotlib:\n\n  pip install matplotlib",
            )
            return

        # ── comprobar que hay grafo seleccionado ──────────────────────────
        sel = self._graph_lb.curselection()
        if not sel:
            messagebox.showwarning("Sin grafo", "Selecciona primero un grafo.")
            return
        graph_name = self._graph_lb.get(sel[0])
        graph_path = Path(self._graphs_var.get()) / graph_name
        try:
            graph = nx.read_gml(str(graph_path), label=None)
        except Exception as exc:
            messagebox.showerror("Error al cargar grafo", str(exc))
            return
        n_nodes = graph.number_of_nodes()
        if n_nodes == 0:
            messagebox.showwarning("Grafo vacío", "El grafo no tiene nodos.")
            return

        # ── pedir destino antes de renderizar ─────────────────────────────
        graph_stem = Path(graph_name).stem
        path = filedialog.asksaveasfilename(
            defaultextension=".png",
            filetypes=[("PNG image", "*.png"), ("Todos los archivos", "*.*")],
            initialfile=f"{graph_stem}_flujos.png",
            title="Guardar imagen PNG",
        )
        if not path:
            return

        # ── layout ────────────────────────────────────────────────────────
        pos = nx.spring_layout(graph, seed=42)  # mismas posiciones que en canvas

        # ── recopilar flujos OD ───────────────────────────────────────────
        # flow_edges_multi: (u,v) → [(flow_idx, color), …]
        flow_edges_multi: Dict[Tuple[int, int], List[Tuple[int, str]]] = {}
        has_routing = False
        sel_r = self._routing_lb.curselection()
        if self._pairs and sel_r:
            routing_name = self._routing_lb.get(sel_r[0])
            routing_path = Path(self._routings_var.get()) / routing_name
            try:
                routing = read_routing(routing_path, n_nodes)
                pm = build_port_map(graph)
                has_routing = True
                for i, (s, d) in enumerate(self._pairs):
                    color = self.FLOW_PALETTE[i % len(self.FLOW_PALETTE)]
                    try:
                        _, queues = reconstruct_route(s, d, n_nodes, routing, pm)
                        for u, v, _port in queues:
                            flow_edges_multi.setdefault((u, v), []).append((i, color))
                    except Exception:
                        pass
            except Exception:
                pass

        # ── calcular μ por arista ─────────────────────────────────────────
        try:
            pkt1 = int(self._pkt1_var.get())
            pkt2 = int(self._pkt2_var.get())
            avg_pkt = (pkt1 + pkt2) / 2.0
        except Exception:
            avg_pkt = 0.0

        # ── paleta según modo (oscuro vs transparente) ────────────────────
        transp = self._viz_transparent_var.get()
        BG          = "#1e1e2e"
        COL_TEXT    = "#111111" if transp else "white"
        COL_EDGE_BG = "#888899" if transp else "#444466"
        COL_NODE_EC = "#111111" if transp else BG
        COL_LEG_BG  = "#eeeeee" if transp else "#2a2a3e"
        COL_LEG_EC  = "#aaaaaa" if transp else "#555577"
        COL_LEG_TXT = "#111111" if transp else "white"
        COL_MU_BG   = "#dddddd" if transp else BG
        AURA_COL    = "#333333" if transp else "white"

        # ── figura matplotlib ─────────────────────────────────────────────
        fig, ax = plt.subplots(figsize=(12, 9), dpi=150)
        if transp:
            fig.patch.set_alpha(0.0)
            ax.patch.set_alpha(0.0)
        else:
            fig.patch.set_facecolor(BG)
            ax.set_facecolor(BG)
        ax.set_aspect("equal")
        ax.axis("off")

        LANE_GAP = 0.025   # offset perpendicular en coordenadas nx (≈ [-1,1])
        NODE_R   = 0.06    # radio de nodo (para acortar flechas)

        def _unit_perp(u: int, v: int) -> Tuple[float, float]:
            x1, y1 = pos[u]; x2, y2 = pos[v]
            dx, dy = x2 - x1, y2 - y1
            length = math.hypot(dx, dy) or 1.0
            return -dy / length, dx / length

        def _trim(x1, y1, x2, y2, r):
            dx, dy = x2 - x1, y2 - y1
            length = math.hypot(dx, dy)
            if length < 2 * r + 0.01:
                return x1, y1, x2, y2
            ux, uy = dx / length, dy / length
            return x1 + ux * r, y1 + uy * r, x2 - ux * r, y2 - uy * r

        # ── aristas base (gris discontinuo) + etiqueta μ ──────────────────
        drawn_bw: Set[Tuple[int, int]] = set()
        for u, v, data in graph.edges(data=True):
            u_i, v_i = int(u), int(v)
            canonical = (min(u_i, v_i), max(u_i, v_i))
            if canonical in drawn_bw:
                continue
            drawn_bw.add(canonical)
            x1, y1 = pos[u]; x2, y2 = pos[v]
            ax.plot([x1, x2], [y1, y2], color=COL_EDGE_BG, lw=0.8,
                    linestyle="--", zorder=1)
            bw = data.get("bandwidth")
            if bw is not None and avg_pkt > 0:
                mu = float(bw) / avg_pkt
                mu_str = (f"{mu:.0f}" if mu == int(mu)
                          else f"{mu:.2f}".rstrip("0").rstrip("."))
                px, py = _unit_perp(u, v)
                mx, my = (x1 + x2) / 2 + px * 0.04, (y1 + y2) / 2 + py * 0.04
                ax.text(mx, my, f"μ={mu_str} pkt/s",
                        color=COL_TEXT, fontsize=6, fontfamily="monospace",
                        ha="center", va="center", zorder=3,
                        bbox=dict(boxstyle="round,pad=0.15",
                                  fc=COL_MU_BG, ec="none", alpha=0.75))

        # ── flujos coloreados con flechas y offset perpendicular ───────────
        for (u_i, v_i), flow_list in flow_edges_multi.items():
            x1, y1 = pos[u_i]; x2, y2 = pos[v_i]
            px, py = _unit_perp(u_i, v_i)
            n_fl = len(flow_list)
            for k, (fi, color) in enumerate(flow_list):
                off = (k - (n_fl - 1) / 2.0) * LANE_GAP
                ox, oy = px * off, py * off
                sx, sy, ex, ey = _trim(
                    x1 + ox, y1 + oy, x2 + ox, y2 + oy, NODE_R
                )
                lw = 3.5 if fi == self._viz_selected_flow else 1.8
                zorder = 5 if fi == self._viz_selected_flow else 4
                if fi == self._viz_selected_flow:
                    ax.annotate("", xy=(ex, ey), xytext=(sx, sy),
                                arrowprops=dict(arrowstyle="-|>",
                                                color=AURA_COL, lw=5,
                                                mutation_scale=14),
                                zorder=zorder - 1)
                ax.annotate("", xy=(ex, ey), xytext=(sx, sy),
                            arrowprops=dict(arrowstyle="-|>",
                                            color=color, lw=lw,
                                            mutation_scale=12),
                            zorder=zorder)

        # ── nodos ─────────────────────────────────────────────────────────
        ACCENT = "#7c6af7"
        for node in graph.nodes():
            node_i = int(node)
            x, y = pos[node]
            circle = plt.Circle((x, y), NODE_R, color=ACCENT,
                                 zorder=6, ec=COL_NODE_EC, lw=1.5)
            ax.add_patch(circle)
            ax.text(x, y, str(node_i), color=COL_TEXT, fontsize=8,
                    ha="center", va="center", fontweight="bold", zorder=7)

        # ── leyenda de flujos ─────────────────────────────────────────────
        if has_routing and self._pairs:
            legend_handles = []
            for i, (s, d) in enumerate(self._pairs):
                color = self.FLOW_PALETTE[i % len(self.FLOW_PALETTE)]
                lw = 3 if i == self._viz_selected_flow else 1.5
                patch = mpatches.Patch(color=color,
                                       label=f"Flujo {i}: {s} → {d}",
                                       linewidth=lw)
                legend_handles.append(patch)
            ax.legend(
                handles=legend_handles,
                loc="upper left", fontsize=7,
                framealpha=0.85, facecolor=COL_LEG_BG,
                edgecolor=COL_LEG_EC, labelcolor=COL_LEG_TXT,
            )

        # ── título ────────────────────────────────────────────────────────
        routing_label = ""
        sel_r = self._routing_lb.curselection()
        if sel_r:
            routing_label = f"  |  routing: {self._routing_lb.get(sel_r[0])}"
        ax.set_title(
            f"Grafo: {graph_name}{routing_label}",
            color=COL_TEXT, fontsize=10, pad=10,
        )

        plt.tight_layout(pad=0.5)
        try:
            save_kw: Dict[str, Any] = dict(dpi=150, bbox_inches="tight")
            if transp:
                save_kw["transparent"] = True
            else:
                save_kw["facecolor"] = BG
            fig.savefig(path, **save_kw)
            plt.close(fig)
            mode_str = " (transparente)" if transp else ""
            self._status_var.set(f"✓ PNG guardado{mode_str}: {Path(path).name}")
        except Exception as exc:
            plt.close(fig)
            messagebox.showerror("Error al guardar", str(exc))

    def _on_viz_click(self, event: Any) -> None:
        """Selecciona/deselecciona un flujo al hacer clic en cualquiera de sus flechas."""
        canvas = self._viz_canvas
        r = 10  # radio de hit-test en píxeles
        # find_overlapping busca TODOS los items en el área, no solo el más cercano.
        # Así detectamos líneas delgadas aunque haya nodos u otros items encima.
        overlapping = canvas.find_overlapping(
            event.x - r, event.y - r, event.x + r, event.y + r
        )
        for item in reversed(overlapping):   # reversed → el de mayor z-order primero
            for tag in canvas.gettags(item):
                if tag.startswith("node_"):
                    try:
                        nidx = int(tag.split("_")[1])
                    except (IndexError, ValueError):
                        continue
                    self._viz_selected_node = (
                        None if self._viz_selected_node == nidx else nidx
                    )
                    self._viz_selected_flow = None   # deseleccionar flujo al tocar nodo
                    self._draw_graph()
                    return
                if tag.startswith("flow_"):
                    try:
                        fidx = int(tag.split("_")[1])
                    except (IndexError, ValueError):
                        continue
                    self._viz_selected_flow = (
                        None if self._viz_selected_flow == fidx else fidx
                    )
                    self._viz_selected_node = None   # deseleccionar nodo al tocar flujo
                    self._draw_graph()
                    return
        # clic en fondo → deseleccionar todo
        if self._viz_selected_flow is not None or self._viz_selected_node is not None:
            self._viz_selected_flow = None
            self._viz_selected_node = None
            self._draw_graph()

    def _draw_graph(self) -> None:
        """Dibuja el grafo seleccionado; resalta los enlaces de cada flujo OD."""
        if not hasattr(self, "_viz_canvas"):
            return
        canvas = self._viz_canvas
        w = canvas.winfo_width()
        h = canvas.winfo_height()
        if w < 20 or h < 20:
            self.root.after(120, self._draw_graph)
            return
        canvas.delete("all")

        # ── cargar el grafo seleccionado ──────────────────────────────────
        sel = self._graph_lb.curselection()
        if not sel:
            canvas.create_text(
                w // 2, h // 2,
                text="Selecciona un grafo en la pestaña Generación",
                fill=C["fg_dim"], font=F, justify="center",
            )
            self._viz_title_var.set("Sin grafo seleccionado")
            return
        graph_name = self._graph_lb.get(sel[0])
        graph_path = Path(self._graphs_var.get()) / graph_name
        try:
            graph = nx.read_gml(str(graph_path), label=None)
        except Exception as exc:
            canvas.create_text(
                w // 2, h // 2,
                text=f"Error al cargar el grafo:\n{exc}",
                fill=C["red"], font=F, justify="center",
            )
            self._viz_title_var.set("Error al cargar el grafo")
            return
        n_nodes = graph.number_of_nodes()
        if n_nodes == 0:
            canvas.create_text(w // 2, h // 2, text="Grafo vacío",
                               fill=C["fg_dim"], font=F)
            return

        # ── calcular layout ───────────────────────────────────────────────
        pos = nx.spring_layout(graph, seed=42)

        # ── detectar aristas activas (de los flujos OD) ───────────────────
        # flow_edges_multi: (u,v) → lista de (flow_idx, color)
        flow_edges_multi: Dict[Tuple[int, int], List[Tuple[int, str]]] = {}
        has_routing = False

        if self._pairs:
            sel_r = self._routing_lb.curselection()
            if sel_r:
                routing_name = self._routing_lb.get(sel_r[0])
                routing_path = Path(self._routings_var.get()) / routing_name
                try:
                    routing = read_routing(routing_path, n_nodes)
                    pm = build_port_map(graph)
                    has_routing = True
                    for i, (s, d) in enumerate(self._pairs):
                        color = self.FLOW_PALETTE[i % len(self.FLOW_PALETTE)]
                        try:
                            _path, queues = reconstruct_route(
                                s, d, n_nodes, routing, pm
                            )
                            for u, v, _port in queues:
                                flow_edges_multi.setdefault((u, v), []).append((i, color))
                        except Exception:
                            pass
                except Exception:
                    pass

        sel_flow = self._viz_selected_flow  # índice de flujo seleccionado (o None)
        sel_node = self._viz_selected_node  # índice de nodo seleccionado (o None)

        # ── mapear posiciones nx → coordenadas canvas ─────────────────────
        margin = 52
        xs = [pos[nd][0] for nd in graph.nodes()]
        ys = [pos[nd][1] for nd in graph.nodes()]
        min_x, max_x = min(xs), max(xs)
        min_y, max_y = min(ys), max(ys)
        dx = max_x - min_x or 1.0
        dy = max_y - min_y or 1.0
        node_r = 14

        def to_cv(nx_: float, ny_: float) -> Tuple[float, float]:
            cx = margin + (nx_ - min_x) / dx * (w - 2 * margin)
            cy = margin + (ny_ - min_y) / dy * (h - 2 * margin)
            return cx, cy

        def _shorten(x1: float, y1: float, x2: float, y2: float,
                     r: float) -> Tuple[float, float, float, float]:
            edx, edy = x2 - x1, y2 - y1
            length = math.hypot(edx, edy)
            if length < 2 * r + 4:
                return x1, y1, x2, y2
            ux, uy = edx / length, edy / length
            return (x1 + ux * r, y1 + uy * r,
                    x2 - ux * r, y2 - uy * r)

        # ── dibujar aristas ───────────────────────────────────────────────
        drawn_bw: Set[Tuple[int, int]] = set()
        for u, v, data in graph.edges(data=True):
            u_i, v_i = int(u), int(v)
            canonical = (min(u_i, v_i), max(u_i, v_i))
            x1, y1 = to_cv(*pos[u])
            x2, y2 = to_cv(*pos[v])

            fwd_items = flow_edges_multi.get((u_i, v_i), [])
            bwd_items = flow_edges_multi.get((v_i, u_i), [])
            # all_directed: (sx, sy, ex, ey, flow_idx, color)
            all_directed = (
                [(x1, y1, x2, y2, fi, c) for fi, c in fwd_items] +
                [(x2, y2, x1, y1, fi, c) for fi, c in bwd_items]
            )

            edx, edy = x2 - x1, y2 - y1
            length = math.hypot(edx, edy) or 1.0
            px, py = -edy / length, edx / length   # perpendicular

            # ── arista base gris ──────────────────────────────────────────
            if canonical not in drawn_bw:
                drawn_bw.add(canonical)
                canvas.create_line(
                    x1, y1, x2, y2,
                    fill=C["border"], width=1, dash=(4, 3),
                )
                bw = data.get("bandwidth")
                if bw is not None:
                    mx, my = (x1 + x2) / 2, (y1 + y2) / 2
                    try:
                        pkt1 = int(self._pkt1_var.get())
                        pkt2 = int(self._pkt2_var.get())
                        avg_pkt = (pkt1 + pkt2) / 2.0
                        mu = float(bw) / avg_pkt if avg_pkt > 0 else 0.0
                        mu_str = (
                            f"{mu:.0f}" if mu == int(mu)
                            else f"{mu:.2f}".rstrip("0").rstrip(".")
                        )
                        label = f"μ={mu_str} pkt/s"
                    except Exception:
                        label = str(bw)
                    canvas.create_text(
                        mx + px * 10, my + py * 10,
                        text=label,
                        fill="white", font=("Consolas", 9, "bold"),
                    )

            # ── flujos con offset, aura y flecha ──────────────────────────
            n_fl = len(all_directed)
            if n_fl == 0:
                continue
            LANE_GAP = 6
            offsets = [(k - (n_fl - 1) / 2.0) * LANE_GAP for k in range(n_fl)]
            for (sx, sy, ex, ey, fi, color), off in zip(all_directed, offsets):
                ox, oy = px * off, py * off
                sx2, sy2, ex2, ey2 = _shorten(
                    sx + ox, sy + oy, ex + ox, ey + oy, node_r
                )
                tag = f"flow_{fi}"
                # aura blanca si este flujo está seleccionado
                if sel_flow == fi:
                    canvas.create_line(
                        sx2, sy2, ex2, ey2,
                        fill="white", width=9,
                        capstyle="round",
                        tags=(tag,),
                    )
                # línea coloreada con flecha
                canvas.create_line(
                    sx2, sy2, ex2, ey2,
                    fill=color, width=2,
                    arrow="last", arrowshape=(10, 13, 4),
                    capstyle="round",
                    tags=(tag,),
                )

        # ── flechitas de entrada λ_i en nodos fuente ─────────────────────
        if has_routing and self._pairs:
            # source_flows: nodo → [(fi, color), …]
            source_flows_map: Dict[int, List[Tuple[int, str]]] = {}
            for i, (s, d) in enumerate(self._pairs):
                color = self.FLOW_PALETTE[i % len(self.FLOW_PALETTE)]
                source_flows_map.setdefault(s, []).append((i, color))

            for src_node, sf_list in source_flows_map.items():
                cx, cy = to_cv(*pos[src_node])
                n_src = len(sf_list)
                # distribuir ángulos alrededor de "arriba" del nodo
                # ángulo en coords matemáticas: π/2 = arriba en canvas
                base_angle = math.pi / 2.0
                spread = math.radians(30)   # separación entre flechas
                for k, (fi, color) in enumerate(sf_list):
                    angle = base_angle + spread * (k - (n_src - 1) / 2.0)
                    cos_a, sin_a = math.cos(angle), math.sin(angle)
                    outer_r = node_r + 26
                    # flecha: desde exterior → borde del nodo
                    # canvas y va hacia abajo → usamos -sin_a
                    ax_s = cx + cos_a * outer_r
                    ay_s = cy - sin_a * outer_r
                    ax_e = cx + cos_a * (node_r + 1)
                    ay_e = cy - sin_a * (node_r + 1)
                    canvas.create_line(
                        ax_s, ay_s, ax_e, ay_e,
                        fill=color, width=2,
                        arrow="last", arrowshape=(8, 10, 3),
                        dash=(5, 3),
                    )
                    # etiqueta λ_N en el exterior
                    lx = cx + cos_a * (outer_r + 13)
                    ly = cy - sin_a * (outer_r + 13)
                    canvas.create_text(
                        lx, ly,
                        text=f"λ_{fi}",
                        fill=color, font=FS,
                    )

        # ── dibujar nodos (encima de todo) ────────────────────────────────
        for node in graph.nodes():
            node_i = int(node)
            x, y = to_cv(*pos[node])
            ntag = f"node_{node_i}"
            is_sel = (sel_node == node_i)
            # halo de selección
            if is_sel:
                canvas.create_oval(
                    x - node_r - 5, y - node_r - 5,
                    x + node_r + 5, y + node_r + 5,
                    fill="", outline="white", width=2, tags=(ntag,),
                )
            canvas.create_oval(
                x - node_r, y - node_r, x + node_r, y + node_r,
                fill="white" if is_sel else C["accent"],
                outline=C["bg"], width=2, tags=(ntag,),
            )
            canvas.create_text(
                x, y, text=str(node_i),
                fill=C["accent"] if is_sel else C["fg"],
                font=FB, tags=(ntag,),
            )

        # ── panel de información de nodo seleccionado ─────────────────────
        if sel_node is not None and has_routing:
            # flujos que salen del nodo seleccionado (tienen al menos un enlace (sel_node, v))
            node_flows: List[Tuple[int, str]] = []
            for (u_i, _v_i), items in flow_edges_multi.items():
                if u_i == sel_node:
                    for fi, color in items:
                        if not any(f == fi for f, _ in node_flows):
                            node_flows.append((fi, color))
            node_flows.sort(key=lambda t: t[0])

            # obtener intervalo [lo, hi] por flujo
            def _get_lambda_range(fi: int) -> Optional[Tuple[float, float]]:
                try:
                    if self._mode_var.get() == "cartesian" and fi < len(self._lambda_vars):
                        vals = parse_lambda_axis(self._lambda_vars[fi].get())
                        if vals:
                            return (min(vals), max(vals))
                    elif self._mode_var.get() == "random":
                        if fi < len(self._lambda_min_vars):
                            lo = float(self._lambda_min_vars[fi].get())
                            hi = float(self._lambda_max_vars[fi].get())
                            return (min(lo, hi), max(lo, hi))
                except Exception:
                    pass
                return None

            lam_ranges = {fi: _get_lambda_range(fi) for fi, _ in node_flows}
            have_values = any(v is not None for v in lam_ranges.values())

            # construir líneas del panel
            lines: List[Tuple[str, str]] = []   # (texto, color)
            lines.append((f"Nodo {sel_node}", "white"))
            if not node_flows:
                lines.append(("Sin flujos transitando", C["fg_dim"]))
            else:
                lines.append((f"{len(node_flows)} flujo(s) entrante(s):", C["fg_dim"]))
                for fi, color in node_flows:
                    rng = lam_ranges.get(fi)
                    s, d = self._pairs[fi]
                    if rng is not None:
                        lo, hi = rng
                        if lo == hi:
                            lam_str = f" · λ={fmt(lo)}"
                        else:
                            lam_str = f" · [{fmt(lo)}, {fmt(hi)}]"
                    else:
                        lam_str = ""
                    lines.append((f"  F{fi}: {s}→{d}{lam_str}", color))

                lines.append(("─" * 18, C["border"]))
                # fórmula simbólica
                lam_sym = " + ".join(f"λ_{fi}" for fi, _ in node_flows)
                lines.append((f"  Σλ = {lam_sym}", C["fg_dim"]))
                if have_values:
                    # Σλ_min y Σλ_max
                    sum_lo = sum(r[0] for r in lam_ranges.values() if r is not None)
                    sum_hi = sum(r[1] for r in lam_ranges.values() if r is not None)
                    lines.append((f"  Σλ_min = {fmt(sum_lo)}", "#aaaaff"))
                    lines.append((f"  Σλ_max = {fmt(sum_hi)}", "#ffaaaa"))

            # posición del panel: junto al nodo seleccionado
            nx_x, nx_y = to_cv(*pos[sel_node])
            pad, row_h, box_w = 8, 16, 200
            bh = pad * 2 + len(lines) * row_h
            bx = min(nx_x + node_r + 8, w - box_w - 6)
            by = max(6, min(nx_y - bh // 2, h - bh - 6))
            canvas.create_rectangle(
                bx, by, bx + box_w, by + bh,
                fill=C["bg_panel"], outline="white", width=1,
            )
            for k, (txt, col) in enumerate(lines):
                canvas.create_text(
                    bx + pad, by + pad + k * row_h,
                    text=txt, fill=col,
                    font=FB if k == 0 else FS,
                    anchor="nw",
                )

        # ── leyenda de flujos ─────────────────────────────────────────────
        if self._pairs and has_routing:
            lx, ly = 10, 10
            row_h = 18
            box_w = 96
            canvas.create_rectangle(
                lx - 4, ly - 4,
                lx + box_w, ly + row_h + len(self._pairs) * row_h,
                fill=C["bg_panel"], outline=C["border"],
            )
            canvas.create_text(
                lx, ly, text="Flujos:", fill=C["fg_dim"],
                font=FS, anchor="nw",
            )
            ly += row_h
            for i, (s, d) in enumerate(self._pairs):
                color = self.FLOW_PALETTE[i % len(self.FLOW_PALETTE)]
                tag = f"flow_{i}"
                # resaltar entrada de leyenda si flujo seleccionado
                if sel_flow == i:
                    canvas.create_rectangle(
                        lx - 2, ly - 1, lx + box_w - 2, ly + 14,
                        fill=C["sel_bg"], outline="",
                    )
                canvas.create_line(
                    lx, ly + 7, lx + 14, ly + 7,
                    fill=color, width=2, arrow="last", arrowshape=(7, 9, 3),
                    tags=(tag,),
                )
                canvas.create_text(
                    lx + 18, ly, text=f"{s} → {d}",
                    fill="white" if sel_flow == i else C["fg"],
                    font=FS if sel_flow != i else FB,
                    anchor="nw",
                    tags=(tag,),
                )
                ly += row_h

        # ── título / info ─────────────────────────────────────────────────
        n_active = len(flow_edges_multi)
        sel_txt = (
            f"  |  flujo {sel_flow} seleccionado"
            if sel_flow is not None else ""
        )
        route_txt = (
            f"  |  {n_active} enlace(s) activo(s){sel_txt}" if n_active
            else ("  |  parsea pares OD para ver rutas"
                  if has_routing or self._pairs else "")
        )
        self._viz_title_var.set(
            f"{graph_name}  —  {n_nodes} nodos, "
            f"{graph.number_of_edges()} aristas{route_txt}"
        )

    # ── HILO DE GENERACIÓN ────────────────────────────────────────────────────
    def _generation_thread(self, cfg: Dict[str, Any]) -> None:
        q = self._log_q
        try:
            scripts_dir  = cfg["scripts_dir"]
            graph_path   = cfg["graph_path"]
            routing_path = cfg["routing_path"]
            output_dir   = cfg["output_dir"]
            pairs        = cfg["pairs"]
            pkt1         = cfg["pkt1"]
            pkt2         = cfg["pkt2"]
            gen_mode     = cfg["gen_mode"]
            avg_pkt      = (pkt1 + pkt2) / 2.0
            q.put(("log", f"  Grafo   : {graph_path.name}\n",   "dim"))
            q.put(("log", f"  Routing : {routing_path.name}\n", "dim"))
            q.put(("log", f"  Salida  : {output_dir}\n",        "dim"))
            # cargar y validar
            q.put(("log", "  Cargando y validando grafo/routing…\n", "info"))
            graph, n, routing, pm = load_and_validate(graph_path, routing_path)
            q.put(("log", f"  [OK] {n} nodos en el grafo\n", "ok"))
            # validar rutas OD
            for s, d in pairs:
                reconstruct_route(s, d, n, routing, pm)
            q.put(("log", f"  [OK] {len(pairs)} par(es) OD válidos\n", "ok"))
            # copiar grafo y routing a la partición
            output_dir.mkdir(parents=True, exist_ok=True)
            gc, rc = prepare_partition_resources(
                output_dir, graph_path, routing_path
            )
            q.put(("log",
                   f"  [OK] Copiados a '{output_dir.name}/"
                   f"graphs' y '{output_dir.name}/routings'\n", "ok"))
            # localizar generadores externos
            q.put(("log", "  Localizando scripts externos…\n", "info"))
            script_paths = resolve_external_generators(scripts_dir)
            for k, sp in script_paths.items():
                q.put(("log", f"    {k}: {sp.name}\n", "dim"))
            # ── construir iterador de tasas según el modo ─────────────────
            if gen_mode == "random":
                total  = cfg["nsamples"]
                ranges = cfg["lambda_ranges"]
                q.put(("log",
                       f"  {total} muestra(s) aleatorias (ρ<1 garantizado). "
                       f"Primera carpeta: results_{next_result_index(output_dir)}\n",
                       "ok"))
            else:
                lambda_axes = cfg["lambda_axes"]
                total       = count_combinations(lambda_axes)
                rate_iter   = itertools.product(*lambda_axes)
                q.put(("log",
                       f"  {total} combinación(es). "
                       f"Primera carpeta: results_{next_result_index(output_dir)}\n",
                       "ok"))
            first_idx = next_result_index(output_dir)
            with tempfile.TemporaryDirectory(
                prefix="routenet_gui_"
            ) as tmpdir:
                workspace = Path(tmpdir)
                q.put(("log", "  Preparando workspace temporal…\n", "info"))
                modules = prepare_external_workspace(
                    workspace, graph_path, routing_path, script_paths
                )
                q.put(("log", "  [OK] Workspace listo\n", "ok"))
                q.put(("log", "─" * 58 + "\n", "dim"))
                t_loop_start: float = time.time()

                if gen_mode == "random":
                    # ── MODO ALEATORIO: rechazar muestras con ρ ≥ 1 ──────────
                    valid_count = 0      # muestras aceptadas
                    discarded   = 0      # muestras rechazadas
                    while valid_count < total:
                        if self._stop_event.is_set():
                            q.put(("log",
                                   "\n[!] Generación cancelada por el usuario.\n",
                                   "warn"))
                            q.put(("done", False))
                            return
                        # generar tasas aleatorias candidatas
                        rates = [
                            random.uniform(lo, hi) if lo != hi else lo
                            for lo, hi in ranges
                        ]
                        # calcular ρ_max ANTES de lanzar scripts externos
                        flows = build_flows(pairs, rates, avg_pkt, n, routing, pm)
                        loads = build_queue_loads(graph, flows)
                        max_rho = max(
                            (ql.rho for ql in loads.values()), default=0.0
                        )
                        if max_rho >= 1.0 and self._skip_var.get():
                            discarded += 1
                            rates_str = ", ".join(fmt(r) for r in rates)
                            q.put(("log",
                                   f"  [SKIP #{discarded}] λ=({rates_str}) | "
                                   f"ρ_max={fmt(max_rho)} ≥ 1 → descartado\n",
                                   "dim"))
                            continue
                        # muestra válida → generar y guardar
                        ridx = first_idx + valid_count
                        contents = generate_one_sample_externally(
                            modules, script_paths, workspace,
                            pairs, rates, pkt1, pkt2,
                        )
                        valid_count += 1
                        cur  = valid_count
                        rates_str  = ", ".join(fmt(r) for r in rates)
                        gdelay, fdelays = simulation_delays_from_content(
                            contents["simulationResults.txt"], n, pairs
                        )
                        delays_str = ", ".join(fmt(dd) for dd in fdelays)
                        _rdir, arch = create_result_package(
                            output_dir, ridx, contents
                        )
                        elapsed = time.time() - t_loop_start
                        q.put(("progress", cur, total, elapsed))
                        stable_mark = "ESTABLE" if max_rho < 1.0 else "INESTABLE"
                        tag = "ok" if max_rho < 1.0 else "warn"
                        q.put(("log",
                               f"  [{cur}/{total}] λ=({rates_str}) | "
                               f"ρ_max={fmt(max_rho)} [{stable_mark}] | "
                               f"delay_global={fmt(gdelay)} | "
                               f"delays=({delays_str}) | {arch.name}"
                               + (f"  (descartadas: {discarded})" if discarded else "")
                               + "\n",
                               tag))
                else:
                    # ── MODO CARTESIANO ──────────────────────────────────────
                    for combo_num, rates_tuple in enumerate(rate_iter):
                        if self._stop_event.is_set():
                            q.put(("log",
                                   "\n[!] Generación cancelada por el usuario.\n",
                                   "warn"))
                            q.put(("done", False))
                            return
                        rates = list(rates_tuple)
                        ridx  = first_idx + combo_num
                        cur   = combo_num + 1
                        flows = build_flows(pairs, rates, avg_pkt, n, routing, pm)
                        loads = build_queue_loads(graph, flows)
                        max_rho = max(
                            (ql.rho for ql in loads.values()), default=0.0
                        )
                        contents = generate_one_sample_externally(
                            modules, script_paths, workspace,
                            pairs, rates, pkt1, pkt2,
                        )
                        rates_str   = ", ".join(fmt(r) for r in rates)
                        gdelay, fdelays = simulation_delays_from_content(
                            contents["simulationResults.txt"], n, pairs
                        )
                        delays_str  = ", ".join(fmt(dd) for dd in fdelays)
                        tag = "ok" if max_rho < 1.0 else "warn"
                        stable_mark = "ESTABLE" if max_rho < 1.0 else "INESTABLE"
                        _rdir, arch = create_result_package(
                            output_dir, ridx, contents
                        )
                        elapsed = time.time() - t_loop_start
                        q.put(("progress", cur, total, elapsed))
                        q.put(("log",
                               f"  [{cur}/{total}] λ=({rates_str}) | "
                               f"ρ_max={fmt(max_rho)} [{stable_mark}] | "
                               f"delay_global={fmt(gdelay)} | "
                               f"delays=({delays_str}) | {arch.name}\n",
                               tag))

            q.put(("log", "─" * 58 + "\n", "dim"))
            q.put(("log",
                   f"[OK] {total} muestra(s) en '{output_dir}'\n", "ok"))
            q.put(("done", True))
        except Exception as exc:
            q.put(("log", f"\n[ERROR] {exc}\n", "err"))
            q.put(("done", False))
    # ── POLLING DE COLA ───────────────────────────────────────────────────────
    _LOG_MAX_LINES = 1500   # máximo de líneas que guarda el log
    _POLL_BATCH    = 20    # mensajes procesados por tick (evita bloquear la UI)
    def _poll_log_queue(self) -> None:
        for _ in range(self._POLL_BATCH):
            try:
                msg = self._log_q.get_nowait()
            except queue.Empty:
                break
            kind = msg[0]
            if kind == "log":
                self._log_append(msg[1], msg[2])
            elif kind == "progress":
                cur, total, elapsed = msg[1], msg[2], msg[3]
                if cur > 0 and elapsed > 0:
                    avg_per_sample = elapsed / cur
                    remaining      = avg_per_sample * (total - cur)
                    eta_str        = _fmt_eta(remaining)
                    self._progress_var.set(f"{cur}/{total}  ETA {eta_str}")
                    self._status_var.set(
                        f"Generando {cur} de {total}  —  "
                        f"~{_fmt_eta(avg_per_sample)}/muestra  —  "
                        f"ETA {eta_str}"
                    )
                else:
                    self._progress_var.set(f"{cur}/{total}")
                    self._status_var.set(f"Generando {cur} de {total}…")
            elif kind == "done":
                success = msg[1]
                self._running = False
                self._btn_gen.config(state="normal")
                self._btn_cancel.config(state="disabled")
                self._progress_var.set("")
                self._status_var.set(
                    "Completado." if success else "Terminado con errores."
                )
                return
        try:
            self.root.update_idletasks()
        except Exception:
            pass
        self.root.after(100, self._poll_log_queue)
    def _cancel(self) -> None:
        if self._running:
            self._stop_event.set()
            self._status_var.set("Cancelando…")
            self._btn_cancel.config(state="disabled")
    # ── LIMPIEZA ─────────────────────────────────────────────────────────────
    def _clean_split(self, split_name: Optional[str]) -> None:
        """Borra todos los results_* de la carpeta del split indicado."""
        if split_name is not None:
            scripts_dir = Path(self._scripts_var.get())
            target = scripts_dir.parent / split_name
        else:
            custom = self._custom_out_var.get().strip()
            if not custom:
                self._log_append(
                    "[!] Indica primero un directorio personalizado.\n", "err"
                )
                return
            target = Path(custom)
        if not target.is_dir():
            self._log_append(
                f"[!] No existe la carpeta '{target}'.\n", "warn"
            )
            return
        # recopilar qué se va a borrar: results_*, graphs/, routings/
        pat = re.compile(r"^results_\d+(?:\.tar\.gz)?$")
        victims = [c for c in target.iterdir() if pat.match(c.name)]
        for extra in ("graphs", "routings"):
            ep = target / extra
            if ep.is_dir():
                victims.append(ep)
        if not victims:
            self._log_append(
                f"[i] '{target.name}' no tiene nada que borrar.\n",
                "dim",
            )
            return
        label = split_name if split_name else target.name
        preview = sorted(v.name for v in victims)
        ok = messagebox.askyesno(
            "Confirmar limpieza",
            f"¿Borrar {len(victims)} elemento(s) de '{label}'?\n\n"
            + "\n".join(preview[:14])
            + ("\n…" if len(preview) > 14 else ""),
        )
        if not ok:
            return
        deleted, errors = 0, 0
        for v in victims:
            try:
                if v.is_dir():
                    shutil.rmtree(str(v))
                else:
                    v.unlink()
                deleted += 1
            except Exception as exc:
                self._log_append(f"[!] No se pudo borrar '{v.name}': {exc}\n", "err")
                errors += 1
        if deleted:
            self._log_append(
                f"[OK] Borrados {deleted} elemento(s) de '{label}'.\n", "ok"
            )
        if errors:
            self._log_append(
                f"[!] {errors} elemento(s) no pudieron borrarse.\n", "warn"
            )
    # ── LOG ───────────────────────────────────────────────────────────────────
    def _log_append(self, text: str, tag: str = "info") -> None:
        self._log.config(state="normal")
        self._log.insert("end", text, tag)
        # recortar si supera el máximo de líneas para no acumular RAM
        lines = int(self._log.index("end-1c").split(".")[0])
        if lines > self._LOG_MAX_LINES:
            self._log.delete("1.0", f"{lines - self._LOG_MAX_LINES}.0")
        self._log.see("end")
        self._log.config(state="disabled")
    def _clear_log(self) -> None:
        self._log.config(state="normal")
        self._log.delete("1.0", "end")
        self._log.config(state="disabled")
        self._plan_var.set("")
# ─── ENTRY POINT ─────────────────────────────────────────────────────────────
def main() -> None:
    root = tk.Tk()
    root.configure(bg=C["bg"])
    DatasetGeneratorApp(root)
    root.mainloop()
if __name__ == "__main__":
    main()