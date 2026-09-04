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
    operaciones_por_valor, dividendos_por_valor, avisos = leer_operaciones("extracto.csv", mapeo)
    for valor, operaciones in operaciones_por_valor.items():
        ganancia, lotes = calcular_detalle(operaciones)
    resumen = resumir_dividendos(dividendos_por_valor)   # rendimiento del capital mobiliario

Todas las funciones que leen un CSV aceptan tanto una ruta de fichero
(ruta_csv) como el texto ya en memoria (contenido). Esto ultimo es lo
que usa la web: el fichero que arrastra el usuario se lee con el File
API del navegador y se pasa su texto directamente, sin escribirlo a
ningun disco (ni siquiera temporal).
"""

import csv
import io
import json
import os
import re
import unicodedata
from collections import defaultdict
from datetime import datetime
from decimal import Decimal

CAMPOS_OBLIGATORIOS = ("fecha", "tipo", "valor", "cantidad", "precio")
CAMPOS_OPCIONALES = ("divisa", "comision")

FORMATOS_FECHA = ("%d/%m/%Y", "%Y-%m-%d", "%Y/%m/%d", "%d-%m-%Y", "%d.%m.%Y")

RUTA_PRESETS_POR_DEFECTO = os.path.join(os.path.dirname(os.path.abspath(__file__)), "presets_broker.json")

# Sinonimos habituales para adivinar el mapeo por el nombre de la
# cabecera (ver sugerir_mapeo). Los nombres de aqui se comparan ya
# normalizados (ver _normalizar_cabecera): minusculas, sin acentos, sin
# espacios/guiones/parentesis, asi que "Nº Acciones" y "n acciones"
# coinciden igual que "numeroacciones".
SINONIMOS_CAMPO = {
    "fecha": [
        "fecha", "date", "fecha operacion", "fecha ejecucion", "fecha contratacion",
        "trade date", "value date", "fecha valor", "fecha transaccion",
    ],
    "tipo": [
        "tipo", "type", "tipo operacion", "transaction type", "operacion",
        "accion", "movimiento", "concepto",
    ],
    "valor": [
        "isin", "valor", "ticker", "symbol", "security", "nombre",
        "activo", "instrumento", "producto", "titulo", "security name",
    ],
    "cantidad": [
        "cantidad", "quantity", "shares", "acciones", "unidades",
        "n acciones", "numero acciones", "units", "titulos", "nominal",
    ],
    "precio": [
        "precio", "price", "precio unitario", "unit price", "cotizacion",
        "importe unitario", "share price", "precio accion",
    ],
    "divisa": ["divisa", "moneda", "currency", "ccy", "divisa operacion"],
    "comision": [
        "comision", "comisiones", "comision eur", "fee", "fees",
        "gastos", "coste", "costes", "charges", "commission", "comision operacion",
    ],
}


def _normalizar_cabecera(texto):
    """Minusculas, sin acentos, sin espacios/guiones/parentesis, para
    comparar cabeceras de forma tolerante a mayusculas, tildes y
    variaciones de formato ("Fecha operación" ~ "fecha_operacion").

    Los indicadores ordinales "º"/"ª" (p.ej. "Nº Acciones") se quitan
    antes de la normalizacion NFKD: esta los descompone en una letra
    superindice ("º" -> "o" superindice) que sobrevive al paso a ascii,
    coloandose como si fuera una letra mas ("Nº" acabaria dando "no" en
    vez de "n") y rompiendo la comparacion con sinonimos como "n acciones".
    """
    texto = texto.replace("º", "").replace("ª", "")
    texto = unicodedata.normalize("NFKD", texto).encode("ascii", "ignore").decode("ascii")
    return re.sub(r"[^a-z0-9]+", "", texto.strip().lower())


def sugerir_mapeo(cabeceras):
    """Adivina que columna es cada campo por el nombre de la cabecera, con
    tolerancia a mayusculas/acentos/espacios y sinonimos habituales
    (importe, coste, comisiones, divisa, currency, date, quantity...).

    Devuelve un dict {campo: columna} solo con los campos que se han
    podido adivinar CON CONFIANZA (una unica cabecera coincide con ese
    campo); el resto se deja fuera para que el usuario elija a mano. Si
    dos cabeceras distintas coinciden con el mismo campo, tampoco se
    sugiere nada para ese campo (mejor no adivinar mal que adivinar a
    ciegas)."""
    normalizadas = {cabecera: _normalizar_cabecera(cabecera) for cabecera in cabeceras}

    sugerencia = {}
    for campo, sinonimos in SINONIMOS_CAMPO.items():
        sinonimos_normalizados = {_normalizar_cabecera(s) for s in sinonimos}
        candidatos = [c for c, norm in normalizadas.items() if norm in sinonimos_normalizados]
        if len(candidatos) == 1:
            sugerencia[campo] = candidatos[0]

    return sugerencia

# Categorias reconocidas en la columna "tipo". "dividendo" se extrae aparte
# (es rendimiento del capital mobiliario, casilla distinta a las ganancias
# patrimoniales: NUNCA debe acabar como un aviso descartable). Todo lo que
# caiga en "ignorar" (o no coincida con nada) se avisa y se excluye del
# calculo sin mas: traspasos, intereses, etc. no son operaciones de compraventa.
TIPOS_POR_DEFECTO = {
    "compra": ["compra", "buy", "purchase"],
    "venta": ["venta", "sell", "sale"],
    "dividendo": ["dividendo", "dividend"],
    "ignorar": [
        "traspaso", "transfer",
        "interes", "interés", "interest", "abono", "retencion", "retención",
    ],
}


class ErrorLectorCSV(Exception):
    """Error claro sobre el propio fichero o el mapeo, no del calculo."""


def _texto_completo(ruta_csv, contenido, encoding):
    if contenido is not None:
        return contenido
    if ruta_csv is None:
        raise ErrorLectorCSV("Hace falta 'ruta_csv' o 'contenido'")
    with open(ruta_csv, encoding=encoding) as f:
        return f.read()


def _detectar_separador(texto):
    try:
        return csv.Sniffer().sniff(texto[:4096], delimiters=",;\t").delimiter
    except csv.Error:
        return ","


def detectar_csv(ruta_csv=None, num_filas_muestra=5, encoding="utf-8-sig", contenido=None):
    """Devuelve (cabeceras, filas_muestra, separador) para poder decidir el mapeo.

    Acepta una ruta de fichero (ruta_csv) o el texto ya en memoria
    (contenido) — ver el docstring del modulo."""
    texto = _texto_completo(ruta_csv, contenido, encoding)
    separador = _detectar_separador(texto)
    lector = csv.reader(io.StringIO(texto), delimiter=separador)
    cabeceras = [c.strip() for c in next(lector)]
    filas_muestra = [fila for _, fila in zip(range(num_filas_muestra), lector)]
    return cabeceras, filas_muestra, separador


# Si la fecha trae hora pegada (ISO 8601 con "T", o "AAAA-MM-DD HH:MM:SS"),
# nos quedamos solo con la parte de fecha antes de probar FORMATOS_FECHA:
# para el FIFO y para el tipo de cambio del BCE solo cuenta el DIA de la
# operacion, nunca la hora ni la zona horaria ("2024-01-15T09:31:22Z",
# "2024-01-15T09:31:22+02:00", "2024-01-15 09:31:22" -> "2024-01-15").
_RE_FECHA_CON_HORA = re.compile(r"^(\d{4}-\d{2}-\d{2}|\d{2}[/.-]\d{2}[/.-]\d{4})[T ]")


def _parsear_fecha(texto):
    texto = texto.strip()
    coincide = _RE_FECHA_CON_HORA.match(texto)
    if coincide:
        texto = coincide.group(1)
    for formato in FORMATOS_FECHA:
        try:
            return datetime.strptime(texto, formato).date()
        except ValueError:
            continue
    raise ErrorLectorCSV(
        f"No reconozco el formato de fecha '{texto}' (formatos soportados: "
        f"{', '.join(FORMATOS_FECHA)})"
    )


def _formatear_fecha(fecha):
    """DD/MM/AAAA a partir de los enteros .day/.month/.year del date, sin
    pasar por strftime: un f-string con enteros no tiene ninguna
    ambiguedad posible de dia/mes, a diferencia de un especificador de
    formato que dependa de como lo interprete la libc de turno."""
    return f"{fecha.day:02d}/{fecha.month:02d}/{fecha.year:04d}"


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


def leer_operaciones(ruta_csv=None, mapeo=None, tipos=None, preset=None,
                      ruta_presets=RUTA_PRESETS_POR_DEFECTO, separador=None,
                      encoding="utf-8-sig", contenido=None,
                      comision_en_divisa_operacion=False,
                      cantidad_valor_absoluto=False):
    """Lee un CSV de operaciones y lo convierte al modelo de calculadora.py.

    Acepta una ruta de fichero (ruta_csv) o el texto ya en memoria
    (contenido) — ver el docstring del modulo.

    mapeo: {"fecha": columna, "tipo": columna, "valor": columna,
            "cantidad": columna, "precio": columna,
            "divisa": columna (opcional, si falta se asume "EUR"),
            "comision": columna (opcional; en compras/ventas es la comision,
                en dividendos se interpreta como la retencion practicada;
                si falta la columna, se asume 0)}
    tipos: como TIPOS_POR_DEFECTO, para reconocer que texto de la columna
           "tipo" es compra/venta/dividendo/ignorar. Si no se da, se usa
           TIPOS_POR_DEFECTO.
    preset: nombre de un preset guardado con guardar_preset(); si se da,
            sustituye a mapeo/tipos/comision_en_divisa_operacion (no hace
            falta pasar los tres).
    comision_en_divisa_operacion: por defecto la comision de compras/ventas
        se asume en EUR (asi la cobran la mayoria de brokers). Si es True,
        se interpreta en la MISMA divisa que el importe de esa fila y se
        convierte a euros con el mismo tipo de cambio y la misma fecha que
        el importe principal (ver calculadora.a_euros). No afecta a la
        retencion de los dividendos, que siempre se trata en EUR.
    cantidad_valor_absoluto: algunos brokers exportan las ventas con la
        cantidad en negativo (p.ej. "-8"). calculadora.py exige que
        "acciones" sea siempre positivo — el signo NUNCA decide si es
        compra o venta, eso lo dice solo la columna "tipo" (y su mapeo de
        valores, ver sugerir_mapeo/tipos mas arriba). Si esto es True, una
        cantidad negativa se guarda en positivo (abs); si es False (por
        defecto) se guarda tal cual viene en el fichero.

    Devuelve (operaciones_por_valor, dividendos_por_valor, avisos):
      operaciones_por_valor: {valor_o_isin: [operacion, ...]}, cada lista ya
        ordenada por fecha y lista para pasar directamente a
        calculadora.calcular_detalle(). El motor de calculadora.py hace FIFO
        sobre una unica lista, por eso aqui se separa por valor: cada ISIN
        es una serie independiente.
      dividendos_por_valor: {valor_o_isin: [{"fecha": "DD/MM/AAAA",
        "bruto": Decimal, "retencion": Decimal}, ...]}. Los dividendos son
        rendimiento del capital mobiliario, NO ganancia patrimonial: van en
        una casilla distinta de la renta y por eso se devuelven aparte, sin
        pasar nunca por calcular_detalle(). Usa resumir_dividendos() para
        los totales.
      avisos: filas que se han ignorado por tipo no reconocido, numero
        invalido, o por no ser una compraventa/dividendo (traspasos,
        intereses...). Ninguna fila de aqui entra en operaciones_por_valor
        ni en dividendos_por_valor.
    """
    if preset is not None:
        mapeo, tipos, comision_en_divisa_operacion, cantidad_valor_absoluto = cargar_preset(preset, ruta_presets)

    if not mapeo:
        raise ErrorLectorCSV("Hace falta un 'mapeo' (o un 'preset' ya guardado)")

    faltan = [campo for campo in CAMPOS_OBLIGATORIOS if not mapeo.get(campo)]
    if faltan:
        raise ErrorLectorCSV(f"Falta mapear estas columnas obligatorias: {', '.join(faltan)}")

    # La misma columna no puede servir para dos campos a la vez (p.ej.
    # "Precio" y "Cantidad" apuntando los dos a la columna "Cantidad"
    # porque el fichero no trae una columna de precio de verdad): eso da
    # numeros sin sentido en vez de avisar de que falta esa columna.
    campos_por_columna = defaultdict(list)
    for campo, columna in mapeo.items():
        if columna:
            campos_por_columna[columna].append(campo)
    columnas_repetidas = {columna: campos for columna, campos in campos_por_columna.items() if len(campos) > 1}
    if columnas_repetidas:
        detalles = "; ".join(
            f"'{columna}' esta asignada a la vez a {' y '.join(campos)}"
            for columna, campos in columnas_repetidas.items()
        )
        raise ErrorLectorCSV(
            f"No puedes usar la misma columna para varios campos: {detalles}. "
            f"Si tu fichero no tiene una columna propia para alguno de esos campos, "
            f"esa operacion no se puede calcular."
        )

    tipos = tipos or TIPOS_POR_DEFECTO
    texto = _texto_completo(ruta_csv, contenido, encoding)
    separador = separador or _detectar_separador(texto)

    lector = csv.DictReader(io.StringIO(texto), delimiter=separador)
    lector.fieldnames = [c.strip() for c in (lector.fieldnames or [])]
    cabeceras = lector.fieldnames

    columnas_del_mapeo = [columna for columna in mapeo.values() if columna]
    faltan_en_csv = [columna for columna in columnas_del_mapeo if columna not in cabeceras]
    if faltan_en_csv:
        raise ErrorLectorCSV(
            f"El CSV no tiene estas columnas del mapeo: {', '.join(faltan_en_csv)} "
            f"(columnas disponibles: {', '.join(cabeceras)})"
        )

    pendientes_por_valor = defaultdict(list)      # valor -> [(fecha_date, operacion), ...]
    pendientes_dividendos_por_valor = defaultdict(list)   # valor -> [(fecha_date, dividendo), ...]
    avisos = []

    for num_fila, fila in enumerate(lector, start=2):   # la fila 1 es la cabecera
        tipo_bruto = fila[mapeo["tipo"]]
        categoria = _clasificar_tipo(tipo_bruto, tipos)

        if categoria is None:
            avisos.append(f"Fila {num_fila}: tipo '{tipo_bruto}' no reconocido, se ignora")
            continue
        if categoria == "ignorar":
            avisos.append(f"Fila {num_fila}: '{tipo_bruto}' no es una compraventa ni un dividendo, se ignora")
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

        if cantidad_valor_absoluto and cantidad.startswith("-"):
            cantidad = cantidad[1:]

        valor = fila[mapeo["valor"]].strip()

        if categoria == "dividendo":
            dividendo = {
                "fecha": _formatear_fecha(fecha),
                "bruto": Decimal(cantidad) * Decimal(precio),
                "retencion": Decimal(comision) if comision is not None else Decimal("0"),
            }
            pendientes_dividendos_por_valor[valor].append((fecha, dividendo))
            continue

        divisa = fila[mapeo["divisa"]].strip() if "divisa" in mapeo else ""

        operacion = {
            "fecha": _formatear_fecha(fecha),
            "tipo": categoria,
            "acciones": cantidad,
            "precio_usd": precio,   # nombre historico del campo: precio en la divisa de la operacion
            "comision_eur": comision if comision is not None else "0",
            "divisa": divisa or "EUR",
            "comision_en_divisa_operacion": comision_en_divisa_operacion,
        }
        pendientes_por_valor[valor].append((fecha, operacion))

    operaciones_por_valor = _agrupar_ordenado(pendientes_por_valor)
    dividendos_por_valor = _agrupar_ordenado(pendientes_dividendos_por_valor)

    return operaciones_por_valor, dividendos_por_valor, avisos


def hay_operaciones_en_otra_divisa_con_comision(mapeo, ruta_csv=None, contenido=None,
                                                 encoding="utf-8-sig", separador=None):
    """True si el fichero tiene alguna fila con divisa distinta de EUR y una
    comision distinta de cero. Es solo para avisar en la interfaz de que hay
    que confirmar en que moneda cobra la comision el broker (ver
    comision_en_divisa_operacion en leer_operaciones): no bloquea nada, y
    una fila con un numero invalido en cualquiera de las dos columnas se
    ignora aqui sin mas (leer_operaciones ya la reporta como aviso aparte).
    """
    columna_comision = mapeo.get("comision") if mapeo else None
    if not columna_comision:
        return False

    columna_divisa = mapeo.get("divisa")
    texto = _texto_completo(ruta_csv, contenido, encoding)
    separador = separador or _detectar_separador(texto)

    lector = csv.DictReader(io.StringIO(texto), delimiter=separador)
    lector.fieldnames = [c.strip() for c in (lector.fieldnames or [])]

    for fila in lector:
        divisa = fila.get(columna_divisa, "").strip() if columna_divisa else ""
        if not divisa or divisa == "EUR":
            continue
        try:
            comision = _parsear_numero(fila.get(columna_comision, ""))
        except ErrorLectorCSV:
            continue
        if comision is not None and Decimal(comision) != 0:
            return True

    return False


def _agrupar_ordenado(pendientes_por_valor):
    """[(fecha, dato), ...] por valor -> [dato, ...] por valor, en orden cronologico."""
    agrupado = {}
    for valor, pendientes in pendientes_por_valor.items():
        pendientes.sort(key=lambda par: par[0])
        agrupado[valor] = [dato for _, dato in pendientes]
    return agrupado


def resumir_dividendos(dividendos_por_valor):
    """Totales de dividendos brutos y retencion (rendimiento del capital
    mobiliario), en conjunto y por valor. Devuelve:
      {"bruto_total": Decimal, "retencion_total": Decimal,
       "por_valor": {valor: {"bruto": Decimal, "retencion": Decimal}}}
    """
    por_valor = {}
    bruto_total = Decimal("0")
    retencion_total = Decimal("0")

    for valor, dividendos in dividendos_por_valor.items():
        bruto_valor = sum((d["bruto"] for d in dividendos), Decimal("0"))
        retencion_valor = sum((d["retencion"] for d in dividendos), Decimal("0"))
        por_valor[valor] = {"bruto": bruto_valor, "retencion": retencion_valor}
        bruto_total += bruto_valor
        retencion_total += retencion_valor

    return {"bruto_total": bruto_total, "retencion_total": retencion_total, "por_valor": por_valor}


def guardar_preset(nombre, mapeo, tipos=None, comision_en_divisa_operacion=False,
                    cantidad_valor_absoluto=False, ruta_presets=RUTA_PRESETS_POR_DEFECTO):
    presets = _cargar_todos_los_presets(ruta_presets)
    presets[nombre] = {
        "mapeo": mapeo,
        "tipos": tipos or TIPOS_POR_DEFECTO,
        "comision_en_divisa_operacion": bool(comision_en_divisa_operacion),
        "cantidad_valor_absoluto": bool(cantidad_valor_absoluto),
    }
    os.makedirs(os.path.dirname(ruta_presets) or ".", exist_ok=True)
    with open(ruta_presets, "w", encoding="utf-8") as f:
        json.dump(presets, f, ensure_ascii=False, indent=2, sort_keys=True)


def cargar_preset(nombre, ruta_presets=RUTA_PRESETS_POR_DEFECTO):
    presets = _cargar_todos_los_presets(ruta_presets)
    if nombre not in presets:
        disponibles = ", ".join(sorted(presets)) or "(ninguno)"
        raise ErrorLectorCSV(f"No existe el preset '{nombre}'. Presets disponibles: {disponibles}")
    preset = presets[nombre]
    return (
        preset["mapeo"],
        preset.get("tipos", TIPOS_POR_DEFECTO),
        preset.get("comision_en_divisa_operacion", False),
        preset.get("cantidad_valor_absoluto", False),
    )


def listar_presets(ruta_presets=RUTA_PRESETS_POR_DEFECTO):
    return sorted(_cargar_todos_los_presets(ruta_presets).keys())


def _cargar_todos_los_presets(ruta_presets):
    if not os.path.exists(ruta_presets):
        return {}
    with open(ruta_presets, encoding="utf-8") as f:
        return json.load(f)
