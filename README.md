# RouteNet-Fermi M/M/1 Dataset Generator
[UNDER CONSTRUCTION]
This is a RouteNet-Fermi sample generator, which allows to generate 1 simulation each time and is designed for studying M/M/1 networks with multiple sources and destinations.


M/M/1 systems have a very interesting property: infinite queue sizes. This property is a key factor on simplifying mathematical expressions while we can get more restrictive results, which can be better rather than having exact or lower results.

RouteNet-Fermi is a brand-new way to look and work with networks. Designed by BNN team (Barcelona Neural Networking Center), from UPC (Universitat Politècnica de Catalunya) has a GitHub repository publicly available for everyone: https://github.com/BNN-UPC/RouteNet-Fermi
And the programs have been made by looking the dataset formats, publicly published in https://github.com/BNN-UPC/RouteNet-Fermi
using format v6
## Screenshots

| Graph editor | Graph view |
|:---:|:---:|
| <img src="docs/images/GrafoGenVista.png" width="450"> | <img src="docs/images/Grafovisto.png" width="450"> |

| Flow information | Node information |
|:---:|:---:|
| <img src="docs/images/Infoflujos.png" width="450"> | <img src="docs/images/Infonodos.png" width="450"> |

| PDF distribution | CDF distribution |
|:---:|:---:|
| <img src="docs/images/DistribVisorPDF.png" width="450"> | <img src="docs/images/DistribVisorCDF.png" width="450"> |

| NPY visualizer | Console log |
|:---:|:---:|
| <img src="docs/images/NPYvisor.png" width="450"> | <img src="docs/images/consola.png" width="450"> |


Currently, there isn't a routing matrix generator, so routing matrices are created on demand for whatever you want to analyze, either manually or with AI assistance.
