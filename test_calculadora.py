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
from datetime import date
from decimal import Decimal
from unittest.mock import patch

from calculadora import a_euros, calcular_detalle, calcular_ganancia

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
    {
        "nombre": "regla_dos_meses_varias_recompras",
        "operaciones": [
            {"fecha": "10/01", "tipo": "compra", "acciones": 10, "precio_usd": 100, "comision_eur": 1, "cambio": 1.00},
            {"fecha": "15/03", "tipo": "venta",  "acciones": 10, "precio_usd": 70,  "comision_eur": 1, "cambio": 1.00},
            {"fecha": "05/04", "tipo": "compra", "acciones": 4,  "precio_usd": 75,  "comision_eur": 1, "cambio": 1.00},
            {"fecha": "20/04", "tipo": "compra", "acciones": 3,  "precio_usd": 80,  "comision_eur": 1, "cambio": 1.00},
        ],
        "esperado": "-90.60",
    },
    {
        "nombre": "eur_sin_conversion_con_lotes_fraccionarios",
        "operaciones": [
            {"fecha": "10/01/2024", "tipo": "compra", "acciones": 2.5, "precio_usd": 100, "comision_eur": 1, "divisa": "EUR"},
            {"fecha": "20/03/2024", "tipo": "compra", "acciones": 1.5, "precio_usd": 120, "comision_eur": 1, "divisa": "EUR"},
            {"fecha": "10/09/2024", "tipo": "venta",  "acciones": 3,   "precio_usd": 150, "comision_eur": 1, "divisa": "EUR"},
        ],
        "esperado": "137.67",
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
        self.assertAlmostEqual(float(lote["coste_accion"] * lote["acciones"]), 1053.00, places=2)

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
        self.assertAlmostEqual(float(lote["coste_accion"] * lote["acciones"]), 421.80, places=2)

    def test_varias_recompras_reparten_el_bloqueo_por_acciones(self):
        operaciones = [
            {"fecha": "10/01", "tipo": "compra", "acciones": 10, "precio_usd": 100, "comision_eur": 1, "cambio": 1.00},
            {"fecha": "15/03", "tipo": "venta",  "acciones": 10, "precio_usd": 70,  "comision_eur": 1, "cambio": 1.00},
            {"fecha": "05/04", "tipo": "compra", "acciones": 4,  "precio_usd": 75,  "comision_eur": 1, "cambio": 1.00},
            {"fecha": "20/04", "tipo": "compra", "acciones": 3,  "precio_usd": 80,  "comision_eur": 1, "cambio": 1.00},
        ]

        with contextlib.redirect_stdout(io.StringIO()):
            ganancia, lotes_finales = calcular_detalle(operaciones)

        self.assertEqual(f"{ganancia:.2f}", "-90.60")
        self.assertEqual(len(lotes_finales), 2)

        lote_05_04, lote_20_04 = lotes_finales
        self.assertEqual(lote_05_04["fecha"], "05/04")
        self.assertAlmostEqual(float(lote_05_04["coste_accion"] * lote_05_04["acciones"]), 421.80, places=2)

        self.assertEqual(lote_20_04["fecha"], "20/04")
        self.assertAlmostEqual(float(lote_20_04["coste_accion"] * lote_20_04["acciones"]), 331.60, places=2)


class TestCambioManualOBCE(unittest.TestCase):
    def test_usa_el_cambio_manual_si_se_da_sin_consultar_el_bce(self):
        op = {"fecha": "15/01", "tipo": "compra", "acciones": 10, "precio_usd": 300, "comision_eur": 1, "cambio": 1.09}

        with patch("calculadora.tipos_cambio.obtener_tipo_cambio") as mock_bce:
            total = a_euros(op)

        mock_bce.assert_not_called()
        self.assertAlmostEqual(float(total), round(3000 / 1.09, 2) + 1)

    def test_busca_en_el_bce_si_falta_el_cambio(self):
        op = {"fecha": "15/01/2024", "tipo": "compra", "acciones": 10, "precio_usd": 300, "comision_eur": 1}

        with patch("calculadora.tipos_cambio.obtener_tipo_cambio", return_value=1.09) as mock_bce:
            total = a_euros(op)

        mock_bce.assert_called_once_with(date(2024, 1, 15), "USD")
        self.assertAlmostEqual(float(total), round(3000 / 1.09, 2) + 1)

    def test_sin_cambio_y_sin_anio_en_la_fecha_da_error_claro(self):
        op = {"fecha": "15/01", "tipo": "compra", "acciones": 10, "precio_usd": 300, "comision_eur": 1}

        with self.assertRaises(ValueError):
            a_euros(op)


class TestEuroYPrecisionDecimal(unittest.TestCase):
    def test_eur_no_convierte_ni_llama_al_bce(self):
        op = {"fecha": "10/01/2024", "tipo": "compra", "acciones": 2, "precio_usd": 50, "comision_eur": 1, "divisa": "EUR"}

        with patch("calculadora.tipos_cambio.obtener_tipo_cambio") as mock_bce:
            total = a_euros(op)

        mock_bce.assert_not_called()
        self.assertEqual(str(total), "101.00")

    def test_acciones_fraccionarias_no_arrastran_ruido_de_coma_flotante(self):
        # 0.3 - 0.1 en float puro da 0.19999999999999998; con Decimal debe
        # quedar exactamente 0.2. Este es el patron real de los planes de
        # inversion mensuales de Trade Republic (compran fracciones).
        operaciones = [
            {"fecha": "10/01/2024", "tipo": "compra", "acciones": 0.3, "precio_usd": 100, "comision_eur": 0, "divisa": "EUR"},
            {"fecha": "15/03/2024", "tipo": "venta",  "acciones": 0.1, "precio_usd": 100, "comision_eur": 0, "divisa": "EUR"},
        ]

        with contextlib.redirect_stdout(io.StringIO()):
            _, lotes_finales = calcular_detalle(operaciones)

        self.assertEqual(str(lotes_finales[0]["acciones"]), "0.2")

    def test_eur_sin_conversion_con_lotes_fraccionarios(self):
        operaciones = [
            {"fecha": "10/01/2024", "tipo": "compra", "acciones": 2.5, "precio_usd": 100, "comision_eur": 1, "divisa": "EUR"},
            {"fecha": "20/03/2024", "tipo": "compra", "acciones": 1.5, "precio_usd": 120, "comision_eur": 1, "divisa": "EUR"},
            {"fecha": "10/09/2024", "tipo": "venta",  "acciones": 3,   "precio_usd": 150, "comision_eur": 1, "divisa": "EUR"},
        ]

        with contextlib.redirect_stdout(io.StringIO()):
            ganancia, lotes_finales = calcular_detalle(operaciones)

        self.assertEqual(f"{ganancia:.2f}", "137.67")
        self.assertEqual(len(lotes_finales), 1)
        self.assertEqual(lotes_finales[0]["acciones"], Decimal("1.0"))


if __name__ == "__main__":
    unittest.main()
