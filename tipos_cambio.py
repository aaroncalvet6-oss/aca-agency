"""Tipos de cambio oficiales del Banco Central Europeo (BCE).

Descarga UNA VEZ el historico completo (desde 1999) y lo guarda en
disco (cache/eurofxref-hist.xml), asi que las llamadas siguientes no
vuelven a pedirlo por red.

Fuente: https://www.ecb.europa.eu/stats/eurofxref/eurofxref-hist.xml
"""

import bisect
import os
import urllib.request
import xml.etree.ElementTree as ET
from datetime import date

URL_BCE = "https://www.ecb.europa.eu/stats/eurofxref/eurofxref-hist.xml"
RUTA_CACHE_POR_DEFECTO = os.path.join(os.path.dirname(os.path.abspath(__file__)), "cache", "eurofxref-hist.xml")

_NS_EUROFXREF = "http://www.ecb.int/vocabulary/2002-08-01/eurofxref"

_datos_por_ruta = {}   # ruta_cache -> {divisa: (fechas_ordenadas, tipos)}, para no reparsear el XML en cada llamada


def _descargar_si_falta(ruta_cache, url):
    if os.path.exists(ruta_cache):
        return

    os.makedirs(os.path.dirname(ruta_cache), exist_ok=True)
    with urllib.request.urlopen(url, timeout=30) as respuesta:
        contenido = respuesta.read()

    # Fichero temporal + rename atomico para no dejar una cache a medias
    # si la descarga se corta a mitad.
    ruta_temporal = ruta_cache + ".tmp"
    with open(ruta_temporal, "wb") as f:
        f.write(contenido)
    os.replace(ruta_temporal, ruta_cache)


def _parsear_cache(ruta_cache):
    root = ET.parse(ruta_cache).getroot()

    pares_por_divisa = {}   # divisa -> lista de (fecha, tipo) sin ordenar todavia

    for cubo_dia in root.iter(f"{{{_NS_EUROFXREF}}}Cube"):
        fecha_str = cubo_dia.attrib.get("time")
        if fecha_str is None:
            continue   # es un Cube de divisa (hijo), no de dia; se procesa abajo

        fecha = date.fromisoformat(fecha_str)
        for cubo_divisa in cubo_dia:
            divisa = cubo_divisa.attrib.get("currency")
            tipo = cubo_divisa.attrib.get("rate")
            if divisa is None or tipo is None:
                continue
            pares_por_divisa.setdefault(divisa, []).append((fecha, float(tipo)))

    datos = {}
    for divisa, pares in pares_por_divisa.items():
        pares.sort()
        datos[divisa] = ([f for f, _ in pares], [t for _, t in pares])

    return datos


def _obtener_datos(ruta_cache, url):
    if ruta_cache not in _datos_por_ruta:
        _descargar_si_falta(ruta_cache, url)
        _datos_por_ruta[ruta_cache] = _parsear_cache(ruta_cache)

    return _datos_por_ruta[ruta_cache]


def obtener_tipo_cambio(fecha, divisa, ruta_cache=RUTA_CACHE_POR_DEFECTO, url=URL_BCE):
    """Tipo de cambio EUR/divisa oficial del BCE para una fecha (date).

    Si esa fecha no tiene tipo publicado (fin de semana o festivo), se usa
    el ultimo tipo publicado ANTES de esa fecha. Si la fecha es anterior al
    primer dato disponible, lanza ValueError.
    """
    datos = _obtener_datos(ruta_cache, url)

    if divisa not in datos:
        raise ValueError(f"El BCE no publica tipos de cambio para la divisa '{divisa}'")

    fechas, tipos = datos[divisa]

    if fecha < fechas[0]:
        raise ValueError(
            f"No hay tipo de cambio BCE para {divisa} en {fecha.isoformat()}: "
            f"es anterior al primer dato disponible ({fechas[0].isoformat()})"
        )

    idx = bisect.bisect_right(fechas, fecha) - 1
    return tipos[idx]
