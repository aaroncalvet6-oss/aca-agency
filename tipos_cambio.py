"""Tipos de cambio oficiales del Banco Central Europeo (BCE).

Descarga UNA VEZ el historico completo (desde 1999) y lo guarda en
disco (cache/eurofxref-hist.xml), asi que las llamadas siguientes no
vuelven a pedirlo por red.

Fuente: https://www.ecb.europa.eu/stats/eurofxref/eurofxref-hist.xml

Control de frescura (critico en algo fiscal): este modulo NUNCA inventa
un tipo para una fecha futura respecto a los datos que tiene. Si pides
un tipo posterior al ultimo publicado, TipoAunNoDisponible explota en
vez de devolver silenciosamente el ultimo conocido.
"""

import bisect
import os
import urllib.request
import xml.etree.ElementTree as ET
from datetime import date, timedelta

URL_BCE = "https://www.ecb.europa.eu/stats/eurofxref/eurofxref-hist.xml"
RUTA_CACHE_POR_DEFECTO = os.path.join(os.path.dirname(os.path.abspath(__file__)), "cache", "eurofxref-hist.xml")

_NS_EUROFXREF = "http://www.ecb.int/vocabulary/2002-08-01/eurofxref"

# Umbral por defecto para avisar de que el fichero cacheado lleva
# demasiado tiempo sin refrescarse. El BCE no publica fin de semana (2
# dias) ni festivos de la zona euro, asi que un margen de 5 dias evita
# falsos avisos en un puente largo pero detecta que el job de refresco
# lleva mas de una semana sin correr.
UMBRAL_DIAS_ANTIGUEDAD = 5

_datos_por_ruta = {}   # ruta_cache -> {divisa: (fechas_ordenadas, tipos)}, para no reparsear el XML en cada llamada


class ErrorTipoCambio(ValueError):
    """Base de los errores de este modulo. Subclase de ValueError por
    compatibilidad con codigo que ya capturaba ValueError a secas."""


class DivisaNoDisponible(ErrorTipoCambio):
    """El BCE no publica tipos para esta divisa (o el fichero no la trae)."""


class FechaAnteriorAlHistorico(ErrorTipoCambio):
    """La fecha pedida es anterior al primer dato disponible (antes de 1999)."""


class TipoAunNoDisponible(ErrorTipoCambio):
    """La fecha pedida es POSTERIOR al ultimo dato disponible.

    Esto NO se resuelve con el tipo mas cercano: seria inventar un dato.
    Puede pasar porque la operacion es de hoy/ayer y el BCE aun no lo ha
    publicado, o porque nuestro fichero cacheado lleva tiempo sin
    refrescarse. En cualquier caso, la operacion no se puede calcular
    todavia y hay que decirlo, no aproximarlo.
    """


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


def _serie(datos, divisa):
    if divisa not in datos:
        raise DivisaNoDisponible(f"El BCE no publica tipos de cambio para la divisa '{divisa}'")
    return datos[divisa]


def obtener_tipo_cambio(fecha, divisa, ruta_cache=RUTA_CACHE_POR_DEFECTO, url=URL_BCE):
    """Tipo de cambio EUR/divisa oficial del BCE para una fecha (date).

    Si esa fecha no tiene tipo publicado (fin de semana o festivo), se usa
    el ultimo tipo publicado ANTES de esa fecha. Si la fecha es anterior al
    primer dato disponible, lanza FechaAnteriorAlHistorico. Si la fecha es
    POSTERIOR al ultimo dato disponible, lanza TipoAunNoDisponible: nunca
    se usa el "mas cercano" para una fecha futura, seria inventarlo.
    """
    datos = _obtener_datos(ruta_cache, url)
    fechas, tipos = _serie(datos, divisa)

    if fecha < fechas[0]:
        raise FechaAnteriorAlHistorico(
            f"No hay tipo de cambio BCE para {divisa} en {fecha.isoformat()}: "
            f"es anterior al primer dato disponible ({fechas[0].isoformat()})"
        )

    if fecha > fechas[-1]:
        raise TipoAunNoDisponible(
            f"Todavia no hay tipo de cambio BCE para {divisa} en {fecha.isoformat()}: "
            f"el ultimo dato disponible es del {fechas[-1].isoformat()}. Esta operacion "
            f"no se puede calcular todavia, no se usa un tipo aproximado."
        )

    idx = bisect.bisect_right(fechas, fecha) - 1
    return tipos[idx]


def fecha_mas_reciente(divisa=None, ruta_cache=RUTA_CACHE_POR_DEFECTO, url=URL_BCE):
    """Fecha del ultimo tipo publicado en el fichero cacheado.

    Con divisa=None (por defecto), la mas reciente entre TODAS las series
    (en el fichero real del BCE todas las divisas se publican el mismo
    dia, asi que deberian coincidir; tomar el maximo es la opcion segura
    si alguna difiriese)."""
    datos = _obtener_datos(ruta_cache, url)

    if divisa is not None:
        fechas, _ = _serie(datos, divisa)
        return fechas[-1]

    if not datos:
        raise ErrorTipoCambio("El fichero de tipos de cambio no tiene ninguna divisa")

    return max(fechas[-1] for fechas, _ in datos.values())


def antiguedad_en_dias(divisa=None, ruta_cache=RUTA_CACHE_POR_DEFECTO, url=URL_BCE, hoy=None):
    """Dias transcurridos entre hoy (o la fecha dada) y el ultimo tipo
    disponible en el fichero cacheado. Sirve para avisar en la pagina si
    el fichero lleva demasiado tiempo sin refrescarse."""
    hoy = hoy or date.today()
    return (hoy - fecha_mas_reciente(divisa, ruta_cache, url)).days


def fichero_desactualizado(umbral_dias=UMBRAL_DIAS_ANTIGUEDAD, divisa=None,
                            ruta_cache=RUTA_CACHE_POR_DEFECTO, url=URL_BCE, hoy=None):
    """True si el fichero cacheado tiene mas de umbral_dias de antiguedad."""
    return antiguedad_en_dias(divisa, ruta_cache, url, hoy) > umbral_dias


def validar_fichero_bce(ruta_cache, divisas_esperadas=("USD", "GBP"), dias_maximos_sin_publicar=30):
    """Comprueba que el XML cacheado tiene la pinta que esperamos.

    Pensado para el job de refresco (CI): si el BCE cambia el formato del
    fichero, o el parseo deja de encontrar lo que debería, esto tiene que
    fallar de forma RUIDOSA (excepcion clara) y no dejar pasar un fichero
    mal leido o incompleto. Devuelve la fecha mas reciente encontrada si
    todo esta en orden.
    """
    try:
        datos = _parsear_cache(ruta_cache)
    except ET.ParseError as error:
        raise ErrorTipoCambio(f"El XML del BCE no se puede parsear, puede haber cambiado el formato: {error}")

    if not datos:
        raise ErrorTipoCambio(
            "El fichero del BCE no tiene ninguna divisa reconocible: revisa si ha cambiado "
            "el namespace o la estructura de <Cube>"
        )

    for divisa in divisas_esperadas:
        if divisa not in datos:
            raise ErrorTipoCambio(f"Falta la divisa '{divisa}' en el fichero del BCE: revisa el formato")

        fechas, tipos = datos[divisa]

        if len(fechas) < 100:
            raise ErrorTipoCambio(
                f"La divisa '{divisa}' solo tiene {len(fechas)} fechas en el fichero: "
                f"parece incompleto (se esperaban miles de dias de historico)"
            )

        if any(tipo <= 0 for tipo in tipos):
            raise ErrorTipoCambio(f"La divisa '{divisa}' tiene algun tipo de cambio <= 0: revisa el parseo")

    ultima_fecha = max(datos[divisa][0][-1] for divisa in divisas_esperadas)
    limite = date.today() - timedelta(days=dias_maximos_sin_publicar)
    if ultima_fecha < limite:
        raise ErrorTipoCambio(
            f"El dato mas reciente del fichero es del {ultima_fecha.isoformat()}, "
            f"hace mas de {dias_maximos_sin_publicar} dias: revisa si el BCE ha cambiado algo "
            f"o si la descarga esta trayendo un fichero viejo"
        )

    return ultima_fecha
