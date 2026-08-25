# Calculadora FIFO - version 1
# Objetivo: que imprima 960.80 (el mismo numero que tu Excel)

operaciones = [
    {"fecha": "15/01", "tipo": "compra", "acciones": 10, "precio_usd": 300, "comision_eur": 1, "cambio": 1.09},
    {"fecha": "20/03", "tipo": "compra", "acciones": 5,  "precio_usd": 350, "comision_eur": 1, "cambio": 1.08},
    {"fecha": "10/09", "tipo": "venta",  "acciones": 12, "precio_usd": 400, "comision_eur": 1, "cambio": 1.10},
    {"fecha": "20/12", "tipo": "venta",  "acciones": 3,  "precio_usd": 500, "comision_eur": 1, "cambio": 1.05},
]


def a_euros(op):
    """Pasa una operacion a euros con SU tipo de cambio y le aplica la comision."""
    importe_usd = op["acciones"] * op["precio_usd"]

    # Redondeamos a centimos porque esto es dinero real que se movio de verdad.
    importe_eur = round(importe_usd / op["cambio"], 2)

    if op["tipo"] == "compra":
        return importe_eur + op["comision_eur"]   # comprar te cuesta mas
    else:
        return importe_eur - op["comision_eur"]   # vender te deja menos


def calcular_ganancia(operaciones):
    lotes = []       # cada lote: acciones que LE QUEDAN y lo que costo cada accion
    ganancia = 0

    for op in operaciones:
        total_eur = a_euros(op)

        if op["tipo"] == "compra":
            lotes.append({
                "fecha": op["fecha"],
                "acciones": op["acciones"],
                # el coste por accion NO se redondea: no es dinero, es un calculo intermedio
                "coste_accion": total_eur / op["acciones"],
            })

        else:
            por_vender = op["acciones"]
            coste_vendido = 0

            # FIFO: vamos comiendo los lotes mas antiguos primero
            while por_vender > 0:
                if not lotes:
                    raise ValueError(f"Venta del {op['fecha']}: vendes mas acciones de las que tienes")

                lote = lotes[0]
                cogidas = min(lote["acciones"], por_vender)

                coste_vendido += cogidas * lote["coste_accion"]
                lote["acciones"] -= cogidas
                por_vender -= cogidas

                print(f"  venta {op['fecha']}: coge {cogidas} acciones del lote del {lote['fecha']}")

                if lote["acciones"] == 0:
                    lotes.pop(0)   # lote agotado, fuera

            ganancia += total_eur - coste_vendido

    return ganancia


resultado = calcular_ganancia(operaciones)
print(f"\nGanancia: {resultado:.2f} EUR")
