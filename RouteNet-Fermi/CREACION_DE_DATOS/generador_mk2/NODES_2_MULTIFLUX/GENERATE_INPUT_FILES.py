"""
generate_input_files.py

Genera el fichero input_files.txt para RouteNet-Fermi, asociando cada
matriz de routing con su grafo correspondiente.

Convención de nombres esperada:
    Graphs/    grafo_XX.txt
    Routings/  Routing_XX_YY.txt   (XX = numero de grafo, YY = numero de routing)

Salida (formato confirmado por la documentacion oficial de RouteNet-Fermi):
    indice;graph_file;routing_file

Uso:
    python generate_input_files.py -->../input_files.txt
"""

import sys
import re
from pathlib import Path
GRAPH_PATH='../graphs'
ROUTING_PATH='../routings'
def extraer_numero_grafo(nombre_archivo):
    """
    Extrae el numero de grafo (XX) de un nombre tipo 'grafo_01.txt' -> '01'
    """
    m = re.search(r"grafo_(\d+)", nombre_archivo, re.IGNORECASE)
    if not m:
        return None
    return m.group(1)


def extraer_numero_grafo_de_routing(nombre_archivo):
    """
    Extrae el numero de grafo (XX) de un nombre tipo
    'Routing_01_02.txt' -> '01'
    """
    m = re.match(r"Routing_(\d+)_(\d+)", nombre_archivo, re.IGNORECASE)
    if not m:
        return None
    return m.group(1)


def main():


    carpeta_graphs = Path(GRAPH_PATH)
    carpeta_routings = Path(ROUTING_PATH)
    salida = "../input_files.txt"

    if not carpeta_graphs.is_dir():
        print(f"Error: la carpeta de grafos no existe: {carpeta_graphs}")
        sys.exit(1)
    if not carpeta_routings.is_dir():
        print(f"Error: la carpeta de routings no existe: {carpeta_routings}")
        sys.exit(1)

    # 1. Mapear numero_de_grafo -> nombre_de_archivo_de_grafo
    grafos_por_numero = {}
    for f in sorted(carpeta_graphs.iterdir()):
        if not f.is_file():
            continue
        if f.suffix.lower() not in (".gml", ".txt"):
            continue
        numero = extraer_numero_grafo(f.name)
        if numero is None:
            print(f"Aviso: no se reconoce el patron 'grafo_XX' en '{f.name}', se omite.")
            continue
        if numero in grafos_por_numero:
            print(f"Aviso: numero de grafo '{numero}' duplicado "
                  f"('{grafos_por_numero[numero]}' y '{f.name}'). Se mantiene el primero.")
            continue
        grafos_por_numero[numero] = f.name

    # 2. Recorrer routings y asociarlos a su grafo
    lineas = []
    routings_sin_grafo = []

    for f in sorted(carpeta_routings.iterdir()):
        if not f.is_file():
            continue
        if f.suffix.lower() != ".txt":
            continue
        numero_grafo = extraer_numero_grafo_de_routing(f.name)
        if numero_grafo is None:
            print(f"Aviso: no se reconoce el patron 'Routing_XX_YY' en '{f.name}', se omite.")
            continue

        nombre_grafo = grafos_por_numero.get(numero_grafo)
        if nombre_grafo is None:
            routings_sin_grafo.append(f.name)
            continue

        lineas.append(f"{nombre_grafo};{f.name}")

    if routings_sin_grafo:
        print("Aviso: los siguientes routings no tienen un grafo asociado y se omiten:")
        for r in routings_sin_grafo:
            print(f"  - {r}")

    # 3. Escribir input_files.txt con indice global
    # newline="\n": DatanetAPI hace [:-1] al leer -> exige LF, no CRLF
    with open(salida, "w", newline="\n") as fout:
        for idx, linea in enumerate(lineas):
            fout.write(f"{idx};{linea}\n")

    print(f"\nGenerado '{salida}' con {len(lineas)} lineas "
          f"(a partir de {len(grafos_por_numero)} grafos).")


if __name__ == "__main__":
    main()