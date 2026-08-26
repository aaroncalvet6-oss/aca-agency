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

from calculadora import calcular_detalle, calcular_ganancia

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
    {
        "nombre": "regla_dos_meses_perdida_bloqueada",
        "operaciones": [
            {"fecha": "10/01", "tipo": "compra", "acciones": 10, "precio_usd": 100, "comision_eur": 1, "cambio": 1.00},
            {"fecha": "15/03", "tipo": "venta",  "acciones": 10, "precio_usd": 70,  "comision_eur": 1, "cambio": 1.00},
            {"fecha": "05/04", "tipo": "compra", "acciones": 10, "precio_usd": 75,  "comision_eur": 1, "cambio": 1.00},
        ],
        "esperado": "0.00",
    },
    {
        "nombre": "regla_dos_meses_recompra_parcial",
        "operaciones": [
            {"fecha": "10/01", "tipo": "compra", "acciones": 10, "precio_usd": 100, "comision_eur": 1, "cambio": 1.00},
            {"fecha": "15/03", "tipo": "venta",  "acciones": 10, "precio_usd": 70,  "comision_eur": 1, "cambio": 1.00},
            {"fecha": "05/04", "tipo": "compra", "acciones": 4,  "precio_usd": 75,  "comision_eur": 1, "cambio": 1.00},
        ],
        "esperado": "-181.20",
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


class TestReglaDosMeses(unittest.TestCase):
    def test_lote_recomprado_incorpora_la_perdida_bloqueada(self):
        operaciones = [
            {"fecha": "10/01", "tipo": "compra", "acciones": 10, "precio_usd": 100, "comision_eur": 1, "cambio": 1.00},
            {"fecha": "15/03", "tipo": "venta",  "acciones": 10, "precio_usd": 70,  "comision_eur": 1, "cambio": 1.00},
            {"fecha": "05/04", "tipo": "compra", "acciones": 10, "precio_usd": 75,  "comision_eur": 1, "cambio": 1.00},
        ]

        with contextlib.redirect_stdout(io.StringIO()):
            ganancia, lotes_finales = calcular_detalle(operaciones)

        self.assertEqual(f"{ganancia:.2f}", "0.00")
        self.assertEqual(len(lotes_finales), 1)

        lote = lotes_finales[0]
        self.assertEqual(lote["fecha"], "05/04")
        self.assertAlmostEqual(lote["coste_accion"] * lote["acciones"], 1053.00, places=2)

    def test_recompra_parcial_bloquea_solo_la_proporcion_recomprada(self):
        operaciones = [
            {"fecha": "10/01", "tipo": "compra", "acciones": 10, "precio_usd": 100, "comision_eur": 1, "cambio": 1.00},
            {"fecha": "15/03", "tipo": "venta",  "acciones": 10, "precio_usd": 70,  "comision_eur": 1, "cambio": 1.00},
            {"fecha": "05/04", "tipo": "compra", "acciones": 4,  "precio_usd": 75,  "comision_eur": 1, "cambio": 1.00},
        ]

        with contextlib.redirect_stdout(io.StringIO()):
            ganancia, lotes_finales = calcular_detalle(operaciones)

        self.assertEqual(f"{ganancia:.2f}", "-181.20")
        self.assertEqual(len(lotes_finales), 1)

        lote = lotes_finales[0]
        self.assertEqual(lote["fecha"], "05/04")
        self.assertAlmostEqual(lote["coste_accion"] * lote["acciones"], 421.80, places=2)


if __name__ == "__main__":
    unittest.main()
