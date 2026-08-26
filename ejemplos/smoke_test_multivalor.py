"""Prueba de humo con datos parecidos a un extracto real de Trade Republic:
varios valores mezclados, varias divisas, acciones fraccionarias, comision
cero (planes automaticos) y fechas que fuerzan el fallback del BCE.

Los datos son INVENTADOS (ver extracto_trade_republic_sintetico.csv), solo
la ESTRUCTURA imita lo que exporta un broker real. El objetivo es ver si
el motor (calcular_detalle) aguanta esta forma de los datos, no verificar
un resultado concreto.

No usa red: los tipos de cambio se leen de eurofxref-fixture-smoke.xml
(mismo formato que el BCE, pero con solo los dias que hacen falta aqui).

Ejecutar: python3 ejemplos/smoke_test_multivalor.py
"""

import os
import sys
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import tipos_cambio
from calculadora import calcular_detalle

RUTA_FIXTURE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "eurofxref-fixture-smoke.xml")

_obtener_real = tipos_cambio.obtener_tipo_cambio
tipos_cambio.obtener_tipo_cambio = lambda fecha, divisa: _obtener_real(fecha, divisa, ruta_cache=RUTA_FIXTURE, url=None)


# El motor actual hace FIFO sobre una unica lista de operaciones: NO
# distingue valores. Con un extracto real, hay que separarlo por
# ISIN/valor tu mismo antes de llamar a calcular_detalle. Aqui ya viene
# separado a mano para poder probar cada valor por separado.
OPERACIONES_POR_VALOR = {
    "AAPL (regla de los 2 meses, recompra parcial con plan automatico)": [
        {"fecha": "10/01/2024", "tipo": "compra", "acciones": 5.5, "precio_usd": 185.20, "comision_eur": 1.00},
        {"fecha": "15/03/2024", "tipo": "venta",  "acciones": 2,   "precio_usd": 170.00, "comision_eur": 1.00},
        {"fecha": "19/04/2024", "tipo": "compra", "acciones": 1.2, "precio_usd": 165.00, "comision_eur": 0.00},
    ],
    "VWCE (plan de inversion mensual, muchos lotes fraccionarios)": [
        {"fecha": "10/01/2024", "tipo": "compra", "acciones": 0.0823, "precio_usd": 105.40, "comision_eur": 0.00, "divisa": "EUR", "cambio": 1.0},
        {"fecha": "09/02/2024", "tipo": "compra", "acciones": 0.0810, "precio_usd": 106.90, "comision_eur": 0.00, "divisa": "EUR", "cambio": 1.0},
        {"fecha": "08/03/2024", "tipo": "compra", "acciones": 0.0790, "precio_usd": 109.20, "comision_eur": 0.00, "divisa": "EUR", "cambio": 1.0},
        {"fecha": "08/04/2024", "tipo": "compra", "acciones": 0.0805, "precio_usd": 108.10, "comision_eur": 0.00, "divisa": "EUR", "cambio": 1.0},
        {"fecha": "08/05/2024", "tipo": "compra", "acciones": 0.0770, "precio_usd": 111.30, "comision_eur": 0.00, "divisa": "EUR", "cambio": 1.0},
        {"fecha": "10/06/2024", "tipo": "compra", "acciones": 0.0755, "precio_usd": 113.50, "comision_eur": 0.00, "divisa": "EUR", "cambio": 1.0},
        {"fecha": "15/07/2024", "tipo": "venta",  "acciones": 0.35,   "precio_usd": 112.00, "comision_eur": 1.00, "divisa": "EUR", "cambio": 1.0},
    ],
    "IWDA (BUG: EUR nativo sin 'cambio' dado -> intenta preguntar al BCE)": [
        {"fecha": "05/02/2024", "tipo": "compra", "acciones": 3.333, "precio_usd": 82.15, "comision_eur": 1.00, "cambio": 1.0},
        {"fecha": "19/08/2024", "tipo": "compra", "acciones": 2.0,   "precio_usd": 84.00, "comision_eur": 1.00, "divisa": "EUR"},  # sin "cambio" a proposito
    ],
    "BARC (libras, cierre de ano)": [
        {"fecha": "12/06/2024", "tipo": "compra", "acciones": 40, "precio_usd": 2.15, "comision_eur": 1.00, "divisa": "GBP"},
        {"fecha": "30/12/2024", "tipo": "venta",  "acciones": 15, "precio_usd": 1.95, "comision_eur": 1.00, "divisa": "GBP"},
    ],
}


def main():
    for nombre, operaciones in OPERACIONES_POR_VALOR.items():
        print(f"\n=== {nombre} ===")
        try:
            ganancia, lotes_finales = calcular_detalle(operaciones)
            print(f"Ganancia/perdida declarable: {ganancia:.2f} EUR")
            print(f"Lotes que quedan: {lotes_finales}")
        except Exception as error:
            print(f"FALLO: {type(error).__name__}: {error}")

    # Caso aparte: una fecha de LIQUIDACION (T+2) cayendo en domingo, en vez
    # de la fecha de ejecucion (viernes). Pasa si tu futuro parser de TR usa
    # la columna equivocada del extracto. El fallback del BCE debe aguantarlo.
    print("\n=== MSFT: fecha de liquidacion en domingo (18/08/2024, T+2 de un viernes) ===")
    try:
        tipo = tipos_cambio.obtener_tipo_cambio(date(2024, 8, 18), "USD")
        print(f"Tipo USD resuelto para el domingo 18/08/2024: {tipo} (deberia ser el del viernes 16/08)")
    except Exception as error:
        print(f"FALLO: {type(error).__name__}: {error}")


if __name__ == "__main__":
    main()
