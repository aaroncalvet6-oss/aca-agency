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
from decimal import ROUND_HALF_UP, Decimal
from unittest.mock import patch

from calculadora import a_euros, calcular_desglose, calcular_detalle, calcular_ganancia

CENTIMO = Decimal("0.01")


def _como_dinero(valor):
    """Redondea un Decimal a centimos, igual que se le mostraria al usuario."""
    return valor.quantize(CENTIMO, rounding=ROUND_HALF_UP)


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
    {
        # Bug real encontrado en producción: la compra que alimenta por FIFO
        # la propia venta (15/05) cae dentro de la ventana de 2 meses de esa
        # misma venta (10/07 ± 2 meses = [10/05, 10/09]). Antes del fix,
        # _buscar_recompras_en_ventana la contaba como "recompra" de sí
        # misma, inflando el bloqueo al 100% en vez del 60% real (solo la
        # compra del 20/07 es una recompra de verdad). Ver TestAutoRecompra
        # para las aserciones detalladas (bruto/bloqueado/lote).
        "nombre": "no_cuenta_como_recompra_la_compra_que_alimenta_la_propia_venta",
        "operaciones": [
            {"fecha": "15/05/2024", "tipo": "compra", "acciones": 10, "precio_usd": 50.00, "comision_eur": 1.00, "divisa": "EUR"},
            {"fecha": "10/07/2024", "tipo": "venta",  "acciones": 10, "precio_usd": 40.00, "comision_eur": 1.00, "divisa": "EUR"},
            {"fecha": "20/07/2024", "tipo": "compra", "acciones": 6,  "precio_usd": 42.00, "comision_eur": 1.00, "divisa": "EUR"},
        ],
        "esperado": "-40.80",
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
        self.assertEqual(_como_dinero(lote["coste_accion"] * lote["acciones"]), Decimal("1053.00"))

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
        self.assertEqual(_como_dinero(lote["coste_accion"] * lote["acciones"]), Decimal("421.80"))

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
        self.assertEqual(_como_dinero(lote_05_04["coste_accion"] * lote_05_04["acciones"]), Decimal("421.80"))

        self.assertEqual(lote_20_04["fecha"], "20/04")
        self.assertEqual(_como_dinero(lote_20_04["coste_accion"] * lote_20_04["acciones"]), Decimal("331.60"))


class TestCambioManualOBCE(unittest.TestCase):
    def test_usa_el_cambio_manual_si_se_da_sin_consultar_el_bce(self):
        op = {"fecha": "15/01", "tipo": "compra", "acciones": 10, "precio_usd": 300, "comision_eur": 1, "cambio": 1.09}

        with patch("calculadora.tipos_cambio.obtener_tipo_cambio") as mock_bce:
            total = a_euros(op)

        mock_bce.assert_not_called()
        self.assertEqual(total, Decimal("2753.29"))

    def test_busca_en_el_bce_si_falta_el_cambio(self):
        op = {"fecha": "15/01/2024", "tipo": "compra", "acciones": 10, "precio_usd": 300, "comision_eur": 1}

        with patch("calculadora.tipos_cambio.obtener_tipo_cambio", return_value=1.09) as mock_bce:
            total = a_euros(op)

        mock_bce.assert_called_once_with(date(2024, 1, 15), "USD")
        self.assertEqual(total, Decimal("2753.29"))

    def test_sin_cambio_y_sin_anio_en_la_fecha_da_error_claro(self):
        op = {"fecha": "15/01", "tipo": "compra", "acciones": 10, "precio_usd": 300, "comision_eur": 1}

        with self.assertRaises(ValueError):
            a_euros(op)


class TestComisionEnDivisaDeLaOperacion(unittest.TestCase):
    """La comision (op["comision_eur"]) se asume en EUR por defecto. Si
    op["comision_en_divisa_operacion"] es verdadero, se convierte con el
    MISMO tipo (y la misma fecha) que el importe principal -- nunca uno
    distinto ni una llamada aparte al BCE."""

    def test_por_defecto_la_comision_se_suma_tal_cual_sin_convertir(self):
        # Si los 11 se trataran como USD y se convirtieran, darian
        # exactamente 10.00 EUR (11 / 1.10): por defecto NO se convierten,
        # asi que el resultado tiene que reflejar 11.00, no 10.00.
        op = {
            "fecha": "15/01/2024", "tipo": "compra", "acciones": 10, "precio_usd": 100,
            "divisa": "USD", "cambio": 1.10, "comision_eur": 11,
        }
        self.assertEqual(a_euros(op), Decimal("920.09"))   # 909.09 (1000/1.10) + 11.00

    def test_con_la_opcion_activada_la_comision_se_convierte_con_el_mismo_tipo(self):
        op = {
            "fecha": "15/01/2024", "tipo": "compra", "acciones": 10, "precio_usd": 100,
            "divisa": "USD", "cambio": 1.10, "comision_eur": 11,
            "comision_en_divisa_operacion": True,
        }
        self.assertEqual(a_euros(op), Decimal("919.09"))   # 909.09 + (11 / 1.10 = 10.00)

    def test_en_una_venta_la_comision_convertida_tambien_resta(self):
        op = {
            "fecha": "20/03/2024", "tipo": "venta", "acciones": 10, "precio_usd": 100,
            "divisa": "USD", "cambio": 1.10, "comision_eur": 11,
            "comision_en_divisa_operacion": True,
        }
        self.assertEqual(a_euros(op), Decimal("899.09"))   # 909.09 - 10.00

    def test_usa_el_mismo_tipo_que_el_importe_no_una_consulta_aparte(self):
        op = {
            "fecha": "15/01/2024", "tipo": "compra", "acciones": 10, "precio_usd": 100,
            "divisa": "USD", "comision_eur": 11, "comision_en_divisa_operacion": True,
        }
        with patch("calculadora.tipos_cambio.obtener_tipo_cambio", return_value=1.10) as mock_bce:
            total = a_euros(op)

        mock_bce.assert_called_once()   # una sola consulta al BCE, reutilizada para importe y comision
        self.assertEqual(total, Decimal("919.09"))

    def test_en_eur_la_opcion_no_tiene_ningun_efecto(self):
        # No hay tipo de cambio que aplicar en una operacion ya en EUR.
        op_normal = {"fecha": "10/01/2024", "tipo": "compra", "acciones": 2, "precio_usd": 50, "comision_eur": 1, "divisa": "EUR"}
        op_con_opcion = dict(op_normal, comision_en_divisa_operacion=True)

        self.assertEqual(a_euros(op_normal), a_euros(op_con_opcion))


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


class TestCalcularDesglose(unittest.TestCase):
    def test_coincide_con_calcular_detalle_y_anade_una_fila_por_venta(self):
        operaciones = [
            {"fecha": "15/01", "tipo": "compra", "acciones": 10, "precio_usd": 300, "comision_eur": 1, "cambio": 1.09},
            {"fecha": "20/03", "tipo": "compra", "acciones": 5,  "precio_usd": 350, "comision_eur": 1, "cambio": 1.08},
            {"fecha": "10/09", "tipo": "venta",  "acciones": 12, "precio_usd": 400, "comision_eur": 1, "cambio": 1.10},
        ]

        with contextlib.redirect_stdout(io.StringIO()):
            ganancia, lotes_finales, detalle_ventas = calcular_desglose(operaciones)
            ganancia_detalle, lotes_detalle = calcular_detalle(operaciones)

        self.assertEqual(ganancia, ganancia_detalle)
        self.assertEqual(lotes_finales, lotes_detalle)

        self.assertEqual(len(detalle_ventas), 1)
        fila = detalle_ventas[0]
        self.assertEqual(fila["fecha"], "10/09")
        self.assertEqual(fila["bloqueado"], Decimal("0"))
        self.assertEqual(fila["resultado_bruto"], fila["resultado_declarado"])
        self.assertEqual(f"{fila['resultado_declarado']:.2f}", "960.80")

    def test_fila_de_venta_bloqueada_muestra_bruto_y_declarado_distintos(self):
        operaciones = [
            {"fecha": "10/01", "tipo": "compra", "acciones": 10, "precio_usd": 100, "comision_eur": 1, "cambio": 1.00},
            {"fecha": "15/03", "tipo": "venta",  "acciones": 10, "precio_usd": 70,  "comision_eur": 1, "cambio": 1.00},
            {"fecha": "05/04", "tipo": "compra", "acciones": 4,  "precio_usd": 75,  "comision_eur": 1, "cambio": 1.00},
        ]

        with contextlib.redirect_stdout(io.StringIO()):
            _, _, detalle_ventas = calcular_desglose(operaciones)

        fila = detalle_ventas[0]
        self.assertEqual(f"{fila['resultado_bruto']:.2f}", "-302.00")
        self.assertEqual(f"{fila['bloqueado']:.2f}", "120.80")
        self.assertEqual(f"{fila['resultado_declarado']:.2f}", "-181.20")


class TestAutoRecompra(unittest.TestCase):
    """Bug real reportado en producción: la compra que alimenta por FIFO la
    venta que se está evaluando cae dentro de la ventana de 2 meses de esa
    misma venta, y se contaba a sí misma como "recompra". La web daba
    bruto -165.75 / bloqueado 102.00 / declarado -63.75; el valor correcto
    (verificado a mano) es bruto -102.00 / bloqueado 61.20 / declarado -40.80."""

    OPERACIONES = [
        {"fecha": "15/05/2024", "tipo": "compra", "acciones": 10, "precio_usd": 50.00, "comision_eur": 1.00, "divisa": "EUR"},
        {"fecha": "10/07/2024", "tipo": "venta",  "acciones": 10, "precio_usd": 40.00, "comision_eur": 1.00, "divisa": "EUR"},
        {"fecha": "20/07/2024", "tipo": "compra", "acciones": 6,  "precio_usd": 42.00, "comision_eur": 1.00, "divisa": "EUR"},
    ]

    def test_bruto_bloqueado_y_declarado(self):
        with contextlib.redirect_stdout(io.StringIO()):
            ganancia, lotes_finales, detalle_ventas = calcular_desglose(self.OPERACIONES)

        fila = detalle_ventas[0]
        self.assertEqual(f"{fila['resultado_bruto']:.2f}", "-102.00")
        self.assertEqual(f"{fila['bloqueado']:.2f}", "61.20")
        self.assertEqual(f"{fila['resultado_declarado']:.2f}", "-40.80")
        self.assertEqual(f"{ganancia:.2f}", "-40.80")

    def test_lote_recomprado_de_verdad_queda_con_coste_314_20(self):
        with contextlib.redirect_stdout(io.StringIO()):
            _, lotes_finales, _ = calcular_desglose(self.OPERACIONES)

        self.assertEqual(len(lotes_finales), 1)
        lote = lotes_finales[0]
        self.assertEqual(lote["fecha"], "20/07/2024")
        coste_total = (lote["coste_accion"] * lote["acciones"]).quantize(Decimal("0.01"))
        self.assertEqual(coste_total, Decimal("314.20"))


class TestFIFORespetaFechaNoOrdenDelFichero(unittest.TestCase):
    """El FIFO tiene que consumir por fecha parseada, no por el orden en
    que aparecen las filas. Antes de este fix, calcular_detalle() confiaba
    en que el llamador ya viniera ordenado (lector_csv.py sí ordena, pero
    llamar a calcular_detalle()/calcular_desglose() directamente con una
    lista desordenada explotaba con "vendes mas acciones de las que
    tienes", o peor, podia dar un resultado incorrecto sin avisar si las
    cantidades cuadraban por casualidad). Este test da las operaciones
    deliberadamente desordenadas (la compra más antigua es la ÚLTIMA de la
    lista) y comprueba que el resultado es el mismo que si vinieran en
    orden."""

    def test_orden_de_entrada_no_afecta_al_resultado(self):
        operaciones_ordenadas = [
            {"fecha": "15/01/2024", "tipo": "compra", "acciones": 10, "precio_usd": 100, "comision_eur": 1, "divisa": "EUR"},
            {"fecha": "20/03/2024", "tipo": "compra", "acciones": 5,  "precio_usd": 120, "comision_eur": 1, "divisa": "EUR"},
            {"fecha": "10/09/2024", "tipo": "venta",  "acciones": 12, "precio_usd": 150, "comision_eur": 1, "divisa": "EUR"},
        ]
        operaciones_desordenadas = [
            operaciones_ordenadas[2],   # la venta primero
            operaciones_ordenadas[1],   # la compra mas reciente segunda
            operaciones_ordenadas[0],   # la compra mas antigua la ultima
        ]

        with contextlib.redirect_stdout(io.StringIO()):
            ganancia_ordenada, lotes_ordenados = calcular_detalle(operaciones_ordenadas)
        with contextlib.redirect_stdout(io.StringIO()):
            ganancia_desordenada, lotes_desordenados = calcular_detalle(operaciones_desordenadas)

        self.assertEqual(ganancia_ordenada, ganancia_desordenada)
        self.assertEqual(lotes_ordenados, lotes_desordenados)


if __name__ == "__main__":
    unittest.main()
