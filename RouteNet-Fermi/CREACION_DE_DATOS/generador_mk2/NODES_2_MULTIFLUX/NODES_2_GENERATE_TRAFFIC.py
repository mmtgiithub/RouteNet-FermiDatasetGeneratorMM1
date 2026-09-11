#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
NODES_2_GENERATE_TRAFFIC_JACKSON.py

Genera ../traffic.txt con varios flujos origen-destino. El usuario decide
la lambda de entrada de CADA flujo.

Cada flujo OD se trata como una clase de una red de Jackson abierta:

    lambda_f = gamma_f + P_f^T lambda_f

Despues se suman las tasas internas de todos los flujos en cada enlace:

    lambda_total(q) = sum_f lambda_f(q)

Para cada cola/enlace:

    mu(q)  = bandwidth(q) / avg_packet_size
    rho(q) = lambda_total(q) / mu(q)

La red es estable cuando rho(q) < 1 en todas las colas.

IMPORTANTE:
- traffic.txt contiene solamente los flujos OD externos.
- Los saltos intermedios no se escriben como flujos nuevos.
- El primer valor de cada linea de traffic.txt es la suma de las lambdas
  externas de todos los flujos.

Compatible con Python 3.8.

Rutas hardcodeadas:
    ../input_files.txt
    ../graphs/
    ../routings/
    ../traffic.txt
    ../jackson_report.txt  # DESACTIVADO: no se genera

Uso interactivo:
    python NODES_2_GENERATE_TRAFFIC_JACKSON.py 300 1700

Con pares concretos:
    python NODES_2_GENERATE_TRAFFIC_JACKSON.py 300 1700 \
        --pairs 0:5,2:8

Con pares y lambdas (valor fijo, rango o aleatorios):
    python NODES_2_GENERATE_TRAFFIC_JACKSON.py 300 1700 \
        --pairs 0:5,2:8 \
        --rates 500,200

    python NODES_2_GENERATE_TRAFFIC_JACKSON.py 300 1700 \
        --pairs 0:5,2:8 \
        --rates 100:900:50,200:400:25

    python NODES_2_GENERATE_TRAFFIC_JACKSON.py 300 1700 \
        --pairs 0:5,2:8 \
        --rates 100:900:r20,200:400:r10

Formatos de --rates por flujo (modo cartesiano):
    valor           -> un unico valor fijo
    inicio:fin      -> rango paso 1 (o -1)
    inicio:fin:paso -> rango con paso explicito
    inicio:fin:rN   -> N valores aleatorios uniformes en [inicio, fin]

Modo muestreo aleatorio total (--nsamples N):
    Genera exactamente N muestras. Cada muestra sortea un lambda
    independiente por flujo en su rango [inicio, fin].
    --rates debe indicar rangos lo:hi (o lo:hi:cualquier_cosa).
    No se hace producto cartesiano: siempre salen exactamente N lineas.

    python NODES_2_GENERATE_TRAFFIC_JACKSON.py 300 1700 \
        --pairs 0:5,2:8 \
        --rates 100:900,200:400 \
        --nsamples 50

"""

import argparse
import itertools
import math
import random
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple

import networkx as nx
import numpy as np


SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_DIR = SCRIPT_DIR.parent

INPUT_FILES = PROJECT_DIR / "input_files.txt"
GRAPHS_DIR = PROJECT_DIR / "graphs"
ROUTINGS_DIR = PROJECT_DIR / "routings"
OUTPUT_FILE = PROJECT_DIR / "traffic.txt"
# REPORT_FILE = PROJECT_DIR / "jackson_report.txt"  # DESACTIVADO

TIME_DIST = 0
EXP_MAX_FACTOR = 10.0
SIZE_DIST = 2
TOS = 0
NO_FLOW = "-1,0,-1,0,0"

QueueId = Tuple[int, int, int]  # (u, v, port)
Pair = Tuple[int, int]


def fmt(value: float, decimals: int = 6) -> str:
    if math.isinf(value):
        return "INF"

    text = ("{0:." + str(decimals) + "f}").format(value)
    return text.rstrip("0").rstrip(".") or "0"


def fail(message: str) -> None:
    print("[!] {}".format(message), file=sys.stderr)
    raise SystemExit(1)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Genera trafico multi-OD y permite decidir la lambda de entrada "
            "de cada flujo."
        )
    )

    parser.add_argument("pkt1_size", type=int)
    parser.add_argument("pkt2_size", type=int)

    parser.add_argument(
        "--num-pairs",
        type=int,
        default=None,
        help=(
            "Numero de pares OD distintos. Si se omite y tampoco se usan "
            "--pairs, el programa lo pregunta."
        ),
    )

    parser.add_argument(
        "--pairs",
        type=str,
        default=None,
        help="Pares OD concretos: 0:5,2:8,6:1",
    )

    parser.add_argument(
        "--rates",
        type=str,
        default=None,
        help=(
            "Lambda de entrada de cada flujo, en el mismo orden que --pairs. "
            "Formatos por flujo: valor | inicio:fin | inicio:fin:paso | inicio:fin:rN. "
            "Ejemplo: --rates 500,200  o  --rates 100:900:r20,200:400:r10"
        ),
    )

    parser.add_argument(
        "--nsamples",
        type=int,
        default=None,
        help=(
            "Numero total de muestras aleatorias a generar. "
            "Cada flujo sortea un lambda independiente en su rango [lo, hi]. "
            "Requiere --rates con especificaciones lo:hi por flujo. "
            "Incompatible con el modo cartesiano."
        ),
    )

    parser.add_argument(
        "--seed",
        type=int,
        default=None,
        help="Semilla para el generador aleatorio (reproducibilidad).",
    )

    return parser.parse_args()


# ─── PARSEO DE RANGOS DE LAMBDAS ─────────────────────────────────────────────

def parse_rate_axis(spec: str) -> List[float]:
    """
    Convierte una especificacion de lambda en una lista de valores.

    Formatos:
        500           -> [500.0]                          valor fijo
        100:900       -> [100.0, 101.0, ..., 900.0]       rango paso 1
        100:900:50    -> [100.0, 150.0, ..., 900.0]       rango paso 50
        100:900:r20   -> [rnd1, ..., rnd20]               20 aleatorios en [100, 900]
    """
    spec = spec.strip()
    if not spec:
        raise ValueError("Especificacion de lambda vacia.")

    pieces = [p.strip() for p in spec.split(":")]
    if len(pieces) not in {1, 2, 3}:
        raise ValueError(
            "Especificacion invalida '{}'. "
            "Usa: valor | inicio:fin | inicio:fin:paso | inicio:fin:rN."
            .format(spec)
        )

    # ── modo aleatorio: inicio:fin:rN ────────────────────────────────────────
    if len(pieces) == 3 and pieces[2].lower().startswith("r"):
        count_str = pieces[2][1:]
        try:
            count = int(count_str)
        except ValueError:
            raise ValueError(
                "Numero de muestras invalido en '{}'. Usa rN, p.ej. r10."
                .format(spec)
            )
        if count < 1:
            raise ValueError(
                "El numero de muestras aleatorias debe ser >= 1."
            )
        try:
            lo = float(pieces[0])
            hi = float(pieces[1])
        except ValueError:
            raise ValueError(
                "Valor no numerico en '{}'.".format(spec)
            )
        if lo <= 0 or hi <= 0:
            raise ValueError("Los extremos de lambda deben ser > 0.")
        if lo >= hi:
            raise ValueError(
                "Para modo aleatorio el inicio ({}) debe ser "
                "menor que el fin ({}).".format(lo, hi)
            )
        return [random.uniform(lo, hi) for _ in range(count)]

    # ── modo determinista ────────────────────────────────────────────────────
    try:
        nums = [float(p) for p in pieces]
    except ValueError:
        raise ValueError(
            "Valor no numerico en '{}'.".format(spec)
        )

    if len(nums) == 1:
        if nums[0] <= 0:
            raise ValueError("Lambda debe ser > 0.")
        return [nums[0]]

    lo, hi = nums[0], nums[1]
    if lo <= 0 or hi <= 0:
        raise ValueError("Los extremos de lambda deben ser > 0.")

    step = nums[2] if len(nums) == 3 else (1.0 if lo <= hi else -1.0)
    if step == 0:
        raise ValueError("El paso no puede ser cero.")

    vals: List[float] = []
    cur = lo
    tol = abs(step) * 1e-9
    if step > 0:
        while cur <= hi + tol:
            vals.append(cur)
            cur = round(cur + step, 10)
    else:
        while cur >= hi - tol:
            vals.append(cur)
            cur = round(cur + step, 10)

    if not vals:
        raise ValueError(
            "El rango '{}' no contiene valores.".format(spec)
        )
    return vals


def parse_rates_axes(text: str) -> List[List[float]]:
    """
    Divide el texto por comas y convierte cada trozo con parse_rate_axis.
    Devuelve una lista de ejes (uno por flujo OD), cada eje con >= 1 valores.
    Usado en el modo cartesiano.
    """
    axes: List[List[float]] = []
    for raw in text.split(","):
        raw = raw.strip()
        if raw:
            axes.append(parse_rate_axis(raw))
    if not axes:
        raise ValueError("--rates no contiene ninguna especificacion.")
    return axes


def parse_rate_range(spec: str) -> Tuple[float, float]:
    """
    Extrae (lo, hi) de una especificacion para el modo --nsamples.
    El tercer campo (paso o rN) se ignora; el conteo lo da --nsamples.

        valor        -> (valor, valor)   lambda fija en todas las muestras
        lo:hi        -> (lo, hi)
        lo:hi:algo   -> (lo, hi)         tercer campo ignorado
    """
    spec = spec.strip()
    if not spec:
        raise ValueError("Especificacion de lambda vacia.")

    pieces = [p.strip() for p in spec.split(":")]
    try:
        lo = float(pieces[0])
        hi = float(pieces[1]) if len(pieces) >= 2 else lo
    except ValueError:
        raise ValueError(
            "Valor no numerico en '{}'.".format(spec)
        )

    if lo <= 0 or hi <= 0:
        raise ValueError(
            "Los extremos de lambda deben ser > 0 en '{}'.".format(spec)
        )
    if lo > hi:
        lo, hi = hi, lo  # intercambiar silenciosamente

    return (lo, hi)


def parse_rate_ranges(text: str) -> List[Tuple[float, float]]:
    """
    Divide el texto por comas y extrae (lo, hi) de cada trozo.
    Usado en el modo --nsamples.
    """
    ranges: List[Tuple[float, float]] = []
    for raw in text.split(","):
        raw = raw.strip()
        if raw:
            ranges.append(parse_rate_range(raw))
    if not ranges:
        raise ValueError("--rates no contiene ninguna especificacion.")
    return ranges


# ─── RESTO DE FUNCIONES (sin cambios) ────────────────────────────────────────

def read_input_files(path: Path) -> List[Tuple[str, str, str]]:
    rows: List[Tuple[str, str, str]] = []

    with path.open("r", encoding="utf-8-sig") as file:
        for line_number, raw_line in enumerate(file, start=1):
            line = raw_line.strip()

            if not line or line.startswith("#"):
                continue

            parts = [value.strip() for value in line.split(";")]

            if len(parts) != 3:
                raise ValueError(
                    "Linea {} invalida en '{}'. Se esperaba "
                    "indice;grafo;routing.".format(line_number, path)
                )

            rows.append((parts[0], parts[1], parts[2]))

    if not rows:
        raise ValueError("'{}' esta vacio.".format(path))

    return rows


def read_routing(path: Path, number_of_nodes: int) -> List[List[int]]:
    rows: List[List[int]] = []

    with path.open("r", encoding="utf-8-sig") as file:
        for line_number, raw_line in enumerate(file, start=1):
            line = raw_line.strip()

            if not line:
                continue

            try:
                row = [
                    int(value.strip())
                    for value in line.split(",")
                    if value.strip()
                ]
            except ValueError:
                raise ValueError(
                    "Valor no entero en '{}', linea {}.".format(
                        path,
                        line_number,
                    )
                )

            rows.append(row)

    if len(rows) != number_of_nodes:
        raise ValueError(
            "'{}' tiene {} filas; se esperaban {}.".format(
                path,
                len(rows),
                number_of_nodes,
            )
        )

    for row_number, row in enumerate(rows):
        if len(row) != number_of_nodes:
            raise ValueError(
                "'{}', fila {}, tiene {} columnas; se esperaban {}.".format(
                    path,
                    row_number,
                    len(row),
                    number_of_nodes,
                )
            )

    return rows


def build_port_map(
    graph: nx.Graph,
) -> Dict[int, Dict[int, Tuple[int, Dict[str, Any]]]]:
    """
    port_map[nodo_actual][puerto] = (siguiente_nodo, atributos_enlace)
    """
    result: Dict[int, Dict[int, Tuple[int, Dict[str, Any]]]] = {}

    if graph.is_multigraph():
        raw_edges = graph.edges(keys=True, data=True)
        edges = (
            (u, v, data)
            for u, v, _key, data in raw_edges
        )
    else:
        edges = graph.edges(data=True)

    for raw_u, raw_v, data in edges:
        u = int(raw_u)
        v = int(raw_v)

        if "port" not in data:
            raise ValueError(
                "La arista {} -> {} no contiene 'port'.".format(u, v)
            )

        if "bandwidth" not in data:
            raise ValueError(
                "La arista {} -> {} no contiene 'bandwidth'.".format(u, v)
            )

        port = int(data["port"])
        bandwidth = float(data["bandwidth"])

        if bandwidth <= 0:
            raise ValueError(
                "Bandwidth no positivo en {} -> {}.".format(u, v)
            )

        node_ports = result.setdefault(u, {})

        if port in node_ports:
            previous_destination = node_ports[port][0]
            raise ValueError(
                "Puerto {} duplicado en el nodo {} hacia {} y {}.".format(
                    port,
                    u,
                    previous_destination,
                    v,
                )
            )

        node_ports[port] = (v, data)

    return result


def load_context(
    index: str,
    graph_name: str,
    routing_name: str,
) -> Dict[str, Any]:
    graph_path = GRAPHS_DIR / graph_name
    routing_path = ROUTINGS_DIR / routing_name

    if not graph_path.is_file():
        raise FileNotFoundError(
            "No existe '{}'.".format(graph_path)
        )

    if not routing_path.is_file():
        raise FileNotFoundError(
            "No existe '{}'.".format(routing_path)
        )

    graph = nx.read_gml(str(graph_path), label=None)
    number_of_nodes = graph.number_of_nodes()

    actual_nodes = sorted(int(node) for node in graph.nodes())
    expected_nodes = list(range(number_of_nodes))

    if actual_nodes != expected_nodes:
        raise ValueError(
            "Los nodos de '{}' deben ser 0..{}. Encontrados: {}.".format(
                graph_name,
                number_of_nodes - 1,
                actual_nodes,
            )
        )

    return {
        "index": index,
        "graph_name": graph_name,
        "routing_name": routing_name,
        "graph": graph,
        "n": number_of_nodes,
        "routing": read_routing(routing_path, number_of_nodes),
        "port_map": build_port_map(graph),
    }


def reconstruct_route(
    source: int,
    destination: int,
    context: Dict[str, Any],
) -> Optional[Tuple[List[int], List[QueueId]]]:
    number_of_nodes = context["n"]

    if source == destination:
        return None

    if not (
        0 <= source < number_of_nodes
        and 0 <= destination < number_of_nodes
    ):
        return None

    node_path = [source]
    queue_path: List[QueueId] = []
    visited = {source}
    current = source

    for _ in range(number_of_nodes):
        port = context["routing"][current][destination]

        if port == -1:
            return None

        next_information = context["port_map"].get(
            current,
            {},
        ).get(port)

        if next_information is None:
            return None

        next_node, _attributes = next_information

        queue_path.append((current, next_node, port))
        node_path.append(next_node)

        if next_node == destination:
            return node_path, queue_path

        if next_node in visited:
            return None

        visited.add(next_node)
        current = next_node

    return None


def valid_pairs(context: Dict[str, Any]) -> List[Pair]:
    pairs: List[Pair] = []

    for source in range(context["n"]):
        for destination in range(context["n"]):
            if source == destination:
                continue

            if reconstruct_route(
                source,
                destination,
                context,
            ) is not None:
                pairs.append((source, destination))

    return pairs


def common_valid_pairs(
    valid_pairs_by_simulation: Sequence[Sequence[Pair]],
) -> List[Pair]:
    """
    Obtiene pares validos en TODAS las simulaciones de input_files.txt.
    Asi se usan los mismos flujos y las mismas lambdas en todas ellas.
    """
    if not valid_pairs_by_simulation:
        return []

    common: Set[Pair] = set(valid_pairs_by_simulation[0])

    for simulation_pairs in valid_pairs_by_simulation[1:]:
        common.intersection_update(simulation_pairs)

    return sorted(common)


def parse_pairs(text: str) -> List[Pair]:
    pairs: List[Pair] = []
    seen: Set[Pair] = set()

    for raw_item in text.split(","):
        item = raw_item.strip()

        if not item:
            continue

        parts = item.split(":")

        if len(parts) != 2:
            raise ValueError(
                "Par invalido '{}'. Use origen:destino.".format(item)
            )

        try:
            source = int(parts[0])
            destination = int(parts[1])
        except ValueError:
            raise ValueError(
                "Par no entero '{}'.".format(item)
            )

        if source == destination:
            raise ValueError(
                "Origen y destino no pueden coincidir en {}:{}.".format(
                    source,
                    destination,
                )
            )

        pair = (source, destination)

        if pair in seen:
            raise ValueError(
                "Par repetido {}:{}.".format(source, destination)
            )

        seen.add(pair)
        pairs.append(pair)

    if not pairs:
        raise ValueError("No se indico ningun par OD.")

    return pairs


def ask_num_pairs(maximum: int) -> int:
    while True:
        raw_value = input(
            "Cuantos pares origen-destino quieres generar? "
            "(1-{}): ".format(maximum)
        ).strip()

        try:
            number = int(raw_value)
        except ValueError:
            print("Introduce un numero entero.")
            continue

        if 1 <= number <= maximum:
            return number

        print(
            "El valor debe estar entre 1 y {}.".format(maximum)
        )


def ask_flow_rates(
    pairs: Sequence[Pair],
    default_lambda: Optional[float],
) -> List[float]:
    rates: List[float] = []

    print("")
    print("Introduce la lambda de entrada de cada flujo en paquetes/s.")

    for source, destination in pairs:
        while True:
            if default_lambda is None:
                prompt = "  lambda_in {} -> {}: ".format(
                    source,
                    destination,
                )
            else:
                prompt = (
                    "  lambda_in {} -> {} [{}]: ".format(
                        source,
                        destination,
                        fmt(default_lambda),
                    )
                )

            raw_value = input(prompt).strip()

            if not raw_value and default_lambda is not None:
                rate = default_lambda
            else:
                try:
                    rate = float(raw_value)
                except ValueError:
                    print("  Introduce un numero valido.")
                    continue

            if rate <= 0:
                print("  La lambda debe ser mayor que 0.")
                continue

            rates.append(rate)
            break

    return rates


def solve_one_flow(
    queue_path: List[QueueId],
    external_rate: float,
) -> Dict[QueueId, float]:
    """
    Resuelve lambda = gamma + P^T lambda para una ruta determinista.
    """
    number_of_queues = len(queue_path)

    gamma = np.zeros(number_of_queues, dtype=float)
    gamma[0] = external_rate

    transition = np.zeros(
        (number_of_queues, number_of_queues),
        dtype=float,
    )

    for position in range(number_of_queues - 1):
        transition[position, position + 1] = 1.0

    internal_rates = np.linalg.solve(
        np.eye(number_of_queues) - transition.T,
        gamma,
    )

    return {
        queue_path[position]: float(internal_rates[position])
        for position in range(number_of_queues)
    }


def queue_bandwidth(
    context: Dict[str, Any],
    queue_id: QueueId,
) -> float:
    source, _destination, port = queue_id
    return float(
        context["port_map"][source][port][1]["bandwidth"]
    )


def solve_jackson(
    context: Dict[str, Any],
    flows: List[Dict[str, Any]],
    avg_packet_size: float,
) -> Tuple[Dict[QueueId, Dict[str, float]], bool, float]:
    total_arrival: Dict[QueueId, float] = {}

    for flow in flows:
        internal_rates = solve_one_flow(
            flow["queues"],
            flow["rate"],
        )

        flow["internal"] = internal_rates

        for queue_id, rate in internal_rates.items():
            total_arrival[queue_id] = (
                total_arrival.get(queue_id, 0.0)
                + rate
            )

    statistics: Dict[QueueId, Dict[str, float]] = {}
    stable = True
    maximum_utilization = 0.0

    for queue_id, arrival_rate in total_arrival.items():
        service_rate = (
            queue_bandwidth(context, queue_id)
            / avg_packet_size
        )
        utilization = arrival_rate / service_rate
        maximum_utilization = max(
            maximum_utilization,
            utilization,
        )

        if utilization >= 1.0:
            stable = False
            mean_number = float("inf")
            mean_queue_number = float("inf")
            mean_system_time = float("inf")
            mean_waiting_time = float("inf")
        else:
            mean_number = utilization / (1.0 - utilization)
            mean_queue_number = (
                utilization * utilization
                / (1.0 - utilization)
            )
            mean_system_time = 1.0 / (
                service_rate - arrival_rate
            )
            mean_waiting_time = (
                utilization
                / (service_rate - arrival_rate)
            )

        statistics[queue_id] = {
            "lambda": arrival_rate,
            "mu": service_rate,
            "rho": utilization,
            "L": mean_number,
            "Lq": mean_queue_number,
            "W": mean_system_time,
            "Wq": mean_waiting_time,
        }

    for flow in flows:
        flow["delay"] = sum(
            statistics[queue_id]["W"]
            for queue_id in flow["queues"]
        )

    return statistics, stable, maximum_utilization


def flow_block(
    packet_rate: float,
    avg_packet_size: float,
    packet_size_1: int,
    packet_size_2: int,
) -> str:
    equivalent_lambda = packet_rate * avg_packet_size

    return ",".join(
        [
            str(TIME_DIST),
            fmt(equivalent_lambda),
            fmt(packet_rate),
            fmt(EXP_MAX_FACTOR),
            str(SIZE_DIST),
            fmt(avg_packet_size),
            str(packet_size_1),
            str(packet_size_2),
            str(TOS),
        ]
    )


def build_traffic_line(
    number_of_nodes: int,
    flows: List[Dict[str, Any]],
    avg_packet_size: float,
    packet_size_1: int,
    packet_size_2: int,
) -> str:
    flow_by_pair = {
        (flow["src"], flow["dst"]): flow
        for flow in flows
    }

    blocks: List[str] = []

    for source in range(number_of_nodes):
        for destination in range(number_of_nodes):
            flow = flow_by_pair.get((source, destination))

            if flow is None:
                blocks.append(NO_FLOW)
            else:
                blocks.append(
                    flow_block(
                        flow["rate"],
                        avg_packet_size,
                        packet_size_1,
                        packet_size_2,
                    )
                )

    total_external_lambda = sum(
        flow["rate"]
        for flow in flows
    )

    return (
        fmt(total_external_lambda)
        + "|"
        + ";".join(blocks)
        + ";"
    )


def main() -> None:
    args = parse_args()

    try:
        if args.pkt1_size <= 0 or args.pkt2_size <= 0:
            raise ValueError(
                "Los tamanos de paquete deben ser positivos."
            )

        if args.pairs is not None and args.num_pairs is not None:
            raise ValueError(
                "No combine --pairs y --num-pairs."
            )

        if args.rates is not None and args.pairs is None:
            raise ValueError(
                "--rates requiere indicar tambien --pairs."
            )

        if args.nsamples is not None and args.rates is None:
            raise ValueError(
                "--nsamples requiere tambien --rates con rangos lo:hi por flujo."
            )

        if args.nsamples is not None and args.nsamples < 1:
            raise ValueError("--nsamples debe ser >= 1.")

        if not INPUT_FILES.is_file():
            raise FileNotFoundError(str(INPUT_FILES))

        if not GRAPHS_DIR.is_dir():
            raise FileNotFoundError(str(GRAPHS_DIR))

        if not ROUTINGS_DIR.is_dir():
            raise FileNotFoundError(str(ROUTINGS_DIR))

        # Semilla para reproducibilidad
        if args.seed is not None:
            random.seed(args.seed)

        entries = read_input_files(INPUT_FILES)
        contexts = [
            load_context(*entry)
            for entry in entries
        ]

        valid_by_simulation = [
            valid_pairs(context)
            for context in contexts
        ]

        available_pairs = common_valid_pairs(
            valid_by_simulation
        )

        if not available_pairs:
            raise ValueError(
                "No existe ningun par OD valido en todas las simulaciones."
            )

        if args.pairs is not None:
            selected_pairs = parse_pairs(args.pairs)
            available_set = set(available_pairs)

            invalid_pairs = [
                pair
                for pair in selected_pairs
                if pair not in available_set
            ]

            if invalid_pairs:
                raise ValueError(
                    "Pares no validos en todas las simulaciones: {}."
                    .format(invalid_pairs)
                )
        else:
            if args.num_pairs is None:
                pair_count = ask_num_pairs(
                    len(available_pairs)
                )
            else:
                pair_count = args.num_pairs

            if not 1 <= pair_count <= len(available_pairs):
                raise ValueError(
                    "El numero de pares debe estar entre 1 y {}.".format(
                        len(available_pairs)
                    )
                )

            random_generator = random.Random(args.seed if args.seed is not None else 1234)
            selected_pairs = random_generator.sample(
                available_pairs,
                pair_count,
            )

        avg_packet_size = (
            args.pkt1_size + args.pkt2_size
        ) / 2.0

        output_lines: List[str] = []
        unstable_count = 0
        combo_number = 0

        if args.nsamples is not None:
            # ── Modo muestreo aleatorio total ─────────────────────────────────
            rate_ranges = parse_rate_ranges(args.rates)

            if len(rate_ranges) != len(selected_pairs):
                raise ValueError(
                    "Se indicaron {} pares y {} especificaciones de lambda. "
                    "Debe haber exactamente una por flujo.".format(
                        len(selected_pairs),
                        len(rate_ranges),
                    )
                )

            total_combinations = args.nsamples
            print("")
            print(
                "Modo aleatorio: {} muestras independientes."
                .format(total_combinations)
            )

            rate_tuples = (
                tuple(
                    random.uniform(lo, hi) if lo != hi else lo
                    for lo, hi in rate_ranges
                )
                for _ in range(args.nsamples)
            )

        else:
            # ── Modo producto cartesiano (comportamiento original) ─────────────
            if args.rates is not None:
                rate_axes = parse_rates_axes(args.rates)

                if len(rate_axes) != len(selected_pairs):
                    raise ValueError(
                        "Se indicaron {} pares y {} especificaciones de lambda. "
                        "Debe haber exactamente una por flujo.".format(
                            len(selected_pairs),
                            len(rate_axes),
                        )
                    )
            else:
                single_rates = ask_flow_rates(selected_pairs, None)
                rate_axes = [[r] for r in single_rates]

            total_combinations = 1
            for axis in rate_axes:
                total_combinations *= len(axis)

            if total_combinations > 1:
                print("")
                print(
                    "Producto cartesiano: {} combinaciones de lambdas."
                    .format(total_combinations)
                )

            rate_tuples = itertools.product(*rate_axes)

        # ── Bucle compartido por ambos modos ──────────────────────────────────
        for rates_tuple in rate_tuples:
            selected_rates = list(rates_tuple)
            combo_number += 1

            total_external_lambda = sum(selected_rates)

            print("")
            if total_combinations > 1:
                print(
                    "Combinacion {}/{}: lambdas = [{}]".format(
                        combo_number,
                        total_combinations,
                        ", ".join(fmt(r, 9) for r in selected_rates),
                    )
                )
            else:
                print("Flujos externos seleccionados:")

            for pair, rate in zip(
                selected_pairs,
                selected_rates,
            ):
                print(
                    "  {} -> {} | lambda_in={} pkt/s".format(
                        pair[0],
                        pair[1],
                        fmt(rate, 9),
                    )
                )

            print(
                "Lambda externa total: {} pkt/s".format(
                    fmt(total_external_lambda, 9)
                )
            )
            print("")

            for context in contexts:
                flows: List[Dict[str, Any]] = []

                for pair, rate in zip(
                    selected_pairs,
                    selected_rates,
                ):
                    source, destination = pair

                    route = reconstruct_route(
                        source,
                        destination,
                        context,
                    )

                    if route is None:
                        raise ValueError(
                            "No se pudo reconstruir {} -> {} en la "
                            "simulacion {}.".format(
                                source,
                                destination,
                                context["index"],
                            )
                        )

                    node_path, queue_path = route

                    flows.append(
                        {
                            "src": source,
                            "dst": destination,
                            "rate": rate,
                            "nodes": node_path,
                            "queues": queue_path,
                        }
                    )

                statistics, stable, maximum_utilization = solve_jackson(
                    context,
                    flows,
                    avg_packet_size,
                )

                if not stable:
                    unstable_count += 1

                output_lines.append(
                    build_traffic_line(
                        context["n"],
                        flows,
                        avg_packet_size,
                        args.pkt1_size,
                        args.pkt2_size,
                    )
                )

                print(
                    "[{}] Simulacion {}: {} flujos, rho_max={}".format(
                        "OK" if stable else "INESTABLE",
                        context["index"],
                        len(flows),
                        fmt(maximum_utilization, 9),
                    )
                )

        with OUTPUT_FILE.open(
            "w",
            encoding="utf-8",
            newline="",
        ) as output_file:
            for line in output_lines:
                output_file.write(line + "\n")

        print("")
        print("[OK] {}".format(OUTPUT_FILE))

        if unstable_count:
            print(
                "[!] {} de {} simulaciones son inestables.".format(
                    unstable_count,
                    len(output_lines),
                )
            )

    except (
        OSError,
        ValueError,
        nx.NetworkXError,
        np.linalg.LinAlgError,
    ) as error:
        fail(str(error))


if __name__ == "__main__":
    main()