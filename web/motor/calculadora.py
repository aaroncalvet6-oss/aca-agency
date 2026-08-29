# Calculadora FIFO - version 1
# Objetivo: que imprima 960.80 (el mismo numero que tu Excel)

import calendar
from collections import defaultdict
from datetime import date
from decimal import ROUND_HALF_UP, Decimal

import tipos_cambio

MESES_REGLA_DOS_MESES = 2
ANIO_BASE = 2001  # las fechas son "DD/MM" sin anio; usamos uno cualquiera no bisiesto
CENTIMO = Decimal("0.01")


def _decimal(valor):
    """Convierte a Decimal de forma segura. Nunca Decimal(float) directo:
    eso arrastraria el error binario del float (Decimal(0.1) no es 0.1).
    Pasando por str se evita, porque str(float) da la representacion
    decimal mas corta que redondea al mismo float."""
    if isinstance(valor, Decimal):
        return valor
    return Decimal(str(valor))


operaciones = [
    {"fecha": "15/01", "tipo": "compra", "acciones": 10, "precio_usd": 300, "comision_eur": 1, "cambio": 1.09},
    {"fecha": "20/03", "tipo": "compra", "acciones": 5,  "precio_usd": 350, "comision_eur": 1, "cambio": 1.08},
    {"fecha": "10/09", "tipo": "venta",  "acciones": 12, "precio_usd": 400, "comision_eur": 1, "cambio": 1.10},
    {"fecha": "20/12", "tipo": "venta",  "acciones": 3,  "precio_usd": 500, "comision_eur": 1, "cambio": 1.05},
]


def _fecha_completa(fecha_str):
    """Convierte "DD/MM/AAAA" en un date real (con anio). Hace falta el
    anio para poder consultar el BCE; con solo "DD/MM" no hay forma de
    saber a que fecha real se refiere la operacion."""
    partes = fecha_str.split("/")
    if len(partes) != 3:
        raise ValueError(
            f"La operacion del {fecha_str} no trae 'cambio' y su fecha no incluye el anio "
            f"(usa \"DD/MM/AAAA\") para poder consultar el tipo del BCE"
        )
    dia, mes, anio = (int(parte) for parte in partes)
    return date(anio, mes, dia)


def a_euros(op):
    """Pasa una operacion a euros con SU tipo de cambio y le aplica la comision.

    Si la operacion ya esta en EUR (op["divisa"] == "EUR"), NO se convierte:
    se usa el importe tal cual, sin pasar por el BCE. Es el caso mas
    comun en Trade Republic (ETFs/acciones que cotizan en EUR), no una
    excepcion.

    Si no esta en EUR y no trae "cambio", se busca el tipo oficial del BCE
    para su fecha y divisa (op.get("divisa", "USD")).

    La comision (op["comision_eur"]) se asume en EUR por defecto -- asi lo
    cobran la mayoria de brokers (Trade Republic, por ejemplo) aunque la
    operacion sea en otra divisa. Si op["comision_en_divisa_operacion"] es
    verdadero, la comision se trata como si viniera en la MISMA divisa que
    el importe principal y se convierte con el mismo tipo y la misma fecha
    (nunca con uno distinto): asi op["comision_eur"] deja de ser un nombre
    exacto en ese caso, pero el campo se mantiene por compatibilidad.
    """
    acciones = _decimal(op["acciones"])
    precio = _decimal(op["precio_usd"])
    comision = _decimal(op["comision_eur"])
    divisa = op.get("divisa", "USD")

    importe = acciones * precio

    if divisa == "EUR":
        importe_eur = importe
    else:
        cambio = op.get("cambio")
        if cambio is None:
            cambio = tipos_cambio.obtener_tipo_cambio(_fecha_completa(op["fecha"]), divisa)
        cambio = _decimal(cambio)
        importe_eur = importe / cambio
        if op.get("comision_en_divisa_operacion"):
            comision = (comision / cambio).quantize(CENTIMO, rounding=ROUND_HALF_UP)

    # Redondeamos a centimos porque esto es dinero real que se movio de verdad.
    importe_eur = importe_eur.quantize(CENTIMO, rounding=ROUND_HALF_UP)

    if op["tipo"] == "compra":
        return importe_eur + comision   # comprar te cuesta mas
    else:
        return importe_eur - comision   # vender te deja menos


def _a_fecha(fecha_str):
    # Aceptamos tanto "DD/MM" como "DD/MM/AAAA": aqui el anio no importa,
    # solo se usa para medir distancia en meses dentro del mismo calendario.
    dia, mes = (int(parte) for parte in fecha_str.split("/")[:2])
    return date(ANIO_BASE, mes, dia)


def _fecha_para_ordenar(fecha_str):
    """Fecha real (con anio si lo trae) solo para ordenar cronologicamente.
    A diferencia de _a_fecha, SI usa el anio cuando esta disponible: un
    FIFO real puede tener lotes de hace varios anios, así que ignorar el
    anio (como hace _a_fecha para la ventana de los 2 meses) daria un
    orden erroneo entre anios distintos."""
    partes = fecha_str.split("/")
    if len(partes) == 3:
        dia, mes, anio = (int(parte) for parte in partes)
        return date(anio, mes, dia)
    dia, mes = (int(parte) for parte in partes[:2])
    return date(ANIO_BASE, mes, dia)


def _sumar_meses(fecha, meses):
    mes_indice = fecha.month - 1 + meses
    anio = fecha.year + mes_indice // 12
    mes = mes_indice % 12 + 1
    dia = min(fecha.day, calendar.monthrange(anio, mes)[1])
    return date(anio, mes, dia)


def _buscar_recompras_en_ventana(operaciones, idx_venta, origenes_de_esta_venta):
    """Indices de las compras del mismo valor en los 2 meses antes/despues
    de la venta, EXCLUYENDO las que han alimentado por FIFO esta misma
    venta (origenes_de_esta_venta): esas acciones se estan liquidando en
    esta transmision, no representan continuidad de la posicion, asi que
    no cuentan como "recompra" a efectos de esta venta. Si ademas se
    compraron de verdad mas acciones en la ventana, esas si cuentan.
    """
    venta = operaciones[idx_venta]
    fecha_venta = _a_fecha(venta["fecha"])
    inicio = _sumar_meses(fecha_venta, -MESES_REGLA_DOS_MESES)
    fin = _sumar_meses(fecha_venta, MESES_REGLA_DOS_MESES)

    return [
        idx for idx, op in enumerate(operaciones)
        if op["tipo"] == "compra"
        and idx not in origenes_de_esta_venta
        and inicio <= _a_fecha(op["fecha"]) <= fin
    ]


def _fifo_consumir(lotes, cantidad, fecha_venta, imprimir_traza):
    coste = Decimal("0")
    por_vender = _decimal(cantidad)
    origenes_consumidos = set()   # indices de las compras de las que ha salido esta venta

    # FIFO: vamos comiendo los lotes mas antiguos primero
    while por_vender > 0:
        if not lotes:
            raise ValueError(f"Venta del {fecha_venta}: vendes mas acciones de las que tienes")

        lote = lotes[0]
        cogidas = min(lote["acciones"], por_vender)

        coste += cogidas * lote["coste_accion"]
        lote["acciones"] -= cogidas
        por_vender -= cogidas
        origenes_consumidos.add(lote["origen_idx"])

        if imprimir_traza:
            print(f"  venta {fecha_venta}: coge {cogidas} acciones del lote del {lote['fecha']}")

        if lote["acciones"] == 0:
            lotes.pop(0)   # lote agotado, fuera

    return coste, origenes_consumidos


def _simular(operaciones, perdida_bloqueada_por_venta, coste_extra_por_compra, imprimir_traza):
    lotes = []       # cada lote: acciones que LE QUEDAN, su coste y de que operacion viene (origen_idx)
    ganancia = Decimal("0")
    resultados_venta = {}   # indice de la operacion -> resultado (ganancia o perdida) de esa venta
    origenes_por_venta = {}   # indice de la venta -> {indices de las compras que la alimentaron por FIFO}
    detalle_ventas = []     # una fila por venta, en orden, para el desglose operacion a operacion

    for idx, op in enumerate(operaciones):
        total_eur = a_euros(op)

        if op["tipo"] == "compra":
            total_eur += coste_extra_por_compra.get(idx, Decimal("0"))
            acciones = _decimal(op["acciones"])
            lotes.append({
                "fecha": op["fecha"],
                "acciones": acciones,
                # el coste por accion NO se redondea: no es dinero, es un calculo intermedio
                "coste_accion": total_eur / acciones,
                "origen_idx": idx,
            })

        else:
            coste_vendido, origenes = _fifo_consumir(lotes, op["acciones"], op["fecha"], imprimir_traza)
            resultado_venta = total_eur - coste_vendido
            resultados_venta[idx] = resultado_venta
            origenes_por_venta[idx] = origenes

            bloqueado = perdida_bloqueada_por_venta.get(idx, Decimal("0"))
            resultado_declarado = resultado_venta + bloqueado
            ganancia += resultado_declarado

            detalle_ventas.append({
                "fecha": op["fecha"],
                "resultado_bruto": resultado_venta,
                "bloqueado": bloqueado,
                "resultado_declarado": resultado_declarado,
            })

            if bloqueado and imprimir_traza:
                print(f"  venta {op['fecha']}: {bloqueado:.2f} de la perdida no se declara "
                      f"(regla de los 2 meses, art. 33.5.f LIRPF); queda una perdida declarable "
                      f"de {resultado_declarado:.2f}")

    return ganancia, resultados_venta, lotes, detalle_ventas, origenes_por_venta


def _aplicar_regla_dos_meses(operaciones, resultados_venta, origenes_por_venta):
    """A partir de los resultados brutos (pasada 1), calcula que parte de
    cada perdida se bloquea (art. 33.5.f LIRPF) y a que lotes recomprados
    se les suma. Ver calcular_detalle() para el porque de cada paso."""
    perdida_bloqueada_por_venta = {}
    coste_extra_por_compra = defaultdict(lambda: Decimal("0"))

    for idx, resultado in resultados_venta.items():
        if resultado >= 0:
            continue

        idxs_recompra = _buscar_recompras_en_ventana(operaciones, idx, origenes_por_venta[idx])
        if not idxs_recompra:
            continue

        acciones_vendidas = _decimal(operaciones[idx]["acciones"])
        acciones_recompradas = sum((_decimal(operaciones[i]["acciones"]) for i in idxs_recompra), Decimal("0"))
        proporcion = min(acciones_recompradas, acciones_vendidas) / acciones_vendidas

        bloqueado = -resultado * proporcion
        perdida_bloqueada_por_venta[idx] = bloqueado

        for i in idxs_recompra:
            parte = bloqueado * (_decimal(operaciones[i]["acciones"]) / acciones_recompradas)
            coste_extra_por_compra[i] += parte

    return perdida_bloqueada_por_venta, coste_extra_por_compra


def calcular_desglose(operaciones):
    """Como calcular_detalle, pero devuelve tambien el detalle operacion a
    operacion (una fila por venta) que necesita la web para mostrar el
    desglose. No se ha tocado calcular_detalle para no romper su
    contrato ya usado en otros sitios.

    Devuelve (ganancia, lotes_finales, detalle_ventas), con
    detalle_ventas = [{"fecha", "resultado_bruto", "bloqueado",
    "resultado_declarado"}, ...] en el mismo orden CRONOLOGICO (no
    necesariamente el orden de entrada, ver mas abajo).
    """
    # El FIFO tiene que consumir por fecha, no por el orden en que vienen
    # las operaciones: un fichero desordenado no debe dar un resultado
    # incorrecto en silencio. lector_csv.py ya entrega todo ordenado, pero
    # esto es defensa en profundidad para cualquiera que llame a esta
    # funcion directamente con una lista sin ordenar. sorted() es estable:
    # si dos operaciones tienen la misma fecha, se respeta el orden en que
    # vinieron (p.ej. una compra y una venta el mismo dia).
    operaciones = sorted(operaciones, key=lambda op: _fecha_para_ordenar(op["fecha"]))

    # Pasada 1: FIFO normal, sin aplicar todavia la regla de los 2 meses,
    # solo para saber que ventas dan perdida y de cuanto, y de que compras
    # ha salido cada venta (para no contarlas luego como "recompra" de si
    # mismas: esas acciones se estan liquidando, no son continuidad).
    _, resultados_venta, _, _, origenes_por_venta = _simular(
        operaciones, perdida_bloqueada_por_venta={}, coste_extra_por_compra={}, imprimir_traza=False)

    # Para cada perdida, se suman las acciones de TODAS las compras del
    # mismo valor en la ventana de los 2 meses (antes o despues), salvo las
    # que hayan alimentado esta misma venta. Se bloquea la parte de la
    # perdida proporcional a esas acciones recompradas en conjunto (el
    # resto se declara con normalidad), y ese importe se reparte entre los
    # lotes recomprados en proporcion a sus propias acciones. Nunca se
    # aplica a ganancias.
    perdida_bloqueada_por_venta, coste_extra_por_compra = _aplicar_regla_dos_meses(
        operaciones, resultados_venta, origenes_por_venta)

    # Pasada 2: FIFO definitivo, ya con la parte bloqueada de cada perdida
    # neutralizada y sumada al coste del lote recomprado correspondiente.
    ganancia, _, lotes_finales, detalle_ventas, _ = _simular(operaciones, perdida_bloqueada_por_venta,
                                                              coste_extra_por_compra, imprimir_traza=True)

    return ganancia, lotes_finales, detalle_ventas


def calcular_detalle(operaciones):
    """Igual que calcular_ganancia, pero devuelve tambien los lotes que
    quedan sin vender al final (con su coste ya ajustado por la regla de
    los 2 meses si aplica). Util para verificar en tests el coste de un
    lote recomprado, ademas de la ganancia declarable."""
    ganancia, lotes_finales, _ = calcular_desglose(operaciones)
    return ganancia, lotes_finales


def calcular_ganancia(operaciones):
    ganancia, _ = calcular_detalle(operaciones)
    return ganancia


if __name__ == "__main__":
    resultado = calcular_ganancia(operaciones)
    print(f"\nGanancia: {resultado:.2f} EUR")
