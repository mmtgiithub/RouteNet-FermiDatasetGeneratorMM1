#!/usr/bin/env python3
# -*- coding: utf-8 -*-

# VERSION: V6_TRAIN_VALIDATION_TEST
# Los cinco TXT se producen ejecutando los generadores independientes.

"""
GENERATE_DATASET_SAMPLES.py

Genera muestras RouteNet-Fermi para un mismo grafo, un mismo routing y
un número arbitrario de flujos OD.

La terminal permite:
1. Elegir un grafo de la carpeta ../graphs.
2. Elegir un routing de la carpeta ../routings.
3. Introducir todos los pares OD en una sola línea:
       0:5,2:5,6:8,1:7
4. El programa cuenta los pares y pide todos los rangos en una línea:
       1:900,67:125,100,10:50:5
5. Genera el producto cartesiano completo de esos rangos.
6. Por cada vector ejecuta los generadores independientes y crea una carpeta results_N con:
       input_files.txt
       traffic.txt
       linkUsage.txt
       simulationResults.txt
       stability.txt
7. Pregunta si las muestras deben guardarse en ../train, ../validation o ../test.
8. Después empaqueta cada carpeta como results_N.tar.gz.
9. Copia el grafo y el routing elegidos dentro de la partición:
       train/graphs y train/routings
   o:
       validation/graphs y validation/routings
   o:
       test/graphs y test/routings

Los bucles anidados se implementan como un producto cartesiano perezoso con itertools.product.

Uso interactivo:
    python GENERATE_DATASET_SAMPLES.py

Uso no interactivo para una muestra:
    python GENERATE_DATASET_SAMPLES.py \
        --graph grafo_00.txt \
        --routing Routing_00_01.txt \
        --pairs "0:5,2:5,6:8" \
        --lambda-ranges "1:9:2,10:20:5,100" \
        --split validation

Directorios de entrada por defecto:
    ../graphs
    ../routings

Directorio de salida:
    ../train
o:
    ../validation
o:
    ../test

La selección se pregunta por terminal, salvo que se use --split
o --output-dir.

GENERADORES UTILIZADOS
----------------------
Este archivo no vuelve a calcular internamente el contenido de los TXT.
Para cada combinación de lambdas utiliza los scripts hermanos:

    GENERATE_INPUT_FILES.py
    NODES_2_GENERATE_TRAFFIC.py
    NODES_2_GENERATE_LINK_USAGE.py
    NODES_2_GENERATE_SIMULATIONRESULTS.py
    generate_stability.py

Los scripts GENERATE_MESH_GRAPH.py y GENERATE_ROUTING_FROM_GML.py no se
utilizan: se parte de un grafo y un routing ya existentes.

Los generadores se ejecutan en un espacio de trabajo temporal aislado.
Después se copian sus cinco salidas a results_N/ y se crea results_N.tar.gz.

Compatible con Python 3.8.
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import importlib.util
import io
import itertools
import math
import os
import re
import shutil
import sys
import tarfile
import tempfile
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Set, Tuple

import networkx as nx


SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_DIR = SCRIPT_DIR.parent

DEFAULT_GRAPHS_DIR = PROJECT_DIR / "graphs"
DEFAULT_ROUTINGS_DIR = PROJECT_DIR / "routings"
DEFAULT_TRAIN_DIR = PROJECT_DIR / "train"
DEFAULT_VALIDATION_DIR = PROJECT_DIR / "validation"
DEFAULT_TEST_DIR = PROJECT_DIR / "test"

EXTERNAL_GENERATOR_NAMES = {
    "input_files": "GENERATE_INPUT_FILES.py",
    "traffic": "NODES_2_GENERATE_TRAFFIC.py",
    "link_usage": "NODES_2_GENERATE_LINK_USAGE.py",
    "simulation_results": "NODES_2_GENERATE_SIMULATIONRESULTS.py",
    "stability": "generate_stability.py",
}

GENERATED_FILENAMES = (
    "input_files.txt",
    "traffic.txt",
    "linkUsage.txt",
    "simulationResults.txt",
    "stability.txt",
)

TIME_DIST = 0
EXP_MAX_FACTOR = 10.0
SIZE_DIST = 2
TOS = 0

NO_TRAFFIC_FLOW = "-1,0,-1,0,0"
NO_RESULT_FLOW = "0,0,0,-1,-1,-1,-1,-1,-1,-1,-1"
NO_LINK = "-1"

EULER_GAMMA = 0.5772156649015329
PERCENTILES = (0.10, 0.20, 0.50, 0.80, 0.90)

RHO_DELAY_CAP = 0.99
RHO_OCCUPANCY_CAP = 0.99

# Se conserva exactamente la convención del generador de stability actual.
STABILITY_LINE = "0;0;OK;Mem(KB):0;Time(s):0"

Pair = Tuple[int, int]
QueueId = Tuple[int, int, int]


@dataclass
class Flow:
    src: int
    dst: int
    packet_rate: float
    bit_rate: float
    avg_packet_size: float
    path: List[int] = field(default_factory=list)
    queues: List[QueueId] = field(default_factory=list)


@dataclass
class QueueLoad:
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
        Delay M/M/1 de sistema:
            W = 1 / (mu - lambda)

        Si rho >= 1 no existe estado estacionario. Para mantener el
        formato numérico de simulationResults se usa rho=0.99 y se
        muestra una advertencia por terminal.
        """
        mu = self.service_rate
        lam = self.packet_rate

        if not math.isfinite(mu):
            return 0.0

        if mu <= 0:
            raise ValueError(
                "La cola {} -> {} tiene una tasa de servicio no positiva."
                .format(self.u, self.v)
            )

        if lam >= mu:
            lam = RHO_DELAY_CAP * mu

        return 1.0 / (mu - lam)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Genera muestras .tar.gz para cualquier número de flujos OD."
        )
    )

    parser.add_argument(
        "--graphs-dir",
        type=Path,
        default=DEFAULT_GRAPHS_DIR,
        help="Carpeta de grafos. Por defecto: ../graphs",
    )

    parser.add_argument(
        "--routings-dir",
        type=Path,
        default=DEFAULT_ROUTINGS_DIR,
        help="Carpeta de routings. Por defecto: ../routings",
    )

    parser.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        help=(
            "Directorio de salida personalizado. Si se usa, tiene "
            "prioridad sobre --split y no se pregunta train/test."
        ),
    )

    parser.add_argument(
        "--split",
        choices=("train", "validation", "test"),
        default=None,
        help=(
            "Guarda las muestras directamente en ../train, ../validation "
            "o ../test. Si no se indica y tampoco se usa "
            "--output-dir, se pregunta "
            "por terminal."
        ),
    )

    parser.add_argument(
        "--graph",
        type=str,
        default=None,
        help="Nombre o ruta del grafo.",
    )

    parser.add_argument(
        "--routing",
        type=str,
        default=None,
        help="Nombre o ruta del routing.",
    )

    parser.add_argument(
        "--pairs",
        type=str,
        default=None,
        help="Pares OD: 0:5,2:5,6:8,...",
    )

    parser.add_argument(
        "--lambda-ranges",
        "--rates",
        dest="lambda_ranges",
        type=str,
        default=None,
        help=(
            "Especificaciones de lambda, una por flujo y separadas por comas. "
            "Formatos: valor, inicio:fin o inicio:fin:paso. "
            "Ejemplo: 1:900:10,67:125:2,100,10:50:5"
        ),
    )

    parser.add_argument(
        "--yes",
        action="store_true",
        help=(
            "Confirma automáticamente la generación de todas las "
            "combinaciones."
        ),
    )

    parser.add_argument(
        "--pkt1-size",
        type=int,
        default=300,
        help="Primer tamaño de paquete en bits. Por defecto: 300.",
    )

    parser.add_argument(
        "--pkt2-size",
        type=int,
        default=1700,
        help="Segundo tamaño de paquete en bits. Por defecto: 1700.",
    )

    return parser.parse_args()


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


def choose_output_directory() -> Path:
    """
    Pregunta si las muestras se guardarán en ../train, ../validation o ../test.
    """
    print("")
    print("=" * 72)
    print("PARTICIÓN DEL DATASET")
    print("=" * 72)
    print("  1. TRAIN      -> {}".format(DEFAULT_TRAIN_DIR))
    print("  2. VALIDATION -> {}".format(DEFAULT_VALIDATION_DIR))
    print("  3. TEST       -> {}".format(DEFAULT_TEST_DIR))
    print("")

    while True:
        answer = input(
            "¿Dónde quieres guardar las muestras? "
            "[1=train / 2=validation / 3=test]: "
        ).strip().lower()

        if answer in {"1", "train", "t"}:
            return DEFAULT_TRAIN_DIR

        if answer in {"2", "validation", "val", "v"}:
            return DEFAULT_VALIDATION_DIR

        if answer in {"3", "test"}:
            return DEFAULT_TEST_DIR

        print(
            "[!] Introduce 1/train, 2/validation o 3/test."
        )


def resolve_output_directory(
    output_dir: Optional[Path],
    split: Optional[str],
) -> Path:
    """
    Prioridad:
        1. --output-dir
        2. --split
        3. pregunta interactiva train/validation/test
    """
    if output_dir is not None:
        selected = output_dir
    elif split == "train":
        selected = DEFAULT_TRAIN_DIR
    elif split == "validation":
        selected = DEFAULT_VALIDATION_DIR
    elif split == "test":
        selected = DEFAULT_TEST_DIR
    else:
        selected = choose_output_directory()

    selected = selected.expanduser().resolve()
    selected.mkdir(
        parents=True,
        exist_ok=True,
    )

    return selected


def copy_dataset_resource(
    source: Path,
    destination_directory: Path,
) -> Path:
    """
    Copia el grafo o routing seleccionado dentro de la partición.

    Así, DatanetAPI encuentra en el mismo nivel:
        graphs/
        routings/
        results_N.tar.gz
    """
    destination_directory.mkdir(
        parents=True,
        exist_ok=True,
    )

    destination = destination_directory / source.name

    if source.resolve() != destination.resolve():
        shutil.copy2(
            str(source),
            str(destination),
        )

    return destination


def prepare_partition_resources(
    output_dir: Path,
    graph_path: Path,
    routing_path: Path,
) -> Tuple[Path, Path]:
    graph_copy = copy_dataset_resource(
        graph_path,
        output_dir / "graphs",
    )

    routing_copy = copy_dataset_resource(
        routing_path,
        output_dir / "routings",
    )

    return graph_copy, routing_copy


def locate_external_generator(
    canonical_name: str,
) -> Path:
    """
    Localiza un script hermano por su nombre normal.

    También admite copias descargadas cuyo nombre termine en '(N)',
    por ejemplo NODES_2_GENERATE_TRAFFIC(4).py.
    """
    direct = SCRIPT_DIR / canonical_name

    if direct.is_file():
        return direct.resolve()

    canonical_stem = Path(canonical_name).stem.lower()
    candidates = sorted(
        path
        for path in SCRIPT_DIR.glob("*.py")
        if (
            path.stem.lower() == canonical_stem
            or path.stem.lower().startswith(
                canonical_stem + "("
            )
        )
    )

    if len(candidates) == 1:
        return candidates[0].resolve()

    if not candidates:
        raise FileNotFoundError(
            "No se encontró el generador hermano '{}', esperado en '{}'."
            .format(
                canonical_name,
                SCRIPT_DIR,
            )
        )

    raise ValueError(
        "Hay varias copias posibles de '{}': {}. Conserva una sola o "
        "renombra la correcta con el nombre canónico."
        .format(
            canonical_name,
            [path.name for path in candidates],
        )
    )


def resolve_external_generators() -> Dict[str, Path]:
    paths = {
        key: locate_external_generator(name)
        for key, name in EXTERNAL_GENERATOR_NAMES.items()
    }

    # Evita que por error se use este mismo archivo como dependencia.
    current = Path(__file__).resolve()

    for key, path in paths.items():
        if path == current:
            raise ValueError(
                "El generador externo '{}' apunta al propio "
                "GENERATE_DATASET_SAMPLES.py.".format(key)
            )

    return paths


def load_external_module(
    module_key: str,
    script_path: Path,
):
    module_name = "_routenet_external_{}_{}".format(
        module_key,
        abs(hash(str(script_path))),
    )

    specification = importlib.util.spec_from_file_location(
        module_name,
        str(script_path),
    )

    if specification is None or specification.loader is None:
        raise ImportError(
            "No se pudo cargar '{}'.".format(script_path)
        )

    module = importlib.util.module_from_spec(specification)
    sys.modules[module_name] = module
    specification.loader.exec_module(module)

    if not hasattr(module, "main"):
        raise AttributeError(
            "'{}' no contiene una función main().".format(script_path)
        )

    return module


def execute_external_main(
    module,
    arguments: Sequence[str],
    working_directory: Path,
) -> str:
    """
    Ejecuta main() sin abrir un proceso nuevo.

    Se capturan stdout y stderr para evitar miles de líneas al generar
    grandes productos cartesianos. Si ocurre un error, la salida completa
    se incorpora al mensaje de excepción.
    """
    previous_argv = list(sys.argv)
    previous_directory = Path.cwd()

    stdout_buffer = io.StringIO()
    stderr_buffer = io.StringIO()

    try:
        sys.argv = list(arguments)
        os.chdir(str(working_directory))

        with contextlib.redirect_stdout(stdout_buffer):
            with contextlib.redirect_stderr(stderr_buffer):
                try:
                    module.main()
                except SystemExit as error:
                    code = error.code

                    if code not in (None, 0):
                        raise RuntimeError(
                            "El generador terminó con código {}."
                            .format(code)
                        )

    except Exception as error:
        output = stdout_buffer.getvalue()
        errors = stderr_buffer.getvalue()

        details = "\n".join(
            part.strip()
            for part in (output, errors)
            if part.strip()
        )

        if details:
            raise RuntimeError(
                "{}\nSalida del generador:\n{}"
                .format(error, details)
            ) from error

        raise

    finally:
        sys.argv = previous_argv
        os.chdir(str(previous_directory))

    return (
        stdout_buffer.getvalue()
        + stderr_buffer.getvalue()
    )


def configure_external_modules(
    modules: Mapping[str, Any],
    workspace_root: Path,
) -> None:
    input_file = workspace_root / "input_files.txt"
    graphs_dir = workspace_root / "graphs"
    routings_dir = workspace_root / "routings"
    traffic_file = workspace_root / "traffic.txt"
    link_usage_file = workspace_root / "linkUsage.txt"
    simulation_results_file = (
        workspace_root / "simulationResults.txt"
    )
    stability_file = workspace_root / "stability.txt"

    input_module = modules["input_files"]
    input_module.GRAPH_PATH = str(graphs_dir)
    input_module.ROUTING_PATH = str(routings_dir)

    traffic_module = modules["traffic"]
    traffic_module.INPUT_FILES = input_file
    traffic_module.GRAPHS_DIR = graphs_dir
    traffic_module.ROUTINGS_DIR = routings_dir
    traffic_module.OUTPUT_FILE = traffic_file

    link_module = modules["link_usage"]
    link_module.INPUT_FILES = input_file
    link_module.GRAPHS_DIR = graphs_dir
    link_module.ROUTINGS_DIR = routings_dir
    link_module.TRAFFIC_FILE = traffic_file
    link_module.OUTPUT_FILE = link_usage_file

    simulation_module = modules["simulation_results"]
    simulation_module.INPUT_FILES = input_file
    simulation_module.GRAPHS_DIR = graphs_dir
    simulation_module.ROUTINGS_DIR = routings_dir
    simulation_module.TRAFFIC_FILE = traffic_file
    simulation_module.OUTPUT_FILE = simulation_results_file

    stability_module = modules["stability"]
    stability_module.INPUT_FILES = str(input_file)
    stability_module.STABILITY_FILE = str(stability_file)


def validate_single_input_entry(
    input_file: Path,
    graph_name: str,
    routing_name: str,
) -> None:
    lines = [
        line.strip()
        for line in input_file.read_text(
            encoding="utf-8-sig",
        ).splitlines()
        if line.strip()
    ]

    expected = "0;{};{}".format(
        graph_name,
        routing_name,
    )

    if lines != [expected]:
        raise ValueError(
            "GENERATE_INPUT_FILES.py debía producir únicamente '{}', "
            "pero produjo: {}."
            .format(expected, lines)
        )


def prepare_external_workspace(
    workspace_root: Path,
    graph_path: Path,
    routing_path: Path,
    script_paths: Mapping[str, Path],
) -> Dict[str, Any]:
    graphs_dir = workspace_root / "graphs"
    routings_dir = workspace_root / "routings"
    scripts_dir = workspace_root / "scripts"

    graphs_dir.mkdir(parents=True, exist_ok=True)
    routings_dir.mkdir(parents=True, exist_ok=True)
    scripts_dir.mkdir(parents=True, exist_ok=True)

    shutil.copy2(
        str(graph_path),
        str(graphs_dir / graph_path.name),
    )
    shutil.copy2(
        str(routing_path),
        str(routings_dir / routing_path.name),
    )

    modules = {
        key: load_external_module(key, path)
        for key, path in script_paths.items()
    }

    configure_external_modules(
        modules,
        workspace_root,
    )

    # GENERATE_INPUT_FILES.py escribe ../input_files.txt respecto al CWD.
    execute_external_main(
        modules["input_files"],
        [str(script_paths["input_files"])],
        scripts_dir,
    )

    validate_single_input_entry(
        workspace_root / "input_files.txt",
        graph_path.name,
        routing_path.name,
    )

    return modules


def generate_one_sample_externally(
    modules: Mapping[str, Any],
    script_paths: Mapping[str, Path],
    workspace_root: Path,
    pairs: Sequence[Pair],
    rates: Sequence[float],
    packet_size_1: int,
    packet_size_2: int,
) -> Dict[str, str]:
    scripts_dir = workspace_root / "scripts"

    pairs_argument = ",".join(
        "{}:{}".format(source, destination)
        for source, destination in pairs
    )
    rates_argument = ",".join(
        fmt(rate)
        for rate in rates
    )

    execute_external_main(
        modules["traffic"],
        [
            str(script_paths["traffic"]),
            str(packet_size_1),
            str(packet_size_2),
            "--pairs",
            pairs_argument,
            "--rates",
            rates_argument,
        ],
        scripts_dir,
    )

    execute_external_main(
        modules["link_usage"],
        [str(script_paths["link_usage"])],
        scripts_dir,
    )

    execute_external_main(
        modules["simulation_results"],
        [str(script_paths["simulation_results"])],
        scripts_dir,
    )

    execute_external_main(
        modules["stability"],
        [str(script_paths["stability"])],
        scripts_dir,
    )

    contents: Dict[str, str] = {}

    for filename in GENERATED_FILENAMES:
        path = workspace_root / filename

        if not path.is_file():
            raise FileNotFoundError(
                "El generador externo no produjo '{}'.".format(path)
            )

        content = path.read_text(
            encoding="utf-8-sig",
        )

        if not content:
            raise ValueError(
                "El generador externo produjo '{}' vacío.".format(path)
            )

        lines = [
            line
            for line in content.splitlines()
            if line.strip()
        ]

        if len(lines) != 1:
            raise ValueError(
                "'{}' debía contener una única simulación, pero tiene "
                "{} líneas no vacías."
                .format(path, len(lines))
            )

        contents[filename] = content

    if not contents["traffic.txt"].endswith(";\n"):
        raise ValueError(
            "traffic.txt no termina exactamente en ';\\n'."
        )

    if not contents["simulationResults.txt"].endswith(";\n"):
        raise ValueError(
            "simulationResults.txt no termina exactamente en ';\\n'."
        )

    return contents


def simulation_delays_from_content(
    content: str,
    number_of_nodes: int,
    pairs: Sequence[Pair],
) -> Tuple[float, List[float]]:
    """
    Extrae el global_delay y los AvgDelay de los pares activos para
    mostrarlos como comprobación del archivo realmente empaquetado.
    """
    line = content.strip()

    if "|" not in line:
        raise ValueError(
            "simulationResults.txt no contiene '|'."
        )

    header, payload = line.split("|", maxsplit=1)
    header_fields = header.split(",")

    if len(header_fields) < 3:
        raise ValueError(
            "Cabecera inválida de simulationResults.txt."
        )

    global_delay = float(header_fields[2])

    blocks = payload.split(";")

    while blocks and not blocks[-1]:
        blocks.pop()

    expected_blocks = number_of_nodes * number_of_nodes

    if len(blocks) != expected_blocks:
        raise ValueError(
            "simulationResults.txt contiene {} bloques; se esperaban {}."
            .format(len(blocks), expected_blocks)
        )

    flow_delays: List[float] = []

    for source, destination in pairs:
        position = source * number_of_nodes + destination
        fields = blocks[position].split(",")

        if len(fields) < 4:
            raise ValueError(
                "Bloque incompleto para {} -> {}."
                .format(source, destination)
            )

        flow_delays.append(float(fields[3]))

    return global_delay, flow_delays


def list_candidate_files(
    directory: Path,
    suffixes: Set[str],
) -> List[Path]:
    if not directory.is_dir():
        raise FileNotFoundError(
            "No existe el directorio '{}'.".format(directory)
        )

    files = sorted(
        path
        for path in directory.iterdir()
        if path.is_file() and path.suffix.lower() in suffixes
    )

    if not files:
        raise FileNotFoundError(
            "No hay archivos compatibles en '{}'.".format(directory)
        )

    return files


def choose_file(
    title: str,
    files: Sequence[Path],
) -> Path:
    print("")
    print(title)
    print("")

    for index, path in enumerate(files, start=1):
        print("  {}. {}".format(index, path.name))

    print("")

    while True:
        raw = input("Selecciona un número: ").strip()

        try:
            choice = int(raw)
        except ValueError:
            print("[!] Introduce un número entero.")
            continue

        if 1 <= choice <= len(files):
            return files[choice - 1]

        print(
            "[!] El número debe estar entre 1 y {}.".format(len(files))
        )


def resolve_named_path(
    value: str,
    directory: Path,
) -> Path:
    candidate = Path(value)

    if candidate.is_file():
        return candidate.resolve()

    candidate = directory / value

    if candidate.is_file():
        return candidate.resolve()

    raise FileNotFoundError(
        "No existe '{}' ni '{}'.".format(value, candidate)
    )


def read_routing(
    path: Path,
    number_of_nodes: int,
) -> List[List[int]]:
    routing: List[List[int]] = []

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
                "Bandwidth no positivo en {} -> {}.".format(u, v)
            )

        source_ports = port_map.setdefault(u, {})

        if port in source_ports:
            previous_destination = source_ports[port][0]
            raise ValueError(
                "Puerto {} duplicado en el nodo {} hacia {} y {}."
                .format(
                    port,
                    u,
                    previous_destination,
                    v,
                )
            )

        source_ports[port] = (v, data)

    return port_map


def validate_no_parallel_directed_edges(graph: nx.Graph) -> None:
    """
    linkUsage.txt tiene una posición por pareja dirigida (u,v).
    El objeto puede ser MultiDiGraph, pero no puede contener dos
    aristas paralelas reales con el mismo origen y destino.
    """
    if not graph.is_multigraph():
        return

    nodes = [int(node) for node in graph.nodes()]

    for u in nodes:
        for v in nodes:
            count = graph.number_of_edges(u, v)

            if count > 1:
                raise ValueError(
                    "El grafo contiene {} enlaces paralelos {} -> {}. "
                    "linkUsage.txt solo admite uno por pareja dirigida."
                    .format(count, u, v)
                )


def load_and_validate(
    graph_path: Path,
    routing_path: Path,
) -> Tuple[
    nx.Graph,
    int,
    List[List[int]],
    Dict[int, Dict[int, Tuple[int, Dict[str, Any]]]],
]:
    try:
        graph = nx.read_gml(str(graph_path), label=None)
    except Exception as error:
        raise ValueError(
            "No se pudo leer '{}' como GML: {}".format(
                graph_path,
                error,
            )
        )

    number_of_nodes = graph.number_of_nodes()

    if number_of_nodes < 2:
        raise ValueError("El grafo necesita al menos dos nodos.")

    actual_nodes = sorted(int(node) for node in graph.nodes())
    expected_nodes = list(range(number_of_nodes))

    if actual_nodes != expected_nodes:
        raise ValueError(
            "Los nodos deben ser 0..{}. Encontrados: {}.".format(
                number_of_nodes - 1,
                actual_nodes,
            )
        )

    validate_no_parallel_directed_edges(graph)

    routing = read_routing(
        routing_path,
        number_of_nodes,
    )

    port_map = build_port_map(graph)

    return graph, number_of_nodes, routing, port_map


def reconstruct_route(
    source: int,
    destination: int,
    number_of_nodes: int,
    routing: Sequence[Sequence[int]],
    port_map: Mapping[int, Mapping[int, Tuple[int, Dict[str, Any]]]],
) -> Tuple[List[int], List[QueueId]]:
    if source == destination:
        raise ValueError(
            "Origen y destino no pueden ser iguales: {} -> {}."
            .format(source, destination)
        )

    if not (
        0 <= source < number_of_nodes
        and 0 <= destination < number_of_nodes
    ):
        raise ValueError(
            "El par {} -> {} queda fuera del rango 0..{}."
            .format(
                source,
                destination,
                number_of_nodes - 1,
            )
        )

    current = source
    visited = {current}
    path = [current]
    queues: List[QueueId] = []

    for _ in range(number_of_nodes):
        port = routing[current][destination]

        if port == -1:
            raise ValueError(
                "La ruta {} -> {} termina en el nodo {}."
                .format(source, destination, current)
            )

        next_information = port_map.get(current, {}).get(port)

        if next_information is None:
            raise ValueError(
                "El routing usa el puerto {} del nodo {}, "
                "pero ese puerto no existe en el grafo."
                .format(port, current)
            )

        next_node, _data = next_information

        if next_node in visited:
            raise ValueError(
                "Bucle en la ruta {} -> {}: {}."
                .format(
                    source,
                    destination,
                    path + [next_node],
                )
            )

        queues.append((current, next_node, port))
        path.append(next_node)

        if next_node == destination:
            return path, queues

        visited.add(next_node)
        current = next_node

    raise ValueError(
        "No se completó la ruta {} -> {}.".format(source, destination)
    )


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
                "Par inválido '{}'. Usa origen:destino.".format(item)
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
                "Origen y destino no pueden coincidir en {}:{}."
                .format(source, destination)
            )

        pair = (source, destination)

        if pair in seen:
            raise ValueError(
                "Par repetido {}:{}.".format(source, destination)
            )

        seen.add(pair)
        pairs.append(pair)

    if not pairs:
        raise ValueError(
            "Debe indicarse al menos un flujo OD."
        )

    return pairs


def ask_pairs(
    number_of_nodes: int,
    routing: Sequence[Sequence[int]],
    port_map: Mapping[int, Mapping[int, Tuple[int, Dict[str, Any]]]],
) -> List[Pair]:
    print("")
    print(
        "Introduce todos los pares OD separados por comas."
    )
    print(
        "Ejemplo: 0:5,2:5,6:8"
    )
    print(
        "Nodos disponibles: 0..{}.".format(number_of_nodes - 1)
    )

    while True:
        raw = input("Pares OD: ").strip()

        try:
            pairs = parse_pairs(raw)

            for source, destination in pairs:
                reconstruct_route(
                    source,
                    destination,
                    number_of_nodes,
                    routing,
                    port_map,
                )

            return pairs

        except ValueError as error:
            print("[!] {}".format(error))


def decimal_to_float(value: Decimal) -> float:
    result = float(value)

    if not math.isfinite(result):
        raise ValueError(
            "Lambda fuera del rango numérico soportado: {}."
            .format(value)
        )

    return result


def parse_lambda_axis(specification: str) -> List[float]:
    """
    Convierte una especificación de lambda en una lista de valores.

    Formatos admitidos:
        500           -> [500]
        1:5           -> [1, 2, 3, 4, 5]       (step por defecto = 1)
        1:9:2         -> [1, 3, 5, 7, 9]
        0.5:2:0.5     -> [0.5, 1.0, 1.5, 2.0]
        10:2:-2       -> [10, 8, 6, 4, 2]

    El extremo final se incluye cuando pertenece exactamente a la
    progresión. Los cálculos se hacen con Decimal para evitar errores
    acumulativos de coma flotante.
    """
    specification = specification.strip()

    if not specification:
        raise ValueError(
            "Hay una especificación de lambda vacía."
        )

    pieces = [
        piece.strip()
        for piece in specification.split(":")
    ]

    if len(pieces) not in {1, 2, 3}:
        raise ValueError(
            "Especificación inválida '{}'. Usa valor, inicio:fin "
            "o inicio:fin:step.".format(specification)
        )

    try:
        numbers = [Decimal(piece) for piece in pieces]
    except InvalidOperation:
        raise ValueError(
            "Valor no numérico en la especificación '{}'."
            .format(specification)
        )

    if len(numbers) == 1:
        value = numbers[0]

        if value <= 0:
            raise ValueError(
                "Cada lambda debe ser mayor que cero."
            )

        return [decimal_to_float(value)]

    start_value = numbers[0]
    end_value = numbers[1]

    if start_value <= 0 or end_value <= 0:
        raise ValueError(
            "Los extremos de lambda deben ser mayores que cero."
        )

    if len(numbers) == 3:
        step = numbers[2]
    else:
        step = (
            Decimal("1")
            if start_value <= end_value
            else Decimal("-1")
        )

    if step == 0:
        raise ValueError(
            "El step no puede ser cero."
        )

    if start_value < end_value and step < 0:
        raise ValueError(
            "Para subir desde {} hasta {}, el step debe ser positivo."
            .format(start_value, end_value)
        )

    if start_value > end_value and step > 0:
        raise ValueError(
            "Para bajar desde {} hasta {}, el step debe ser negativo."
            .format(start_value, end_value)
        )

    values: List[float] = []
    current = start_value

    if step > 0:
        while current <= end_value:
            values.append(decimal_to_float(current))
            current += step
    else:
        while current >= end_value:
            values.append(decimal_to_float(current))
            current += step

    if not values:
        raise ValueError(
            "El rango '{}' no contiene valores.".format(specification)
        )

    return values

def parse_lambda_ranges(
    text: str,
    expected_count: int,
) -> List[List[float]]:
    specifications = [
        item.strip()
        for item in text.split(",")
        if item.strip()
    ]

    if len(specifications) != expected_count:
        raise ValueError(
            "Se definieron {} flujos OD, por lo que deben indicarse "
            "exactamente {} especificaciones de lambda. Se recibieron {}."
            .format(
                expected_count,
                expected_count,
                len(specifications),
            )
        )

    return [
        parse_lambda_axis(specification)
        for specification in specifications
    ]


def ask_lambda_ranges(
    pairs: Sequence[Pair],
) -> List[List[float]]:
    print("")
    print(
        "Se han definido {} flujos OD.".format(len(pairs))
    )
    print(
        "Introduce TODAS las lambdas o rangos en una sola línea."
    )
    print(
        "Formatos: valor | inicio:fin | inicio:fin:step"
    )
    print(
        "Ejemplo: 1:900:10,67:125:2,100,10:50:5"
    )

    for index, pair in enumerate(pairs, start=1):
        print(
            "  lambda_in{} corresponde a {} -> {}."
            .format(index, pair[0], pair[1])
        )

    while True:
        raw = input(
            "Lambdas/rangos [inicio:fin:step] separados por comas: "
        ).strip()

        try:
            return parse_lambda_ranges(
                raw,
                len(pairs),
            )
        except ValueError as error:
            print("[!] {}".format(error))


def count_combinations(
    lambda_axes: Sequence[Sequence[float]],
) -> int:
    total = 1

    for axis in lambda_axes:
        total *= len(axis)

    return total


def print_generation_plan(
    pairs: Sequence[Pair],
    lambda_axes: Sequence[Sequence[float]],
) -> int:
    total = count_combinations(lambda_axes)

    print("")
    print("=" * 72)
    print("PLAN DE GENERACIÓN")

    for index, (pair, axis) in enumerate(
        zip(pairs, lambda_axes),
        start=1,
    ):
        if len(axis) >= 2:
            step = axis[1] - axis[0]
            step_text = fmt(step)
        else:
            step_text = "N/A"

        print(
            "  lambda_in{} | {} -> {} | {} valores | "
            "inicio={} | fin={} | step={}"
            .format(
                index,
                pair[0],
                pair[1],
                len(axis),
                fmt(axis[0]),
                fmt(axis[-1]),
                step_text,
            )
        )

    print("  Producto cartesiano: {} muestras".format(total))
    print("=" * 72)

    return total

def ask_generation_confirmation(total: int) -> bool:
    while True:
        answer = input(
            "Se generarán {} archivos .tar.gz. ¿Continuar? [s/N]: "
            .format(total)
        ).strip().lower()

        if answer in {"", "n", "no"}:
            return False

        if answer in {"s", "si", "sí", "y", "yes"}:
            return True

        print("[!] Responde s o n.")


def build_flows(
    pairs: Sequence[Pair],
    rates: Sequence[float],
    avg_packet_size: float,
    number_of_nodes: int,
    routing: Sequence[Sequence[int]],
    port_map: Mapping[int, Mapping[int, Tuple[int, Dict[str, Any]]]],
) -> List[Flow]:
    flows: List[Flow] = []

    for pair, rate in zip(pairs, rates):
        path, queues = reconstruct_route(
            pair[0],
            pair[1],
            number_of_nodes,
            routing,
            port_map,
        )

        flows.append(
            Flow(
                src=pair[0],
                dst=pair[1],
                packet_rate=rate,
                bit_rate=rate * avg_packet_size,
                avg_packet_size=avg_packet_size,
                path=path,
                queues=queues,
            )
        )

    return flows


def edge_data_for_queue(
    graph: nx.Graph,
    queue_id: QueueId,
) -> Dict[str, Any]:
    u, v, port = queue_id
    records = edge_records(graph, u, v)

    candidates = [
        data
        for data in records
        if int(data.get("port", -1)) == port
    ]

    if len(candidates) != 1:
        raise ValueError(
            "Se esperaba una única arista {} -> {} con port {}, "
            "pero se encontraron {}."
            .format(
                u,
                v,
                port,
                len(candidates),
            )
        )

    return candidates[0]


def build_queue_loads(
    graph: nx.Graph,
    flows: Sequence[Flow],
) -> Dict[QueueId, QueueLoad]:
    loads: Dict[QueueId, QueueLoad] = {}

    for flow in flows:
        for queue_id in flow.queues:
            data = edge_data_for_queue(graph, queue_id)
            bandwidth = float(data["bandwidth"])

            if queue_id not in loads:
                loads[queue_id] = QueueLoad(
                    u=queue_id[0],
                    v=queue_id[1],
                    port=queue_id[2],
                    bandwidth=bandwidth,
                )

            load = loads[queue_id]
            load.packet_rate += flow.packet_rate
            load.bit_rate += flow.bit_rate
            load.flow_count += 1

    return loads


def build_input_files(
    graph_name: str,
    routing_name: str,
) -> str:
    return "0;{};{}\n".format(
        graph_name,
        routing_name,
    )


def traffic_flow_block(
    flow: Flow,
    packet_size_1: int,
    packet_size_2: int,
) -> str:
    return ",".join(
        [
            str(TIME_DIST),
            fmt(flow.bit_rate),
            fmt(flow.packet_rate),
            fmt(EXP_MAX_FACTOR),
            str(SIZE_DIST),
            fmt(flow.avg_packet_size),
            str(packet_size_1),
            str(packet_size_2),
            str(TOS),
        ]
    )


def build_traffic(
    number_of_nodes: int,
    flows: Sequence[Flow],
    packet_size_1: int,
    packet_size_2: int,
) -> str:
    flow_by_pair = {
        (flow.src, flow.dst): flow
        for flow in flows
    }

    blocks: List[str] = []

    for source in range(number_of_nodes):
        for destination in range(number_of_nodes):
            flow = flow_by_pair.get((source, destination))

            if flow is None:
                blocks.append(NO_TRAFFIC_FLOW)
            else:
                blocks.append(
                    traffic_flow_block(
                        flow,
                        packet_size_1,
                        packet_size_2,
                    )
                )

    total_external_rate = sum(
        flow.packet_rate
        for flow in flows
    )

    return (
        fmt(total_external_rate)
        + "|"
        + ";".join(blocks)
        + ";\n"
    )


def calculate_link_usage_block(
    packet_rate: float,
    bit_rate: float,
    bandwidth: float,
    fallback_packet_size: float,
) -> Tuple[str, float]:
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
        loss = min(
            1.0,
            max(0.0, 1.0 - 1.0 / rho),
        )
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
        utilization,
        loss,
        avg_packet_size,
        utilization,
        loss,
        avg_occupancy,
        max_occupancy,
        avg_packet_size,
    ]

    return ",".join(fmt(value, 6) for value in values), rho


def build_link_usage(
    graph: nx.Graph,
    number_of_nodes: int,
    flows: Sequence[Flow],
    loads: Mapping[QueueId, QueueLoad],
) -> str:
    total_packets = sum(
        flow.packet_rate
        for flow in flows
    )
    total_bits = sum(
        flow.bit_rate
        for flow in flows
    )

    if total_packets <= 0:
        raise ValueError(
            "No se puede calcular el tamaño medio global."
        )

    fallback_packet_size = total_bits / total_packets
    blocks: List[str] = []

    for source in range(number_of_nodes):
        for destination in range(number_of_nodes):
            records = edge_records(
                graph,
                source,
                destination,
            )

            if not records:
                blocks.append(NO_LINK)
                continue

            if len(records) > 1:
                raise ValueError(
                    "Hay {} enlaces paralelos {} -> {}. "
                    "linkUsage.txt solo admite uno."
                    .format(
                        len(records),
                        source,
                        destination,
                    )
                )

            data = records[0]
            port = int(data["port"])
            bandwidth = float(data["bandwidth"])
            load = loads.get(
                (source, destination, port)
            )

            if load is None:
                packet_rate = 0.0
                bit_rate = 0.0
            else:
                packet_rate = load.packet_rate
                bit_rate = load.bit_rate

            block, _rho = calculate_link_usage_block(
                packet_rate=packet_rate,
                bit_rate=bit_rate,
                bandwidth=bandwidth,
                fallback_packet_size=fallback_packet_size,
            )

            blocks.append(block)

    # linkUsage no lleva ';' final.
    return ";".join(blocks) + "\n"


def exponential_percentile(
    mean_delay: float,
    probability: float,
) -> float:
    return -mean_delay * math.log(1.0 - probability)


def simulation_result_block(
    flow: Flow,
    loads: Mapping[QueueId, QueueLoad],
) -> Tuple[str, float, float, float]:
    link_delays: List[float] = []
    survival_probability = 1.0

    for queue_id in flow.queues:
        load = loads[queue_id]
        link_delays.append(load.mean_delay)
        survival_probability *= 1.0 - load.loss_fraction

    avg_delay = sum(link_delays)

    if avg_delay <= 0:
        avg_delay = 1e-12

    loss_fraction = 1.0 - survival_probability
    packets_generated = flow.packet_rate
    packets_dropped = packets_generated * loss_fraction

    avg_bw_kbps = flow.bit_rate / 1000.0
    avg_ln_delay = math.log(avg_delay) - EULER_GAMMA

    percentiles = [
        exponential_percentile(
            avg_delay,
            probability,
        )
        for probability in PERCENTILES
    ]

    jitter = sum(
        link_delay * link_delay
        for link_delay in link_delays
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


def build_simulation_results(
    number_of_nodes: int,
    flows: Sequence[Flow],
    loads: Mapping[QueueId, QueueLoad],
) -> str:
    results_by_pair: Dict[Pair, str] = {}

    total_packets = 0.0
    total_losses = 0.0
    weighted_delay = 0.0

    for flow in flows:
        (
            block,
            generated,
            dropped,
            avg_delay,
        ) = simulation_result_block(
            flow,
            loads,
        )

        results_by_pair[(flow.src, flow.dst)] = block
        total_packets += generated
        total_losses += dropped
        weighted_delay += generated * avg_delay

    global_delay = (
        weighted_delay / total_packets
        if total_packets > 0
        else 0.0
    )

    blocks: List[str] = []

    for source in range(number_of_nodes):
        for destination in range(number_of_nodes):
            blocks.append(
                results_by_pair.get(
                    (source, destination),
                    NO_RESULT_FLOW,
                )
            )

    header = ",".join(
        [
            fmt(total_packets),
            fmt(total_losses),
            fmt(global_delay),
        ]
    )

    # Termina exactamente en ";\n".
    return (
        header
        + "|"
        + ";".join(blocks)
        + ";\n"
    )


def safe_token(value: float) -> str:
    text = fmt(value, 6)
    return (
        text
        .replace("-", "m")
        .replace(".", "p")
    )


def sanitized_stem(path: Path) -> str:
    return (
        re.sub(
            r"[^A-Za-z0-9_-]+",
            "_",
            path.stem,
        ).strip("_")
        or "file"
    )


def rates_token(rates: Sequence[float]) -> str:
    raw = "_".join(
        safe_token(rate)
        for rate in rates
    )

    if len(raw) <= 100:
        return raw

    digest = hashlib.sha1(
        raw.encode("utf-8")
    ).hexdigest()[:12]

    return "{}_hash-{}".format(
        raw[:80],
        digest,
    )


def next_result_index(output_dir: Path) -> int:
    """
    Busca results_0, results_1, ... y devuelve el siguiente índice libre.

    Se tienen en cuenta tanto carpetas results_N como archivos
    results_N.tar.gz para evitar sobrescribir resultados anteriores.
    """
    output_dir.mkdir(
        parents=True,
        exist_ok=True,
    )

    used_indices: Set[int] = set()
    name_pattern = re.compile(
        r"^results_(\d+)(?:\.tar\.gz)?$"
    )

    for candidate in output_dir.iterdir():
        match = name_pattern.match(candidate.name)

        if match:
            used_indices.add(int(match.group(1)))

    if not used_indices:
        return 0

    return max(used_indices) + 1


def create_result_package(
    output_dir: Path,
    result_index: int,
    contents: Mapping[str, str],
) -> Tuple[Path, Path]:
    """
    Crea y conserva una carpeta results_N con los cinco archivos.
    Después empaqueta esa carpeta como results_N.tar.gz.

    El TAR contiene la carpeta superior results_N.
    """
    output_dir.mkdir(
        parents=True,
        exist_ok=True,
    )

    result_name = "results_{}".format(result_index)
    result_dir = output_dir / result_name
    archive_path = output_dir / (result_name + ".tar.gz")

    if result_dir.exists():
        raise FileExistsError(
            "Ya existe la carpeta '{}'.".format(result_dir)
        )

    if archive_path.exists():
        raise FileExistsError(
            "Ya existe el archivo '{}'.".format(archive_path)
        )

    result_dir.mkdir()

    try:
        for filename, content in contents.items():
            file_path = result_dir / filename

            with file_path.open(
                "w",
                encoding="utf-8",
                newline="",
            ) as file:
                file.write(content)

        with tarfile.open(
            str(archive_path),
            mode="w:gz",
        ) as archive:
            archive.add(
                str(result_dir),
                arcname=result_name,
                recursive=True,
            )

    except Exception:
        if archive_path.exists():
            archive_path.unlink()
        raise

    return result_dir, archive_path


def print_sample_summary(
    sample_number: int,
    archive_path: Path,
    flows: Sequence[Flow],
    loads: Mapping[QueueId, QueueLoad],
) -> None:
    maximum_rho = max(
        (
            load.rho
            for load in loads.values()
        ),
        default=0.0,
    )

    print("")
    print("=" * 72)
    print("MUESTRA {:04d}".format(sample_number))

    for index, flow in enumerate(flows, start=1):
        print(
            "  Flujo {}: {} -> {} | lambda_in={} pkt/s | ruta={}"
            .format(
                index,
                flow.src,
                flow.dst,
                fmt(flow.packet_rate),
                " -> ".join(
                    str(node)
                    for node in flow.path
                ),
            )
        )

    print("  rho_max: {}".format(fmt(maximum_rho)))
    print(
        "  Estado Jackson: {}".format(
            "ESTABLE"
            if maximum_rho < 1.0
            else "INESTABLE"
        )
    )
    print("  Contenido del pack:")
    print("    input_files.txt")
    print("    traffic.txt")
    print("    linkUsage.txt")
    print("    simulationResults.txt")
    print("    stability.txt")
    print("  Archivo: {}".format(archive_path.resolve()))
    print("=" * 72)


def ask_continue() -> bool:
    while True:
        answer = input(
            "\n¿Generar otra muestra con otro vector de lambdas? [s/N]: "
        ).strip().lower()

        if answer in {"", "n", "no"}:
            return False

        if answer in {"s", "si", "sí", "y", "yes"}:
            return True

        print("[!] Responde s o n.")


def main() -> None:
    args = parse_args()

    try:
        output_dir = resolve_output_directory(
            args.output_dir,
            args.split,
        )

        print("")
        print(
            "[OK] Las muestras se guardarán en: {}"
            .format(output_dir)
        )

        if args.pkt1_size <= 0 or args.pkt2_size <= 0:
            raise ValueError(
                "Los tamaños de paquete deben ser positivos."
            )

        graph_files = list_candidate_files(
            args.graphs_dir,
            {".txt", ".gml"},
        )

        if args.graph is None:
            graph_path = choose_file(
                "Grafos disponibles:",
                graph_files,
            )
        else:
            graph_path = resolve_named_path(
                args.graph,
                args.graphs_dir,
            )

        routing_files = list_candidate_files(
            args.routings_dir,
            {".txt"},
        )

        if args.routing is None:
            routing_path = choose_file(
                "Routings disponibles:",
                routing_files,
            )
        else:
            routing_path = resolve_named_path(
                args.routing,
                args.routings_dir,
            )

        (
            graph,
            number_of_nodes,
            routing,
            port_map,
        ) = load_and_validate(
            graph_path,
            routing_path,
        )

        graph_copy, routing_copy = prepare_partition_resources(
            output_dir,
            graph_path,
            routing_path,
        )

        print("")
        print("[OK] Grafo: {}".format(graph_path.name))
        print("[OK] Routing: {}".format(routing_path.name))
        print("[OK] Copia del grafo: {}".format(graph_copy))
        print("[OK] Copia del routing: {}".format(routing_copy))
        print("[OK] Nodos: {}".format(number_of_nodes))
        print(
            "[OK] Tamaños de paquete: {} y {} bits"
            .format(
                args.pkt1_size,
                args.pkt2_size,
            )
        )

        if args.pairs is None:
            pairs = ask_pairs(
                number_of_nodes,
                routing,
                port_map,
            )
        else:
            pairs = parse_pairs(args.pairs)

            for source, destination in pairs:
                reconstruct_route(
                    source,
                    destination,
                    number_of_nodes,
                    routing,
                    port_map,
                )

        print("")
        print(
            "[OK] Se han definido {} flujos OD."
            .format(len(pairs))
        )

        for index, pair in enumerate(pairs, start=1):
            print(
                "     lambda_in{} -> flujo {} -> {}"
                .format(
                    index,
                    pair[0],
                    pair[1],
                )
            )

        avg_packet_size = (
            args.pkt1_size + args.pkt2_size
        ) / 2.0

        if args.lambda_ranges is None:
            lambda_axes = ask_lambda_ranges(pairs)
        else:
            lambda_axes = parse_lambda_ranges(
                args.lambda_ranges,
                len(pairs),
            )

        total_samples = print_generation_plan(
            pairs,
            lambda_axes,
        )

        if total_samples <= 0:
            raise ValueError(
                "El producto cartesiano no contiene muestras."
            )

        if not args.yes:
            if not ask_generation_confirmation(total_samples):
                print("Generación cancelada.")
                return

        generated_count = 0
        first_result_index = next_result_index(
            output_dir
        )

        print(
            "[OK] La primera carpeta será results_{}."
            .format(first_result_index)
        )

        script_paths = resolve_external_generators()

        print("")
        print("[OK] Modo de generación: SCRIPTS EXTERNOS")
        print(
            "[OK] simulationResults será producido por: {}"
            .format(
                script_paths["simulation_results"].name
            )
        )

        with tempfile.TemporaryDirectory(
            prefix="routenet_dataset_",
        ) as temporary_directory:
            workspace_root = Path(temporary_directory)

            modules = prepare_external_workspace(
                workspace_root,
                graph_path,
                routing_path,
                script_paths,
            )

            for combination_number, rates_tuple in enumerate(
                itertools.product(*lambda_axes),
                start=0,
            ):
                rates = list(rates_tuple)
                result_index = (
                    first_result_index
                    + combination_number
                )

                # Se conserva para validar rutas y mostrar rho_enlace_max.
                # Ninguno de estos cálculos produce los TXT del paquete.
                flows = build_flows(
                    pairs,
                    rates,
                    avg_packet_size,
                    number_of_nodes,
                    routing,
                    port_map,
                )

                link_loads = build_queue_loads(
                    graph,
                    flows,
                )

                contents = generate_one_sample_externally(
                    modules=modules,
                    script_paths=script_paths,
                    workspace_root=workspace_root,
                    pairs=pairs,
                    rates=rates,
                    packet_size_1=args.pkt1_size,
                    packet_size_2=args.pkt2_size,
                )

                (
                    generated_global_delay,
                    generated_flow_delays,
                ) = simulation_delays_from_content(
                    contents["simulationResults.txt"],
                    number_of_nodes,
                    pairs,
                )

                result_dir, archive_path = create_result_package(
                    output_dir=output_dir,
                    result_index=result_index,
                    contents=contents,
                )

                generated_count += 1
                current_number = combination_number + 1

                # Evita inundar la terminal cuando hay muchas muestras.
                if (
                    total_samples <= 20
                    or current_number == 1
                    or current_number == total_samples
                    or current_number % 100 == 0
                ):
                    maximum_rho = max(
                        (
                            load.rho
                            for load in link_loads.values()
                        ),
                        default=0.0,
                    )

                    print(
                        "[{}/{}] lambdas={} | rho_enlace_max={} | "
                        "global_delay={} | delays={} | {} + {}"
                        .format(
                            current_number,
                            total_samples,
                            ",".join(
                                fmt(rate)
                                for rate in rates
                            ),
                            fmt(maximum_rho),
                            fmt(generated_global_delay),
                            ",".join(
                                fmt(delay)
                                for delay in generated_flow_delays
                            ),
                            result_dir.name,
                            archive_path.name,
                        )
                    )

        print("")
        print(
            "[OK] {} carpetas results_N y sus .tar.gz generados en '{}'."
            .format(
                generated_count,
                output_dir,
            )
        )

    except (
        OSError,
        ValueError,
        nx.NetworkXError,
        tarfile.TarError,
    ) as error:
        fail(str(error))
    except KeyboardInterrupt:
        print("\nOperación cancelada.", file=sys.stderr)
        raise SystemExit(130)


if __name__ == "__main__":
    main()