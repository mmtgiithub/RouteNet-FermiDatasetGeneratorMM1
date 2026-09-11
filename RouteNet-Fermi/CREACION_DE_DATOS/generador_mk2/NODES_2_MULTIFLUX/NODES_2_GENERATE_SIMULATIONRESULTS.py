#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
NODES_2_GENERATE_SIMULATIONRESULTS_NODE_MODEL_V3.py

Genera ../simulationResults.txt para RouteNet-Fermi/DatanetAPI a partir de:

    ../input_files.txt
    ../graphs/
    ../routings/
    ../traffic.txt

Admite cualquier número de flujos OD activos.

AJUSTE FINO DEL MODELO
----------------------
- La red original no se supone estrictamente M/M/1; se aproxima cada
  recurso de nodo mediante una cola M/M/1 para generar el target.
- Hay una única cola M/M/1 compartida por nodo/recurso.
- La lambda de un nodo es la suma de las tasas de todos los flujos que
  todavía deben salir de ese nodo.
- El nodo destino no añade delay: para una ruta [n0, ..., nd] solo se
  cuentan los nodos de path[:-1].
- El delay de cada nodo es D_i = 1 / (mu_i - lambda_i).
- El delay extremo a extremo es la suma de los delays de los nodos
  recorridos antes del destino.
- Como el GML guarda bandwidth por enlace, todos los enlaces salientes de
  un mismo nodo deben tener el mismo bandwidth para definir un único mu_i.

Para cada flujo activo genera 11 campos:

    avg_bw_kbps,
    pkts_generated,
    pkts_dropped,
    avg_delay,
    avg_ln_delay,
    p10,
    p20,
    p50,
    p80,
    p90,
    jitter

Cada bloque sin flujo:

    0,0,0,-1,-1,-1,-1,-1,-1,-1,-1

Formato global de cada simulación:

    global_packets,global_losses,global_delay|bloque_0;...;bloque_N2-1;

IMPORTANTE SOBRE EL FINAL DE LÍNEA
---------------------------------
Cada línea termina exactamente en ";\n". La DatanetAPI antigua elimina
los dos últimos caracteres de simulationResults.txt mediante [:-2].

Compatible con Python 3.8.
"""

from __future__ import annotations

import math
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

import networkx as nx


# Rutas relativas al directorio que contiene este script.
SCRIPT_DIR = Path(__file__).resolve().parent
BASE_DIR = SCRIPT_DIR.parent

INPUT_FILES = BASE_DIR / "input_files.txt"
GRAPHS_DIR = BASE_DIR / "graphs"
ROUTINGS_DIR = BASE_DIR / "routings"
TRAFFIC_FILE = BASE_DIR / "traffic.txt"
OUTPUT_FILE = BASE_DIR / "simulationResults.txt"

MODEL_ID = "NODE_MM1_DESTINATION_EXCLUDED_V3"

NO_FLOW = "0,0,0,-1,-1,-1,-1,-1,-1,-1,-1"

EULER_GAMMA = 0.5772156649015329
PERCENTILES = (0.10, 0.20, 0.50, 0.80, 0.90)

# Si rho >= 1, la M/M/1 no tiene estado estacionario.
# Para mantener un TXT finito se calcula el delay con rho=0.99 y se
# representan pérdidas mediante el exceso de carga.
RHO_DELAY_CAP = 0.99

@dataclass
class Flow:
    src: int
    dst: int
    bit_rate: float             # bits/s
    packet_rate: float          # paquetes/s
    avg_packet_size: float      # bits
    path: List[int] = field(default_factory=list)


@dataclass
class NodeLoad:
    node: int
    bandwidth: float            # bits/s del recurso del nodo
    packet_rate: float = 0.0
    bit_rate: float = 0.0
    flow_count: int = 0

    @property
    def avg_packet_size(self) -> float:
        if self.packet_rate <= 0:
            return 0.0
        return self.bit_rate / self.packet_rate

    @property
    def service_rate(self) -> float:
        """mu en paquetes/s."""
        avg_size = self.avg_packet_size

        if avg_size <= 0:
            return math.inf

        return self.bandwidth / avg_size

    @property
    def rho(self) -> float:
        mu = self.service_rate

        if not math.isfinite(mu) or mu <= 0:
            return 0.0

        return self.packet_rate / mu

    @property
    def loss_fraction(self) -> float:
        """
        En una M/M/1 estable no se introducen pérdidas.

        Si lambda >= mu:
            loss = 1 - mu/lambda
        """
        lam = self.packet_rate
        mu = self.service_rate

        if lam <= 0 or not math.isfinite(mu):
            return 0.0

        if mu <= 0:
            return 1.0

        if lam < mu:
            return 0.0

        return min(1.0, max(0.0, 1.0 - mu / lam))

    @property
    def mean_delay(self) -> float:
        """
        Tiempo medio de sistema M/M/1:

            W = 1 / (mu - lambda)

        En caso inestable se usa lambda_efectiva = 0.99*mu para obtener
        un valor finito y se informa de la inestabilidad.
        """
        mu = self.service_rate
        lam = self.packet_rate

        if not math.isfinite(mu):
            return 0.0

        if mu <= 0:
            raise ValueError(
                "El nodo {} tiene una tasa de servicio no positiva."
                .format(self.node)
            )

        if lam >= mu:
            lam = RHO_DELAY_CAP * mu

        return 1.0 / (mu - lam)


def fmt(value: float, decimals: int = 9) -> str:
    if not math.isfinite(value):
        raise ValueError(
            "No se puede escribir un valor no finito: {!r}.".format(value)
        )

    text = ("{0:." + str(decimals) + "f}").format(value)
    text = text.rstrip("0").rstrip(".")
    return text if text else "0"


def fail(message: str) -> None:
    print("[!] {}".format(message), file=sys.stderr)
    raise SystemExit(1)


def read_input_entries(path: Path) -> List[Tuple[str, str, str]]:
    entries: List[Tuple[str, str, str]] = []

    try:
        with path.open("r", encoding="utf-8-sig") as file:
            for line_number, raw_line in enumerate(file, start=1):
                line = raw_line.strip()

                if not line or line.startswith("#"):
                    continue

                parts = [part.strip() for part in line.split(";")]

                if len(parts) != 3:
                    raise ValueError(
                        "Línea {} mal formada en '{}'. Se esperaba "
                        "indice;graph_file;routing_file.".format(
                            line_number,
                            path,
                        )
                    )

                index, graph_name, routing_name = parts

                if not index or not graph_name or not routing_name:
                    raise ValueError(
                        "Línea {} incompleta en '{}'.".format(
                            line_number,
                            path,
                        )
                    )

                entries.append((index, graph_name, routing_name))

    except OSError as error:
        raise OSError(
            "No se pudo leer '{}': {}.".format(path, error)
        )

    if not entries:
        raise ValueError(
            "'{}' no contiene simulaciones válidas.".format(path)
        )

    return entries


def read_routing(path: Path, number_of_nodes: int) -> List[List[int]]:
    routing: List[List[int]] = []

    try:
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
                        "Valor no entero en '{}', línea {}.".format(
                            path,
                            line_number,
                        )
                    )

                routing.append(row)

    except OSError as error:
        raise OSError(
            "No se pudo leer '{}': {}.".format(path, error)
        )

    if len(routing) != number_of_nodes:
        raise ValueError(
            "'{}' tiene {} filas; se esperaban {}.".format(
                path,
                len(routing),
                number_of_nodes,
            )
        )

    for row_number, row in enumerate(routing):
        if len(row) != number_of_nodes:
            raise ValueError(
                "'{}', fila {}, tiene {} columnas; se esperaban {}."
                .format(
                    path,
                    row_number,
                    len(row),
                    number_of_nodes,
                )
            )

    return routing


def iter_edges(
    graph: nx.Graph,
) -> Iterable[Tuple[int, int, Dict[str, Any]]]:
    if graph.is_multigraph():
        return (
            (int(u), int(v), data)
            for u, v, _key, data in graph.edges(
                keys=True,
                data=True,
            )
        )

    return (
        (int(u), int(v), data)
        for u, v, data in graph.edges(data=True)
    )


def build_port_map(
    graph: nx.Graph,
) -> Dict[int, Dict[int, Tuple[int, Dict[str, Any]]]]:
    """
    port_map[nodo_actual][puerto] =
        (siguiente_nodo, atributos_del_enlace)
    """
    port_map: Dict[
        int,
        Dict[int, Tuple[int, Dict[str, Any]]]
    ] = {}

    for u, v, data in iter_edges(graph):
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
                "El enlace {} -> {} tiene bandwidth={}.".format(
                    u,
                    v,
                    bandwidth,
                )
            )

        node_ports = port_map.setdefault(u, {})

        if port in node_ports:
            previous_destination = node_ports[port][0]

            raise ValueError(
                "El nodo {} tiene el puerto {} asociado a {} y {}."
                .format(
                    u,
                    port,
                    previous_destination,
                    v,
                )
            )

        node_ports[port] = (v, data)

    return port_map


def reconstruct_path(
    flow: Flow,
    routing: List[List[int]],
    port_map: Dict[int, Dict[int, Tuple[int, Dict[str, Any]]]],
    number_of_nodes: int,
) -> None:
    if flow.src == flow.dst:
        raise ValueError(
            "El flujo {} -> {} está en la diagonal.".format(
                flow.src,
                flow.dst,
            )
        )

    current = flow.src
    visited = {current}
    path = [current]

    for _ in range(number_of_nodes):
        port = routing[current][flow.dst]

        if port == -1:
            raise ValueError(
                "La ruta {} -> {} termina en el nodo {}.".format(
                    flow.src,
                    flow.dst,
                    current,
                )
            )

        next_information = port_map.get(current, {}).get(port)

        if next_information is None:
            raise ValueError(
                "El routing utiliza el puerto {} del nodo {}, pero "
                "ese puerto no existe en el GML.".format(port, current)
            )

        next_node, _data = next_information

        if next_node in visited:
            raise ValueError(
                "Bucle en la ruta {} -> {}: {}.".format(
                    flow.src,
                    flow.dst,
                    path + [next_node],
                )
            )

        path.append(next_node)

        if next_node == flow.dst:
            flow.path = path
            return

        visited.add(next_node)
        current = next_node

    raise ValueError(
        "No se completó la ruta {} -> {}.".format(flow.src, flow.dst)
    )


def parse_traffic_line(
    line: str,
    number_of_nodes: int,
) -> Tuple[float, List[Flow]]:
    if "|" not in line:
        raise ValueError(
            "La línea de traffic.txt no contiene '|'."
        )

    header, payload = line.strip().split("|", maxsplit=1)

    try:
        declared_total_rate = float(header)
    except ValueError:
        raise ValueError(
            "La lambda total no es numérica: {!r}.".format(header)
        )

    blocks = payload.split(";")

    while blocks and not blocks[-1].strip():
        blocks.pop()

    expected_blocks = number_of_nodes * number_of_nodes

    if len(blocks) != expected_blocks:
        raise ValueError(
            "traffic.txt contiene {} bloques; se esperaban {} para "
            "{} nodos.".format(
                len(blocks),
                expected_blocks,
                number_of_nodes,
            )
        )

    flows: List[Flow] = []

    for position, raw_block in enumerate(blocks):
        fields = [
            field.strip()
            for field in raw_block.split(",")
        ]

        src = position // number_of_nodes
        dst = position % number_of_nodes

        if not fields or fields[0] == "-1":
            continue

        if src == dst:
            raise ValueError(
                "Hay tráfico activo en la diagonal ({},{}).".format(
                    src,
                    dst,
                )
            )

        if len(fields) < 3:
            raise ValueError(
                "Bloque ({},{}) incompleto: {!r}.".format(
                    src,
                    dst,
                    raw_block,
                )
            )

        try:
            bit_rate = float(fields[1])
            packet_rate = float(fields[2])
        except ValueError:
            raise ValueError(
                "Tasas no numéricas en el flujo {} -> {}.".format(
                    src,
                    dst,
                )
            )

        if bit_rate <= 0 or packet_rate <= 0:
            raise ValueError(
                "El flujo {} -> {} debe tener tasas positivas.".format(
                    src,
                    dst,
                )
            )

        avg_packet_size = bit_rate / packet_rate

        if avg_packet_size <= 0:
            raise ValueError(
                "Tamaño medio no positivo en {} -> {}.".format(src, dst)
            )

        flows.append(
            Flow(
                src=src,
                dst=dst,
                bit_rate=bit_rate,
                packet_rate=packet_rate,
                avg_packet_size=avg_packet_size,
            )
        )

    if not flows:
        raise ValueError(
            "La línea de traffic.txt no contiene flujos activos."
        )

    calculated_total_rate = sum(
        flow.packet_rate
        for flow in flows
    )

    if not math.isclose(
        declared_total_rate,
        calculated_total_rate,
        rel_tol=1e-6,
        abs_tol=1e-6,
    ):
        raise ValueError(
            "La lambda total declarada ({}) no coincide con la suma "
            "de las lambdas de los flujos ({}).".format(
                fmt(declared_total_rate),
                fmt(calculated_total_rate),
            )
        )

    return declared_total_rate, flows


def node_bandwidth(
    graph: nx.Graph,
    node: int,
) -> float:
    """
    Obtiene el bandwidth del único recurso M/M/1 asociado al nodo.

    Como el GML define bandwidth en las aristas, para aplicar una sola
    cola por nodo se exige que todos sus enlaces salientes tengan la
    misma capacidad. No se suman capacidades: el nodo se interpreta como
    un único servidor con ese bandwidth.
    """
    bandwidths: List[float] = []

    if graph.is_multigraph():
        outgoing = graph.out_edges(
            node,
            keys=True,
            data=True,
        )

        for _u, _v, _key, data in outgoing:
            if "bandwidth" not in data:
                raise ValueError(
                    "Una arista saliente del nodo {} no contiene "
                    "'bandwidth'.".format(node)
                )
            bandwidths.append(float(data["bandwidth"]))
    else:
        outgoing = graph.out_edges(
            node,
            data=True,
        )

        for _u, _v, data in outgoing:
            if "bandwidth" not in data:
                raise ValueError(
                    "Una arista saliente del nodo {} no contiene "
                    "'bandwidth'.".format(node)
                )
            bandwidths.append(float(data["bandwidth"]))

    if not bandwidths:
        raise ValueError(
            "El nodo {} no tiene enlaces salientes; no se puede definir "
            "su mu.".format(node)
        )

    if any(value <= 0 for value in bandwidths):
        raise ValueError(
            "El nodo {} tiene un bandwidth saliente no positivo."
            .format(node)
        )

    reference = bandwidths[0]

    for value in bandwidths[1:]:
        if not math.isclose(
            value,
            reference,
            rel_tol=1e-12,
            abs_tol=0.0,
        ):
            raise ValueError(
                "El modelo por nodo necesita un único mu, pero el nodo "
                "{} tiene bandwidths salientes distintos: {}."
                .format(node, bandwidths)
            )

    return reference


def build_node_loads(
    graph: nx.Graph,
    flows: List[Flow],
) -> Dict[int, NodeLoad]:
    """
    Acumula la carga por nodo.

    Cada flujo aporta su lambda a todos los nodos de path[:-1]. El nodo
    destino se excluye porque al llegar a él el flujo termina y no espera
    para una etapa posterior.
    """
    loads: Dict[int, NodeLoad] = {}

    for flow in flows:
        for node in flow.path[:-1]:
            if node not in loads:
                loads[node] = NodeLoad(
                    node=node,
                    bandwidth=node_bandwidth(graph, node),
                )

            load = loads[node]
            load.packet_rate += flow.packet_rate
            load.bit_rate += flow.bit_rate
            load.flow_count += 1

    return loads

def percentile_exponential(
    mean_delay: float,
    probability: float,
) -> float:
    return -mean_delay * math.log(1.0 - probability)


def flow_metrics(
    flow: Flow,
    node_loads: Dict[int, NodeLoad],
) -> Tuple[str, float, float, float]:
    """
    Devuelve:
        bloque,
        paquetes_generados_por_u.t.,
        paquetes_perdidos_por_u.t.,
        delay_medio
    """
    node_delays: List[float] = []
    survival_probability = 1.0

    for node in flow.path[:-1]:
        load = node_loads[node]
        node_delays.append(load.mean_delay)
        survival_probability *= 1.0 - load.loss_fraction

    avg_delay = sum(node_delays)

    if avg_delay <= 0:
        avg_delay = 1e-12

    loss_fraction = 1.0 - survival_probability
    packets_generated = flow.packet_rate
    packets_dropped = packets_generated * loss_fraction

    # DatanetAPI multiplica este campo por 1000 al leerlo.
    # Por tanto, debe escribirse en kbps.
    avg_bw_kbps = flow.bit_rate / 1000.0

    # Aproximación del delay extremo a extremo por una exponencial
    # con la misma media.
    avg_ln_delay = math.log(avg_delay) - EULER_GAMMA

    percentiles = [
        percentile_exponential(avg_delay, probability)
        for probability in PERCENTILES
    ]

    # Se conserva la convención del generador anterior:
    # suma de varianzas de nodos M/M/1. El campo se denomina
    # Jitter en DatanetAPI, aunque dimensionalmente es una varianza.
    jitter = sum(
        node_delay * node_delay
        for node_delay in node_delays
    )

    values = [
        avg_bw_kbps,
        packets_generated,
        packets_dropped,
        avg_delay,
        avg_ln_delay,
        percentiles[0],
        percentiles[1],
        percentiles[2],
        percentiles[3],
        percentiles[4],
        jitter,
    ]

    return (
        ",".join(fmt(value) for value in values),
        packets_generated,
        packets_dropped,
        avg_delay,
    )


def process_simulation(
    index: str,
    graph_name: str,
    routing_name: str,
    traffic_line: str,
) -> Tuple[
    str,
    List[Flow],
    Dict[int, NodeLoad],
    int,
    float,
    float,
    float,
]:
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

    if number_of_nodes < 2:
        raise ValueError(
            "El grafo '{}' necesita al menos dos nodos.".format(
                graph_name
            )
        )

    actual_nodes = sorted(int(node) for node in graph.nodes())
    expected_nodes = list(range(number_of_nodes))

    if actual_nodes != expected_nodes:
        raise ValueError(
            "Los nodos de '{}' deben ser 0..{}. Encontrados: {}."
            .format(
                graph_name,
                number_of_nodes - 1,
                actual_nodes,
            )
        )

    routing = read_routing(routing_path, number_of_nodes)
    port_map = build_port_map(graph)
    _declared_total_rate, flows = parse_traffic_line(
        traffic_line,
        number_of_nodes,
    )

    for flow in flows:
        reconstruct_path(
            flow,
            routing,
            port_map,
            number_of_nodes,
        )

    node_loads = build_node_loads(graph, flows)

    unstable_loads = [
        load
        for load in node_loads.values()
        if load.rho >= 1.0
    ]

    result_by_pair: Dict[Tuple[int, int], str] = {}

    total_packets = 0.0
    total_losses = 0.0
    weighted_delay = 0.0

    for flow in flows:
        block, generated, dropped, avg_delay = flow_metrics(
            flow,
            node_loads,
        )

        result_by_pair[(flow.src, flow.dst)] = block
        total_packets += generated
        total_losses += dropped
        weighted_delay += generated * avg_delay

    global_delay = (
        weighted_delay / total_packets
        if total_packets > 0
        else 0.0
    )

    blocks: List[str] = []

    for src in range(number_of_nodes):
        for dst in range(number_of_nodes):
            blocks.append(
                result_by_pair.get((src, dst), NO_FLOW)
            )

    header = ",".join(
        [
            fmt(total_packets),
            fmt(total_losses),
            fmt(global_delay),
        ]
    )

    line = header + "|" + ";".join(blocks) + ";"

    return (
        line,
        flows,
        node_loads,
        len(unstable_loads),
        total_packets,
        total_losses,
        global_delay,
    )


def main() -> None:
    print("=" * 72)
    print("MODELO ACTIVO: {}".format(MODEL_ID))
    print("SCRIPT: {}".format(Path(__file__).resolve()))
    print("TRAFFIC: {}".format(TRAFFIC_FILE.resolve()))
    print("SALIDA: {}".format(OUTPUT_FILE.resolve()))
    print("REGLA: cada flujo carga todos los nodos de path[:-1]")
    print("=" * 72)

    if len(sys.argv) != 1:
        print(
            "Uso: python NODES_2_GENERATE_SIMULATIONRESULTS_NODE_MM1_CORRECTED.py",
            file=sys.stderr,
        )
        raise SystemExit(2)

    required = [
        (INPUT_FILES, "input_files.txt", False),
        (GRAPHS_DIR, "graphs", True),
        (ROUTINGS_DIR, "routings", True),
        (TRAFFIC_FILE, "traffic.txt", False),
    ]

    for path, description, is_directory in required:
        exists = path.is_dir() if is_directory else path.is_file()

        if not exists:
            fail(
                "No se encontró {} en '{}'.".format(
                    description,
                    path,
                )
            )

    try:
        entries = read_input_entries(INPUT_FILES)

        with TRAFFIC_FILE.open(
            "r",
            encoding="utf-8-sig",
        ) as file:
            traffic_lines = [
                line.strip()
                for line in file
                if line.strip()
            ]

        if len(entries) != len(traffic_lines):
            raise ValueError(
                "input_files.txt tiene {} simulaciones y traffic.txt "
                "tiene {} líneas.".format(
                    len(entries),
                    len(traffic_lines),
                )
            )

        output_lines: List[str] = []
        total_unstable_queues = 0

        for (
            index,
            graph_name,
            routing_name,
        ), traffic_line in zip(entries, traffic_lines):
            (
                output_line,
                flows,
                node_loads,
                unstable_queues,
                total_packets,
                total_losses,
                global_delay,
            ) = process_simulation(
                index,
                graph_name,
                routing_name,
                traffic_line,
            )

            output_lines.append(output_line)
            total_unstable_queues += unstable_queues

            print(
                "[{}] Simulación {} | {} flujo(s) | {} nodo(s) con cola cargada | "
                "global_packets={} | global_losses={} | global_delay={}"
                .format(
                    "OK" if unstable_queues == 0 else "INESTABLE",
                    index,
                    len(flows),
                    len(node_loads),
                    fmt(total_packets),
                    fmt(total_losses),
                    fmt(global_delay),
                )
            )

            for flow in flows:
                flow_delay = sum(
                    node_loads[node].mean_delay
                    for node in flow.path[:-1]
                )

                print(
                    "     {} -> {} | lambda_in={} pkt/s | "
                    "bw={} kbps | ruta {} | nodos_contados={} | delay={}"
                    .format(
                        flow.src,
                        flow.dst,
                        fmt(flow.packet_rate),
                        fmt(flow.bit_rate / 1000.0),
                        " -> ".join(
                            str(node)
                            for node in flow.path
                        ),
                        list(flow.path[:-1]),
                        fmt(flow_delay),
                    )
                )

            for node in sorted(node_loads):
                load = node_loads[node]

                print(
                    "       nodo {} | {} flujo(s) | lambda_total={} | "
                    "mu={} | rho={} | W={}"
                    .format(
                        load.node,
                        load.flow_count,
                        fmt(load.packet_rate),
                        fmt(load.service_rate),
                        fmt(load.rho),
                        fmt(load.mean_delay),
                    )
                )

        with OUTPUT_FILE.open(
            "w",
            encoding="utf-8",
            newline="",
        ) as file:
            for output_line in output_lines:
                # Debe terminar exactamente en ";\n".
                file.write(output_line + "\n")

    except (
        OSError,
        ValueError,
        nx.NetworkXError,
    ) as error:
        fail(str(error))

    print("")
    print(
        "[OK] {} líneas escritas en '{}'.".format(
            len(output_lines),
            OUTPUT_FILE,
        )
    )

    if total_unstable_queues:
        print(
            "[!] Se detectaron {} colas de nodo inestables. Para ellas se usó "
            "rho={} al calcular el delay y pérdidas por exceso de carga."
            .format(
                total_unstable_queues,
                RHO_DELAY_CAP,
            )
        )


if __name__ == "__main__":
    main()