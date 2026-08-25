"""Tests de la calculadora FIFO.

Para añadir un caso nuevo: añade una entrada a CASOS con sus
operaciones y el resultado esperado (string con 2 decimales, tal
como lo imprime calcular_ganancia). No hace falta tocar nada mas.

Ejecutar todos los casos:
    python3 -m unittest test_calculadora.py -v
"""

import contextlib
import io
import unittest

from calculadora import calcular_ganancia

CASOS = [
    {
        "nombre": "venta_unica_10_09",
        "operaciones": [
            {"fecha": "15/01", "tipo": "compra", "acciones": 10, "precio_usd": 300, "comision_eur": 1, "cambio": 1.09},
            {"fecha": "20/03", "tipo": "compra", "acciones": 5,  "precio_usd": 350, "comision_eur": 1, "cambio": 1.08},
            {"fecha": "10/09", "tipo": "venta",  "acciones": 12, "precio_usd": 400, "comision_eur": 1, "cambio": 1.10},
        ],
        "esperado": "960.80",
    },
    {
        "nombre": "dos_ventas_10_09_y_20_12",
        "operaciones": [
            {"fecha": "15/01", "tipo": "compra", "acciones": 10, "precio_usd": 300, "comision_eur": 1, "cambio": 1.09},
            {"fecha": "20/03", "tipo": "compra", "acciones": 5,  "precio_usd": 350, "comision_eur": 1, "cambio": 1.08},
            {"fecha": "10/09", "tipo": "venta",  "acciones": 12, "precio_usd": 400, "comision_eur": 1, "cambio": 1.10},
            {"fecha": "20/12", "tipo": "venta",  "acciones": 3,  "precio_usd": 500, "comision_eur": 1, "cambio": 1.05},
        ],
        "esperado": "1415.55",
    },
    # Añade aqui nuevos casos:
    # {
    #     "nombre": "nombre_descriptivo_del_caso",
    #     "operaciones": [...],
    #     "esperado": "X.XX",
    # },
]


def _crear_test(caso):
    def test(self):
        with contextlib.redirect_stdout(io.StringIO()):
            ganancia = calcular_ganancia(caso["operaciones"])
        self.assertEqual(f"{ganancia:.2f}", caso["esperado"])

    return test


class TestCalculadoraFIFO(unittest.TestCase):
    pass


for _caso in CASOS:
    setattr(TestCalculadoraFIFO, f"test_{_caso['nombre']}", _crear_test(_caso))


if __name__ == "__main__":
    unittest.main()
