#!/usr/bin/env python3
"""Descarga el historico de tipos de cambio del BCE, lo valida, y si esta
bien lo publica en web/motor/cache/eurofxref-hist.xml.

Pensado para el job de CI que refresca el fichero de la web estatica
(sin esto, la web tendria que pedir el XML al BCE en vivo desde el
navegador de cada visitante, lo cual depende de que ECB mantenga
cabeceras CORS permisivas — no queremos depender de eso).

Si el BCE ha cambiado el formato, o el fichero descargado no tiene la
pinta que esperamos, este script termina con un error claro (exit code
!= 0) y NO TOCA el fichero ya publicado: preferimos servir un dato con
uno o varios dias de retraso a servir uno corrupto o mal interpretado.
"""

import shutil
import sys
import tempfile
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tipos_cambio import URL_BCE, ErrorTipoCambio, validar_fichero_bce  # noqa: E402

RUTA_DESTINO = Path(__file__).resolve().parent.parent / "web" / "motor" / "cache" / "eurofxref-hist.xml"


def main():
    with tempfile.TemporaryDirectory() as directorio_temporal:
        ruta_temporal = Path(directorio_temporal) / "eurofxref-hist.xml"

        print(f"Descargando {URL_BCE} ...")
        try:
            with urllib.request.urlopen(URL_BCE, timeout=60) as respuesta:
                contenido = respuesta.read()
        except Exception as error:
            print(f"ERROR: no se ha podido descargar el fichero del BCE: {error}", file=sys.stderr)
            sys.exit(1)

        ruta_temporal.write_bytes(contenido)
        print(f"Descargado ({len(contenido):,} bytes)")

        try:
            ultima_fecha = validar_fichero_bce(str(ruta_temporal))
        except ErrorTipoCambio as error:
            print(f"ERROR: el fichero descargado no pasa la validacion: {error}", file=sys.stderr)
            print("No se publica nada nuevo: se mantiene el ultimo fichero valido.", file=sys.stderr)
            sys.exit(1)

        RUTA_DESTINO.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ruta_temporal, RUTA_DESTINO)
        print(f"Publicado en {RUTA_DESTINO}")
        print(f"Tipo mas reciente disponible: {ultima_fecha.isoformat()}")


if __name__ == "__main__":
    main()
