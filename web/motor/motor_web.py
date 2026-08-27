"""Punto de entrada unico para la web (llamado desde Pyodide).

Ata lector_csv + calculadora + tipos_cambio y devuelve un dict ya listo
para convertir a JSON y pintar en la pagina.

Principio rector: nunca se cuela un numero a medias.
- Si un valor (ISIN) no se puede calcular del todo (tipo de cambio no
  disponible todavia, fecha invalida...), ese valor se marca con su
  error y NO lleva una ganancia calculada.
- Si algun valor falla, el total general de ganancias patrimoniales se
  marca como no calculable en vez de sumar solo lo que salio bien y
  presentarlo como si fuera el total real.
- Los dividendos se calculan aparte y no dependen de si el FIFO de
  algun valor fallo (son datos independientes de la ganancia patrimonial).
"""

import contextlib
import io
from decimal import Decimal

import lector_csv
import tipos_cambio
from calculadora import calcular_desglose


def _dinero(valor):
    """Decimal -> string con 2 decimales, listo para mostrar o exportar."""
    return str(valor.quantize(Decimal("0.01")))


def info_frescura_bce(divisa="USD"):
    """Fecha del ultimo tipo BCE disponible y si el fichero cacheado esta
    desactualizado. Se muestra siempre en la pagina, se pueda o no
    calcular nada (por eso es una funcion aparte de procesar_csv)."""
    try:
        fecha = tipos_cambio.fecha_mas_reciente(divisa=divisa)
        dias = tipos_cambio.antiguedad_en_dias(divisa=divisa)
        return {
            "ok": True,
            "fecha_mas_reciente": fecha.strftime("%d/%m/%Y"),
            "dias_de_antiguedad": dias,
            "desactualizado": dias > tipos_cambio.UMBRAL_DIAS_ANTIGUEDAD,
            "error": None,
        }
    except Exception as error:
        return {
            "ok": False,
            "fecha_mas_reciente": None,
            "dias_de_antiguedad": None,
            "desactualizado": None,
            "error": str(error),
        }


def procesar_csv(contenido_csv, mapeo, tipos=None):
    """Lee el CSV (ya en memoria, nunca en disco) y calcula todo.

    Devuelve un dict JSON-serializable:
      {
        "frescura_bce": {...},
        "error_lectura": str | None,        # si el CSV/mapeo es invalido, nada mas se calcula
        "avisos_lectura": [str, ...],       # filas ignoradas al leer el CSV
        "valores": {
          isin: {
            "error": str | None,
            "ganancia": "X.XX" | None,      # None si "error" no es None
            "desglose": [{"fecha", "resultado_bruto", "bloqueado", "resultado_declarado"}, ...] | None,
            "lotes_pendientes": [{"fecha", "acciones"}, ...] | None,
          },
          ...
        },
        "dividendos": {
          "bruto_total": "X.XX", "retencion_total": "X.XX",
          "por_valor": {isin: {"bruto": "X.XX", "retencion": "X.XX"}},
        } | None,
        "totales": {
          "ganancia_patrimonial": "X.XX" | None,   # None si "completo" es False
          "completo": bool,   # False si algun valor no se pudo calcular
        },
      }
    """
    resultado = {
        "frescura_bce": info_frescura_bce(),
        "error_lectura": None,
        "avisos_lectura": [],
        "valores": {},
        "dividendos": None,
        "totales": {"ganancia_patrimonial": None, "completo": True},
    }

    try:
        operaciones_por_valor, dividendos_por_valor, avisos = lector_csv.leer_operaciones(
            contenido=contenido_csv, mapeo=mapeo, tipos=tipos,
        )
    except lector_csv.ErrorLectorCSV as error:
        resultado["error_lectura"] = str(error)
        resultado["totales"]["completo"] = False
        return resultado

    resultado["avisos_lectura"] = avisos

    ganancia_total = Decimal("0")
    algun_error = False

    for valor, operaciones in operaciones_por_valor.items():
        try:
            # calcular_desglose imprime una traza de depuracion pensada para
            # la consola de un script, no para la web: aqui no tiene lector.
            with contextlib.redirect_stdout(io.StringIO()):
                ganancia, lotes_finales, detalle_ventas = calcular_desglose(operaciones)
        except Exception as error:
            algun_error = True
            resultado["valores"][valor] = {
                "error": str(error),
                "ganancia": None,
                "desglose": None,
                "lotes_pendientes": None,
            }
            continue

        ganancia_total += ganancia
        resultado["valores"][valor] = {
            "error": None,
            "ganancia": _dinero(ganancia),
            "desglose": [
                {
                    "fecha": fila["fecha"],
                    "resultado_bruto": _dinero(fila["resultado_bruto"]),
                    "bloqueado": _dinero(fila["bloqueado"]),
                    "resultado_declarado": _dinero(fila["resultado_declarado"]),
                }
                for fila in detalle_ventas
            ],
            "lotes_pendientes": [
                {"fecha": lote["fecha"], "acciones": str(lote["acciones"])}
                for lote in lotes_finales
            ],
        }

    resumen_dividendos = lector_csv.resumir_dividendos(dividendos_por_valor)
    resultado["dividendos"] = {
        "bruto_total": _dinero(resumen_dividendos["bruto_total"]),
        "retencion_total": _dinero(resumen_dividendos["retencion_total"]),
        "por_valor": {
            valor: {"bruto": _dinero(datos["bruto"]), "retencion": _dinero(datos["retencion"])}
            for valor, datos in resumen_dividendos["por_valor"].items()
        },
    }

    resultado["totales"]["completo"] = not algun_error
    # Si algun valor fallo, el total NO se calcula sumando solo lo que
    # salio bien: eso seria un numero a medias disfrazado de total.
    resultado["totales"]["ganancia_patrimonial"] = None if algun_error else _dinero(ganancia_total)

    return resultado
