Este es el primer readme de este proyecto, y viene a decir una cosa:

Este proyecto no contiene las carpetas de RouteNet-Fermi, sino que son un complemento a éste y se pueden bajar desde https://github.com/BNN-UPC/RouteNet-Fermi.

Lo primero sería bajar el repositorio original con "git clone https://github.com/BNN-UPC/RouteNet-Fermi" en la carpeta en la que se desea trabajar
Lo siguiente es buscar en sendos repositorios (éste y el original de RouteNet-Fermi) el nivel de la carpeta "RouteNet-Fermi", el cual será la raiz de los dos.

A continuación va a ir añadiendo carpetas de mi repositorio al repositorio oficial, ya que como he dicho: esto es un complemento.
Y donde haya archivos en misma subcarpeta dentro de los 2 folders diferentes (el original de RouteNet-F y éste), se copiarán los archivos a la subcarpeta del folder de RouteNet-F correspondiente. Ejemplo:


Ejemplo:

traffic_models/	(mio)			traffic_models/											traffic_models/
	C.py							A.py					----Resultado--->				A.py
									B.py													B.py
																							C.py   
																							
Repetir esto para todos los archivos que haya en el árbol en todas las subcarpetas; de ese modo, estarán todos los complementos 
