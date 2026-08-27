#!/usr/bin/env python3
"""Copia el motor de calculo (los .py de la raiz del repo) a
web/motor/, para que la carpeta web/ sea autocontenida y desplegable
como sitio estatico independiente (Pyodide carga esos ficheros por
fetch, en el mismo origen que la pagina).

No se versiona un symlink ni se duplica el codigo a mano: este script
es la unica fuente de verdad de que copiar, y el job de CI lo ejecuta
en cada push que toque el motor.
"""

import shutil
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
DESTINO = RAIZ / "web" / "motor"

FICHEROS = [
    "calculadora.py",
    "tipos_cambio.py",
    "lector_csv.py",
    "motor_web.py",
    "presets_broker.json",
]


def main():
    DESTINO.mkdir(parents=True, exist_ok=True)

    copiados = []
    for nombre in FICHEROS:
        origen = RAIZ / nombre
        if not origen.exists():
            print(f"ERROR: no existe {origen}", file=sys.stderr)
            sys.exit(1)
        shutil.copyfile(origen, DESTINO / nombre)
        copiados.append(nombre)

    print(f"Sincronizados en {DESTINO}: {', '.join(copiados)}")


if __name__ == "__main__":
    main()
