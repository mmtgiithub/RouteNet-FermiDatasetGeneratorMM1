#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
NODES_2_GENERATE_LINK_USAGE.py

Genera ../linkUsage.txt para RouteNet-Fermi/DatanetAPI_2 a partir de:

    ../input_files.txt
    ../graphs/
    ../routings/
    ../traffic.txt

El programa admite CUALQUIER número de flujos OD activos en cada línea
de traffic.txt.

Modelo:
- Una cola FIFO M/M/1 por puerto/enlace dirigido.
- Cada flujo mantiene su lambda_in a lo largo de su ruta.
- Si varios flujos usan el mismo puerto, se suman sus tasas:
      lambda_puerto = suma(lambda_f)
- También se suman sus tasas binarias:
      bit_rate_puerto = suma(lambda_f * avg_packet_size_f)
- Si dos flujos atraviesan el mismo nodo pero usan puertos distintos,
  no comparten cola.

Formato de cada bloque de linkUsage.txt compatible con la DatanetAPI antigua:

    port_util,port_loss,avg_packet_size,
    queue_util,queue_loss,avg_occupancy,max_occupancy,avg_packet_size

Todo se escribe en una sola secuencia de 8 campos separados por comas.

Para una pareja (src,dst) sin enlace directo:
    -1

Para un enlace existente sin tráfico:
    0,0,AVG_SIZE,0,0,0,0,AVG_SIZE

Las N² posiciones se separan con ';' y NO se añade ';' al final de la línea.

Compatible con Python 3.8.
"""

from __future__ import annotations

import math
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import networkx as nx


# Rutas relativas al lugar donde se encuentra este script.
SCRIPT_DIR = Path(__file__).resolve().parent
BASE_DIR = SCRIPT_DIR.parent

INPUT_FILES = BASE_DIR / "input_files.txt"
GRAPHS_DIR = BASE_DIR / "graphs"
ROUTINGS_DIR = BASE_DIR / "routings"
TRAFFIC_FILE = BASE_DIR / "traffic.txt"
OUTPUT_FILE = BASE_DIR / "linkUsage.txt"

NO_LINK = "-1"

# Se conserva la aproximación usada en los generadores anteriores.
# Si rho >= 1, la ocupación sintética se calcula con rho=0.99 para
# evitar infinitos en el TXT, mientras que las pérdidas reflejan el exceso.
RHO_OCCUPANCY_CAP = 0.99


@dataclass
class Flow:
    src: int
    dst: int
    equivalent_lambda: float       # bits/s
    packet_rate: float             # paquetes/s
    avg_packet_size: float         # bits
    path: List[int] = field(default_factory=list)
    links: List[Tuple[int, int, int]] = field(default_factory=list)


@dataclass
class LinkLoad:
    u: int
    v: int
    port: int
    bandwidth: float
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
        if self.avg_packet_size <= 0:
            return math.inf
        return self.bandwidth / self.avg_packet_size

    @property
    def rho(self) -> float:
        if not math.isfinite(self.service_rate) or self.service_rate <= 0:
            return 0.0
        return self.packet_rate / self.service_rate


def fmt(value: float, decimals: int = 6) -> str:
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

                index, graph_file, routing_file = parts

                if not index or not graph_file or not routing_file:
                    raise ValueError(
                        "Línea {} incompleta en '{}'.".format(
                            line_number,
                            path,
                        )
                    )

                entries.append((index, graph_file, routing_file))

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
                "'{}', fila {}, tiene {} columnas; se esperaban {}.".format(
                    path,
                    row_number,
                    len(row),
                    number_of_nodes,
                )
            )

    return routing


def edge_records(
    graph: nx.Graph,
    u: int,
    v: int,
) -> List[Dict[str, Any]]:
    if not graph.has_edge(u, v):
        return []

    if graph.is_multigraph():
        edge_group = graph.get_edge_data(u, v, default={})
        return list(edge_group.values())

    data = graph.get_edge_data(u, v)
    return [data] if data is not None else []


def build_port_map(
    graph: nx.Graph,
) -> Dict[int, Dict[int, Tuple[int, Dict[str, Any]]]]:
    """
    port_map[nodo][puerto] = (siguiente_nodo, atributos_del_enlace)
    """
    port_map: Dict[
        int,
        Dict[int, Tuple[int, Dict[str, Any]]]
    ] = {}

    if graph.is_multigraph():
        iterator = (
            (u, v, data)
            for u, v, _key, data in graph.edges(
                keys=True,
                data=True,
            )
        )
    else:
        iterator = graph.edges(data=True)

    for raw_u, raw_v, data in iterator:
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
                "La arista {} -> {} tiene bandwidth={}.".format(
                    u,
                    v,
                    bandwidth,
                )
            )

        node_ports = port_map.setdefault(u, {})

        if port in node_ports:
            previous_destination = node_ports[port][0]
            raise ValueError(
                "El nodo {} tiene el puerto {} asociado a {} y {}.".format(
                    u,
                    port,
                    previous_destination,
                    v,
                )
            )

        node_ports[port] = (v, data)

    return port_map


def unique_direct_edge(
    graph: nx.Graph,
    u: int,
    v: int,
) -> Optional[Tuple[int, Dict[str, Any]]]:
    """
    linkUsage.txt tiene un bloque por pareja (u,v), por lo que no puede
    representar dos enlaces paralelos distintos entre la misma pareja.
    """
    records = edge_records(graph, u, v)

    if not records:
        return None

    if len(records) > 1:
        raise ValueError(
            "Hay {} enlaces paralelos {} -> {}. linkUsage.txt solo "
            "admite uno por pareja dirigida.".format(
                len(records),
                u,
                v,
            )
        )

    data = records[0]

    if "port" not in data:
        raise ValueError(
            "La arista {} -> {} no contiene 'port'.".format(u, v)
        )

    if "bandwidth" not in data:
        raise ValueError(
            "La arista {} -> {} no contiene 'bandwidth'.".format(u, v)
        )

    return int(data["port"]), data


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

    path = [flow.src]
    links: List[Tuple[int, int, int]] = []
    visited = {flow.src}
    current = flow.src

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
                "El routing usa el puerto {} del nodo {}, pero no existe "
                "en el GML.".format(port, current)
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

        links.append((current, next_node, port))
        path.append(next_node)

        if next_node == flow.dst:
            flow.path = path
            flow.links = links
            return

        visited.add(next_node)
        current = next_node

    raise ValueError(
        "No se completó la ruta {} -> {}.".format(flow.src, flow.dst)
    )


def parse_traffic_line(
    traffic_line: str,
    number_of_nodes: int,
) -> List[Flow]:
    """
    Lee todos los bloques OD activos. Es compatible con el bloque Poisson
    actual:

        0,EqLambda,AvgPktsLambda,ExpMaxFactor,
        2,AvgPktSize,PktSize1,PktSize2,ToS

    Para linkUsage solo son necesarios EqLambda y AvgPktsLambda.
    """
    if "|" not in traffic_line:
        raise ValueError(
            "traffic.txt no contiene el separador '|'."
        )

    prefix, payload = traffic_line.strip().split("|", maxsplit=1)

    try:
        declared_total_rate = float(prefix)
    except ValueError:
        raise ValueError(
            "La lambda total de traffic.txt no es numérica: {!r}.".format(
                prefix
            )
        )

    blocks = payload.split(";")

    while blocks and not blocks[-1].strip():
        blocks.pop()

    expected_blocks = number_of_nodes * number_of_nodes

    if len(blocks) != expected_blocks:
        raise ValueError(
            "traffic.txt tiene {} bloques; se esperaban {} para {} nodos."
            .format(
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

        if len(fields) < 3:
            raise ValueError(
                "Bloque ({},{}) incompleto: {!r}.".format(
                    src,
                    dst,
                    raw_block,
                )
            )

        try:
            equivalent_lambda = float(fields[1])
            packet_rate = float(fields[2])
        except ValueError:
            raise ValueError(
                "Tasas no numéricas en el flujo {} -> {}.".format(
                    src,
                    dst,
                )
            )

        if equivalent_lambda <= 0 or packet_rate <= 0:
            raise ValueError(
                "El flujo {} -> {} debe tener tasas positivas.".format(
                    src,
                    dst,
                )
            )

        avg_packet_size = equivalent_lambda / packet_rate

        if avg_packet_size <= 0:
            raise ValueError(
                "Tamaño medio no positivo en {} -> {}.".format(src, dst)
            )

        flows.append(
            Flow(
                src=src,
                dst=dst,
                equivalent_lambda=equivalent_lambda,
                packet_rate=packet_rate,
                avg_packet_size=avg_packet_size,
            )
        )

    if not flows:
        raise ValueError("La línea de traffic.txt no contiene flujos activos.")

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
            "La lambda total declarada ({}) no coincide con la suma de "
            "las lambdas de los flujos ({}).".format(
                fmt(declared_total_rate),
                fmt(calculated_total_rate),
            )
        )

    return flows


def build_link_loads(
    graph: nx.Graph,
    flows: List[Flow],
) -> Dict[Tuple[int, int, int], LinkLoad]:
    """
    Agrega todos los flujos que usan cada puerto.

    Si 0->5 lleva 500 pkt/s y 2->8 lleva 200 pkt/s, y ambos atraviesan
    el mismo puerto q, ese puerto recibe 700 pkt/s.
    """
    loads: Dict[Tuple[int, int, int], LinkLoad] = {}

    for flow in flows:
        for u, v, port in flow.links:
            records = edge_records(graph, u, v)
            matching = [
                data
                for data in records
                if int(data.get("port", -1)) == port
            ]

            if len(matching) != 1:
                raise ValueError(
                    "Se esperó una única arista {} -> {} con puerto {}, "
                    "pero se encontraron {}.".format(
                        u,
                        v,
                        port,
                        len(matching),
                    )
                )

            bandwidth = float(matching[0]["bandwidth"])
            key = (u, v, port)

            if key not in loads:
                loads[key] = LinkLoad(
                    u=u,
                    v=v,
                    port=port,
                    bandwidth=bandwidth,
                )

            load = loads[key]
            load.packet_rate += flow.packet_rate
            load.bit_rate += flow.equivalent_lambda
            load.flow_count += 1

    return loads


def global_average_packet_size(flows: List[Flow]) -> float:
    total_packets = sum(flow.packet_rate for flow in flows)
    total_bits = sum(flow.equivalent_lambda for flow in flows)

    if total_packets <= 0:
        raise ValueError(
            "No se puede calcular el tamaño medio global."
        )

    return total_bits / total_packets


def calculate_block(
    packet_rate: float,
    bit_rate: float,
    bandwidth: float,
    fallback_packet_size: float,
) -> Tuple[str, float]:
    """
    Devuelve:
        bloque_de_8_campos, rho_real_sin_limitar
    """
    if packet_rate > 0:
        avg_packet_size = bit_rate / packet_rate
    else:
        avg_packet_size = fallback_packet_size

    if avg_packet_size <= 0:
        raise ValueError(
            "El tamaño medio de paquete debe ser positivo."
        )

    service_rate = bandwidth / avg_packet_size

    if service_rate <= 0:
        raise ValueError(
            "La tasa de servicio debe ser positiva."
        )

    rho = packet_rate / service_rate if packet_rate > 0 else 0.0
    utilization = min(max(rho, 0.0), 1.0)

    if rho >= 1.0:
        # Aproximación del exceso de tráfico que no puede servirse.
        loss = min(1.0, max(0.0, 1.0 - 1.0 / rho))
        occupancy_rho = RHO_OCCUPANCY_CAP
    else:
        loss = 0.0
        occupancy_rho = max(rho, 0.0)

    if occupancy_rho == 0:
        avg_occupancy = 0.0
        max_occupancy = 0.0
    else:
        avg_occupancy = (
            occupancy_rho
            / (1.0 - occupancy_rho)
        )

        std_occupancy = (
            math.sqrt(occupancy_rho)
            / (1.0 - occupancy_rho)
        )

        max_occupancy = (
            avg_occupancy
            + 3.0 * std_occupancy
        )

    values = [
        utilization,       # utilización del puerto
        loss,              # pérdidas del puerto
        avg_packet_size,   # tamaño medio del puerto
        utilization,       # utilización de la cola FIFO
        loss,              # pérdidas de la cola
        avg_occupancy,     # ocupación media
        max_occupancy,     # ocupación máxima sintética
        avg_packet_size,   # tamaño medio de la cola
    ]

    return ",".join(fmt(value) for value in values), rho


def process_simulation(
    index: str,
    graph_name: str,
    routing_name: str,
    traffic_line: str,
) -> Tuple[str, List[Flow], Dict[Tuple[int, int, int], LinkLoad], int]:
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

    routing = read_routing(routing_path, number_of_nodes)
    port_map = build_port_map(graph)
    flows = parse_traffic_line(traffic_line, number_of_nodes)

    for flow in flows:
        reconstruct_path(
            flow,
            routing,
            port_map,
            number_of_nodes,
        )

    loads = build_link_loads(graph, flows)
    fallback_packet_size = global_average_packet_size(flows)

    blocks: List[str] = []
    unstable_links = 0

    for src in range(number_of_nodes):
        for dst in range(number_of_nodes):
            direct_edge = unique_direct_edge(graph, src, dst)

            if direct_edge is None:
                blocks.append(NO_LINK)
                continue

            port, data = direct_edge
            bandwidth = float(data["bandwidth"])
            load = loads.get((src, dst, port))

            if load is None:
                packet_rate = 0.0
                bit_rate = 0.0
            else:
                packet_rate = load.packet_rate
                bit_rate = load.bit_rate

            block, rho = calculate_block(
                packet_rate=packet_rate,
                bit_rate=bit_rate,
                bandwidth=bandwidth,
                fallback_packet_size=fallback_packet_size,
            )

            blocks.append(block)

            if rho >= 1.0:
                unstable_links += 1

                print("")
                print(
                    "[!] Simulación {}: enlace {} -> {} INESTABLE.".format(
                        index,
                        src,
                        dst,
                    )
                )
                print(
                    "    lambda_total={} pkt/s, mu={} pkt/s, rho={}.".format(
                        fmt(packet_rate),
                        fmt(
                            bandwidth
                            / (
                                bit_rate / packet_rate
                                if packet_rate > 0
                                else fallback_packet_size
                            )
                        ),
                        fmt(rho),
                    )
                )

    return ";".join(blocks), flows, loads, unstable_links


def main() -> None:
    if len(sys.argv) != 1:
        print(
            "Uso: python NODES_2_GENERATE_LINK_USAGE.py",
            file=sys.stderr,
        )
        raise SystemExit(2)

    required_paths = [
        (INPUT_FILES, "input_files.txt"),
        (GRAPHS_DIR, "directorio graphs"),
        (ROUTINGS_DIR, "directorio routings"),
        (TRAFFIC_FILE, "traffic.txt"),
    ]

    for path, description in required_paths:
        exists = path.is_dir() if "directorio" in description else path.is_file()

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
        total_unstable_links = 0

        for (
            index,
            graph_name,
            routing_name,
        ), traffic_line in zip(entries, traffic_lines):
            line, flows, loads, unstable_links = process_simulation(
                index,
                graph_name,
                routing_name,
                traffic_line,
            )

            output_lines.append(line)
            total_unstable_links += unstable_links

            print(
                "[{}] Simulación {} | {} flujo(s) | {} enlace(s) cargados"
                .format(
                    "OK" if unstable_links == 0 else "INESTABLE",
                    index,
                    len(flows),
                    len(loads),
                )
            )

            for flow in flows:
                print(
                    "     {} -> {} | lambda_in={} pkt/s | ruta {}".format(
                        flow.src,
                        flow.dst,
                        fmt(flow.packet_rate),
                        " -> ".join(str(node) for node in flow.path),
                    )
                )

            for key in sorted(loads):
                load = loads[key]
                print(
                    "       enlace {} -> {} (port {}) | {} flujo(s) | "
                    "lambda_total={} pkt/s | rho={}".format(
                        load.u,
                        load.v,
                        load.port,
                        load.flow_count,
                        fmt(load.packet_rate),
                        fmt(load.rho),
                    )
                )

        with OUTPUT_FILE.open(
            "w",
            encoding="utf-8",
            newline="",
        ) as file:
            for line in output_lines:
                # DatanetAPI elimina únicamente el LF con [:-1].
                file.write(line + "\n")

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

    if total_unstable_links:
        print(
            "[!] Se detectaron {} enlaces inestables.".format(
                total_unstable_links
            )
        )


if __name__ == "__main__":
    main()