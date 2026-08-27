"""Lector de CSV generico con mapeo de columnas.

En vez de un parser especifico por broker, este modulo lee CUALQUIER CSV
de operaciones y usa un "mapeo" (que columna es cada cosa) para convertirlo
al modelo de operaciones de calculadora.py. Los mapeos frecuentes se
pueden guardar como presets (ver guardar_preset/cargar_preset) para no
tener que repetirlos cada vez que llega un extracto del mismo broker.

Flujo tipico:
    cabeceras, filas_muestra, separador = detectar_csv("extracto.csv")
    # ... se mira que columna es cada cosa y se arma el mapeo ...
    mapeo = {"fecha": "Fecha operacion", "tipo": "Tipo", "valor": "ISIN",
             "cantidad": "Cantidad", "precio": "Precio", "divisa": "Moneda",
             "comision": "Comision"}
    operaciones_por_valor, avisos = leer_operaciones("extracto.csv", mapeo)
    for valor, operaciones in operaciones_por_valor.items():
        ganancia, lotes = calcular_detalle(operaciones)
"""

import csv
import json
import os
from collections import defaultdict
from datetime import datetime

CAMPOS_OBLIGATORIOS = ("fecha", "tipo", "valor", "cantidad", "precio")
CAMPOS_OPCIONALES = ("divisa", "comision")

FORMATOS_FECHA = ("%d/%m/%Y", "%Y-%m-%d", "%d-%m-%Y", "%d.%m.%Y")

RUTA_PRESETS_POR_DEFECTO = os.path.join(os.path.dirname(os.path.abspath(__file__)), "presets_broker.json")

# Categorias reconocidas en la columna "tipo". Todo lo que caiga en
# "ignorar" (o no coincida con nada) se avisa y se excluye del calculo:
# nunca se cuela un dividendo o un traspaso como si fuera una compraventa.
TIPOS_POR_DEFECTO = {
    "compra": ["compra", "buy", "purchase"],
    "venta": ["venta", "sell", "sale"],
    "ignorar": [
        "dividendo", "dividend", "traspaso", "transfer",
        "interes", "interés", "interest", "abono", "retencion", "retención",
    ],
}


class ErrorLectorCSV(Exception):
    """Error claro sobre el propio fichero o el mapeo, no del calculo."""


def _detectar_separador(ruta_csv, encoding):
    with open(ruta_csv, encoding=encoding) as f:
        muestra = f.read(4096)
    try:
        return csv.Sniffer().sniff(muestra, delimiters=",;\t").delimiter
    except csv.Error:
        return ","


def detectar_csv(ruta_csv, num_filas_muestra=5, encoding="utf-8-sig"):
    """Devuelve (cabeceras, filas_muestra, separador) para poder decidir el mapeo."""
    separador = _detectar_separador(ruta_csv, encoding)
    with open(ruta_csv, newline="", encoding=encoding) as f:
        lector = csv.reader(f, delimiter=separador)
        cabeceras = [c.strip() for c in next(lector)]
        filas_muestra = [fila for _, fila in zip(range(num_filas_muestra), lector)]
    return cabeceras, filas_muestra, separador


def _parsear_fecha(texto):
    texto = texto.strip()
    for formato in FORMATOS_FECHA:
        try:
            return datetime.strptime(texto, formato).date()
        except ValueError:
            continue
    raise ErrorLectorCSV(
        f"No reconozco el formato de fecha '{texto}' (formatos soportados: "
        f"{', '.join(FORMATOS_FECHA)})"
    )


def _parsear_numero(texto):
    """Acepta decimales con coma o con punto, y miles con el simbolo contrario.
    Devuelve un string limpio (NUNCA un float): pasar por float perderia la
    ventaja del refactor a Decimal en calculadora.py, que exige convertir
    siempre desde str para no arrastrar error binario."""
    texto = texto.strip().replace("€", "").replace("$", "").replace(" ", "")
    if not texto:
        return None

    negativo = texto.startswith("-")
    if negativo:
        texto = texto[1:]

    if "," in texto and "." in texto:
        if texto.rfind(",") > texto.rfind("."):
            texto = texto.replace(".", "").replace(",", ".")   # "1.234,56"
        else:
            texto = texto.replace(",", "")                      # "1,234.56"
    elif "," in texto:
        texto = texto.replace(",", ".")                          # "3,5"

    try:
        float(texto)   # solo para validar que es un numero; no se usa el resultado
    except ValueError:
        raise ErrorLectorCSV(f"'{texto}' no es un numero valido")

    return ("-" + texto) if negativo else texto


def _clasificar_tipo(valor_bruto, tipos):
    normalizado = valor_bruto.strip().casefold()
    for categoria, alias in tipos.items():
        if normalizado in (a.strip().casefold() for a in alias):
            return categoria
    return None


def leer_operaciones(ruta_csv, mapeo=None, tipos=None, preset=None,
                      ruta_presets=RUTA_PRESETS_POR_DEFECTO, separador=None,
                      encoding="utf-8-sig"):
    """Lee un CSV de operaciones y lo convierte al modelo de calculadora.py.

    mapeo: {"fecha": columna, "tipo": columna, "valor": columna,
            "cantidad": columna, "precio": columna,
            "divisa": columna (opcional, si falta se asume "EUR"),
            "comision": columna (opcional, si falta se asume 0)}
    tipos: como TIPOS_POR_DEFECTO, para reconocer que texto de la columna
           "tipo" es compra/venta/ignorar. Si no se da, se usa TIPOS_POR_DEFECTO.
    preset: nombre de un preset guardado con guardar_preset(); si se da,
            sustituye a mapeo/tipos (no hace falta pasar los dos).

    Devuelve (operaciones_por_valor, avisos):
      operaciones_por_valor: {valor_o_isin: [operacion, ...]}, cada lista ya
        ordenada por fecha y lista para pasar directamente a
        calculadora.calcular_detalle(). El motor de calculadora.py hace FIFO
        sobre una unica lista, por eso aqui se separa por valor: cada ISIN
        es una serie independiente.
      avisos: filas que se han ignorado (tipo no reconocido, numero invalido...)
        o que no son compraventas (dividendos, traspasos...), para que se
        puedan revisar. Ninguna fila de aqui entra en operaciones_por_valor.
    """
    if preset is not None:
        mapeo, tipos = cargar_preset(preset, ruta_presets)

    if not mapeo:
        raise ErrorLectorCSV("Hace falta un 'mapeo' (o un 'preset' ya guardado)")

    faltan = [campo for campo in CAMPOS_OBLIGATORIOS if campo not in mapeo]
    if faltan:
        raise ErrorLectorCSV(f"Falta mapear estas columnas obligatorias: {', '.join(faltan)}")

    tipos = tipos or TIPOS_POR_DEFECTO
    separador = separador or _detectar_separador(ruta_csv, encoding)

    with open(ruta_csv, newline="", encoding=encoding) as f:
        lector = csv.DictReader(f, delimiter=separador)
        lector.fieldnames = [c.strip() for c in (lector.fieldnames or [])]
        cabeceras = lector.fieldnames

        columnas_del_mapeo = list(mapeo.values())
        faltan_en_csv = [columna for columna in columnas_del_mapeo if columna not in cabeceras]
        if faltan_en_csv:
            raise ErrorLectorCSV(
                f"El CSV no tiene estas columnas del mapeo: {', '.join(faltan_en_csv)} "
                f"(columnas disponibles: {', '.join(cabeceras)})"
            )

        pendientes_por_valor = defaultdict(list)   # valor -> [(fecha_date, operacion), ...]
        avisos = []

        for num_fila, fila in enumerate(lector, start=2):   # la fila 1 es la cabecera
            tipo_bruto = fila[mapeo["tipo"]]
            categoria = _clasificar_tipo(tipo_bruto, tipos)

            if categoria is None:
                avisos.append(f"Fila {num_fila}: tipo '{tipo_bruto}' no reconocido, se ignora")
                continue
            if categoria == "ignorar":
                avisos.append(f"Fila {num_fila}: '{tipo_bruto}' no es una compraventa, se ignora")
                continue

            try:
                fecha = _parsear_fecha(fila[mapeo["fecha"]])
                cantidad = _parsear_numero(fila[mapeo["cantidad"]])
                precio = _parsear_numero(fila[mapeo["precio"]])
                comision = _parsear_numero(fila[mapeo["comision"]]) if "comision" in mapeo else None
            except ErrorLectorCSV as error:
                avisos.append(f"Fila {num_fila}: {error}, se ignora")
                continue

            if cantidad is None or precio is None:
                avisos.append(f"Fila {num_fila}: falta cantidad o precio, se ignora")
                continue

            valor = fila[mapeo["valor"]].strip()
            divisa = fila[mapeo["divisa"]].strip() if "divisa" in mapeo else ""

            operacion = {
                "fecha": fecha.strftime("%d/%m/%Y"),
                "tipo": categoria,
                "acciones": cantidad,
                "precio_usd": precio,   # nombre historico del campo: precio en la divisa de la operacion
                "comision_eur": comision if comision is not None else "0",
                "divisa": divisa or "EUR",
            }
            pendientes_por_valor[valor].append((fecha, operacion))

    operaciones_por_valor = {}
    for valor, pendientes in pendientes_por_valor.items():
        pendientes.sort(key=lambda par: par[0])   # FIFO exige orden cronologico
        operaciones_por_valor[valor] = [operacion for _, operacion in pendientes]

    return operaciones_por_valor, avisos


def guardar_preset(nombre, mapeo, tipos=None, ruta_presets=RUTA_PRESETS_POR_DEFECTO):
    presets = _cargar_todos_los_presets(ruta_presets)
    presets[nombre] = {"mapeo": mapeo, "tipos": tipos or TIPOS_POR_DEFECTO}
    os.makedirs(os.path.dirname(ruta_presets) or ".", exist_ok=True)
    with open(ruta_presets, "w", encoding="utf-8") as f:
        json.dump(presets, f, ensure_ascii=False, indent=2, sort_keys=True)


def cargar_preset(nombre, ruta_presets=RUTA_PRESETS_POR_DEFECTO):
    presets = _cargar_todos_los_presets(ruta_presets)
    if nombre not in presets:
        disponibles = ", ".join(sorted(presets)) or "(ninguno)"
        raise ErrorLectorCSV(f"No existe el preset '{nombre}'. Presets disponibles: {disponibles}")
    preset = presets[nombre]
    return preset["mapeo"], preset.get("tipos", TIPOS_POR_DEFECTO)


def listar_presets(ruta_presets=RUTA_PRESETS_POR_DEFECTO):
    return sorted(_cargar_todos_los_presets(ruta_presets).keys())


def _cargar_todos_los_presets(ruta_presets):
    if not os.path.exists(ruta_presets):
        return {}
    with open(ruta_presets, encoding="utf-8") as f:
        return json.load(f)
