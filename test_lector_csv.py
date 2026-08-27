"""Tests del lector de CSV generico (lector_csv.py)."""

import contextlib
import io
import os
import tempfile
import unittest
from decimal import Decimal

from calculadora import calcular_detalle
from lector_csv import (
    ErrorLectorCSV,
    cargar_preset,
    detectar_csv,
    guardar_preset,
    leer_operaciones,
    listar_presets,
    resumir_dividendos,
)

RUTA_EJEMPLO = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ejemplos", "extracto_generico_ejemplo.csv")
RUTA_EJEMPLO_PERDIDA = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "ejemplos", "extracto_generico_perdida_dos_meses.csv"
)

MAPEO_EJEMPLO = {
    "fecha": "Fecha",
    "tipo": "Tipo",
    "valor": "ISIN",
    "cantidad": "Cantidad",
    "precio": "Precio",
    "divisa": "Moneda",
    "comision": "Comision",
}


def _csv_temporal(contenido):
    directorio = tempfile.mkdtemp()
    ruta = os.path.join(directorio, "extracto.csv")
    with open(ruta, "w", encoding="utf-8", newline="") as f:
        f.write(contenido)
    return ruta


class TestLeerDesdeContenidoEnMemoria(unittest.TestCase):
    """La web (Pyodide) nunca escribe el CSV a disco: pasa el texto tal
    cual, leido del navegador con el File API. Estas dos funciones tienen
    que aceptarlo igual que una ruta de fichero."""

    CONTENIDO = (
        "Fecha;Tipo;ISIN;Cantidad;Precio;Moneda;Comision\n"
        "05/01/2024;Compra;XX0000000000;10;80,50;EUR;1\n"
    )

    def test_detectar_csv_desde_contenido(self):
        cabeceras, filas_muestra, separador = detectar_csv(contenido=self.CONTENIDO)

        self.assertEqual(separador, ";")
        self.assertEqual(cabeceras, ["Fecha", "Tipo", "ISIN", "Cantidad", "Precio", "Moneda", "Comision"])
        self.assertEqual(filas_muestra[0][0], "05/01/2024")

    def test_leer_operaciones_desde_contenido(self):
        operaciones_por_valor, _, avisos = leer_operaciones(contenido=self.CONTENIDO, mapeo=MAPEO_EJEMPLO)

        self.assertEqual(avisos, [])
        op = operaciones_por_valor["XX0000000000"][0]
        self.assertEqual(op["acciones"], "10")
        self.assertEqual(op["precio_usd"], "80.50")

    def test_sin_ruta_ni_contenido_da_error_claro(self):
        with self.assertRaises(ErrorLectorCSV):
            leer_operaciones(mapeo=MAPEO_EJEMPLO)


class TestDetectarCSV(unittest.TestCase):
    def test_detecta_cabeceras_y_primeras_filas(self):
        cabeceras, filas_muestra, separador = detectar_csv(RUTA_EJEMPLO, num_filas_muestra=3)

        self.assertEqual(separador, ";")
        self.assertEqual(cabeceras, ["Fecha", "Tipo", "ISIN", "Cantidad", "Precio", "Moneda", "Comision"])
        self.assertEqual(len(filas_muestra), 3)
        self.assertEqual(filas_muestra[0][0], "05/01/2024")


class TestFormatosDeFecha(unittest.TestCase):
    def test_fecha_dd_mm_aaaa(self):
        ruta = _csv_temporal(
            "Fecha,Tipo,ISIN,Cantidad,Precio,Moneda,Comision\n"
            "05/01/2024,Compra,XX0000000000,10,80,EUR,1\n"
        )
        operaciones_por_valor, _, avisos = leer_operaciones(ruta, MAPEO_EJEMPLO)

        self.assertEqual(avisos, [])
        self.assertEqual(operaciones_por_valor["XX0000000000"][0]["fecha"], "05/01/2024")

    def test_fecha_aaaa_mm_dd(self):
        ruta = _csv_temporal(
            "Fecha,Tipo,ISIN,Cantidad,Precio,Moneda,Comision\n"
            "2024-01-05,Compra,XX0000000000,10,80,EUR,1\n"
        )
        operaciones_por_valor, _, avisos = leer_operaciones(ruta, MAPEO_EJEMPLO)

        self.assertEqual(avisos, [])
        self.assertEqual(operaciones_por_valor["XX0000000000"][0]["fecha"], "05/01/2024")

    def test_ordena_las_operaciones_por_fecha_aunque_el_csv_no_venga_ordenado(self):
        ruta = _csv_temporal(
            "Fecha,Tipo,ISIN,Cantidad,Precio,Moneda,Comision\n"
            "10/02/2024,Compra,XX0000000000,5,80,EUR,1\n"
            "05/01/2024,Compra,XX0000000000,10,80,EUR,1\n"
        )
        operaciones_por_valor, _, _ = leer_operaciones(ruta, MAPEO_EJEMPLO)

        fechas = [op["fecha"] for op in operaciones_por_valor["XX0000000000"]]
        self.assertEqual(fechas, ["05/01/2024", "10/02/2024"])


class TestDecimales(unittest.TestCase):
    def test_decimal_con_punto(self):
        ruta = _csv_temporal(
            "Fecha,Tipo,ISIN,Cantidad,Precio,Moneda,Comision\n"
            "05/01/2024,Compra,XX0000000000,2.5,80.75,EUR,1\n"
        )
        operaciones_por_valor, _, _ = leer_operaciones(ruta, MAPEO_EJEMPLO)

        op = operaciones_por_valor["XX0000000000"][0]
        self.assertEqual(op["acciones"], "2.5")
        self.assertEqual(op["precio_usd"], "80.75")

    def test_decimal_con_coma(self):
        # Separador de columnas ";" para que la coma decimal no choque con
        # el separador (como suelen exportar las hojas de calculo en ES).
        ruta = _csv_temporal(
            "Fecha;Tipo;ISIN;Cantidad;Precio;Moneda;Comision\n"
            "05/01/2024;Compra;XX0000000000;2,5;80,75;EUR;1\n"
        )
        operaciones_por_valor, _, _ = leer_operaciones(ruta, MAPEO_EJEMPLO)

        op = operaciones_por_valor["XX0000000000"][0]
        self.assertEqual(op["acciones"], "2.5")
        self.assertEqual(op["precio_usd"], "80.75")

    def test_miles_con_punto_y_decimal_con_coma(self):
        ruta = _csv_temporal(
            "Fecha;Tipo;ISIN;Cantidad;Precio;Moneda;Comision\n"
            "05/01/2024;Compra;XX0000000000;10;1.234,56;EUR;1\n"
        )
        operaciones_por_valor, _, _ = leer_operaciones(ruta, MAPEO_EJEMPLO)

        self.assertEqual(operaciones_por_valor["XX0000000000"][0]["precio_usd"], "1234.56")


class TestDividendos(unittest.TestCase):
    def test_dividendo_no_entra_en_operaciones_ni_en_avisos(self):
        ruta = _csv_temporal(
            "Fecha,Tipo,ISIN,Cantidad,Precio,Moneda,Comision\n"
            "05/01/2024,Compra,XX0000000000,10,80,EUR,1\n"
            "10/01/2024,Dividendo,XX0000000000,10,0.5,EUR,0\n"
        )
        operaciones_por_valor, dividendos_por_valor, avisos = leer_operaciones(ruta, MAPEO_EJEMPLO)

        self.assertEqual(len(operaciones_por_valor["XX0000000000"]), 1)   # solo la compra
        self.assertEqual(avisos, [])   # un dividendo no es un aviso, es su propio apartado

        dividendos = dividendos_por_valor["XX0000000000"]
        self.assertEqual(len(dividendos), 1)
        self.assertEqual(dividendos[0]["bruto"], Decimal("5.0"))
        self.assertEqual(dividendos[0]["retencion"], Decimal("0"))

    def test_retencion_se_toma_de_la_columna_de_comision(self):
        ruta = _csv_temporal(
            "Fecha,Tipo,ISIN,Cantidad,Precio,Moneda,Comision\n"
            "10/01/2024,Dividendo,XX0000000000,10,1.00,EUR,1.90\n"
        )
        _, dividendos_por_valor, _ = leer_operaciones(ruta, MAPEO_EJEMPLO)

        dividendo = dividendos_por_valor["XX0000000000"][0]
        self.assertEqual(dividendo["bruto"], Decimal("10.00"))
        self.assertEqual(dividendo["retencion"], Decimal("1.90"))

    def test_resumir_dividendos_suma_bruto_y_retencion(self):
        ruta = _csv_temporal(
            "Fecha,Tipo,ISIN,Cantidad,Precio,Moneda,Comision\n"
            "10/01/2024,Dividendo,XX0000000000,10,1.00,EUR,1.90\n"
            "10/04/2024,Dividendo,XX0000000000,10,1.20,EUR,2.28\n"
        )
        _, dividendos_por_valor, _ = leer_operaciones(ruta, MAPEO_EJEMPLO)

        resumen = resumir_dividendos(dividendos_por_valor)

        self.assertEqual(resumen["bruto_total"], Decimal("22.00"))
        self.assertEqual(resumen["retencion_total"], Decimal("4.18"))


class TestFilasQueNoSonOperaciones(unittest.TestCase):
    def test_tipo_desconocido_se_ignora_y_avisa(self):
        ruta = _csv_temporal(
            "Fecha,Tipo,ISIN,Cantidad,Precio,Moneda,Comision\n"
            "05/01/2024,Compra,XX0000000000,10,80,EUR,1\n"
            "10/01/2024,Splits,XX0000000000,10,0,EUR,0\n"
        )
        operaciones_por_valor, dividendos_por_valor, avisos = leer_operaciones(ruta, MAPEO_EJEMPLO)

        self.assertEqual(len(operaciones_por_valor["XX0000000000"]), 1)
        self.assertEqual(dividendos_por_valor, {})
        self.assertEqual(len(avisos), 1)
        self.assertIn("no reconocido", avisos[0])

    def test_traspaso_se_ignora_y_avisa(self):
        ruta = _csv_temporal(
            "Fecha,Tipo,ISIN,Cantidad,Precio,Moneda,Comision\n"
            "05/01/2024,Compra,XX0000000000,10,80,EUR,1\n"
            "10/01/2024,Traspaso,XX0000000000,10,0,EUR,0\n"
        )
        operaciones_por_valor, _, avisos = leer_operaciones(ruta, MAPEO_EJEMPLO)

        self.assertEqual(len(operaciones_por_valor["XX0000000000"]), 1)
        self.assertEqual(len(avisos), 1)
        self.assertIn("Traspaso", avisos[0])


class TestErroresClaros(unittest.TestCase):
    def test_falta_columna_obligatoria_en_el_mapeo(self):
        mapeo_incompleto = {k: v for k, v in MAPEO_EJEMPLO.items() if k != "precio"}

        with self.assertRaises(ErrorLectorCSV) as contexto:
            leer_operaciones(RUTA_EJEMPLO, mapeo_incompleto)

        self.assertIn("precio", str(contexto.exception))

    def test_columna_del_mapeo_no_existe_en_el_csv(self):
        mapeo_con_columna_inventada = dict(MAPEO_EJEMPLO, precio="Precio unitario (no existe)")

        with self.assertRaises(ErrorLectorCSV) as contexto:
            leer_operaciones(RUTA_EJEMPLO, mapeo_con_columna_inventada)

        self.assertIn("Precio unitario (no existe)", str(contexto.exception))


class TestPresets(unittest.TestCase):
    def setUp(self):
        directorio = tempfile.mkdtemp()
        self.ruta_presets = os.path.join(directorio, "presets_broker.json")

    def test_guardar_y_cargar_preset(self):
        guardar_preset("mi_broker", MAPEO_EJEMPLO, ruta_presets=self.ruta_presets)

        mapeo_cargado, tipos_cargados = cargar_preset("mi_broker", ruta_presets=self.ruta_presets)

        self.assertEqual(mapeo_cargado, MAPEO_EJEMPLO)
        self.assertIn("compra", tipos_cargados)
        self.assertIn("mi_broker", listar_presets(ruta_presets=self.ruta_presets))

    def test_cargar_preset_inexistente_da_error_claro(self):
        with self.assertRaises(ErrorLectorCSV):
            cargar_preset("no_existe", ruta_presets=self.ruta_presets)

    def test_leer_operaciones_acepta_un_preset(self):
        guardar_preset("mi_broker", MAPEO_EJEMPLO, ruta_presets=self.ruta_presets)

        operaciones_por_valor, _, _ = leer_operaciones(
            RUTA_EJEMPLO, preset="mi_broker", ruta_presets=self.ruta_presets
        )

        self.assertIn("IE00B4L5Y983", operaciones_por_valor)


class TestExtractoDeEjemploCompleto(unittest.TestCase):
    def test_10_filas_compras_ventas_dividendo_y_fraccion_dan_154_41(self):
        # Verificado a mano: 3 ventas con ganancia (78.20 + 28.80 + 47.41).
        # El dividendo (5.25 EUR brutos, 0 de retencion) sale aparte y no
        # entra en este calculo.
        operaciones_por_valor, dividendos_por_valor, avisos = leer_operaciones(RUTA_EJEMPLO, MAPEO_EJEMPLO)

        self.assertEqual(avisos, [])
        self.assertEqual(list(operaciones_por_valor.keys()), ["IE00B4L5Y983"])

        operaciones = operaciones_por_valor["IE00B4L5Y983"]
        self.assertEqual(len(operaciones), 9)   # 10 filas - 1 dividendo (aparte, no descartado)

        with contextlib.redirect_stdout(io.StringIO()):
            ganancia, lotes_finales = calcular_detalle(operaciones)

        self.assertEqual(f"{ganancia:.2f}", "154.41")
        self.assertEqual(len(lotes_finales), 5)

        resumen = resumir_dividendos(dividendos_por_valor)
        self.assertEqual(resumen["bruto_total"], Decimal("5.25"))
        self.assertEqual(resumen["retencion_total"], Decimal("0.00"))


class TestExtractoDeEjemploPerdidaDosMeses(unittest.TestCase):
    def test_perdida_con_recompra_parcial_en_2_meses_da_menos_40_80(self):
        # Verificado a mano: perdida total de la venta = 102.00 EUR.
        # Recompra 6 de las 10 vendidas dentro de los 2 meses -> se
        # bloquea 102.00 * 6/10 = 61.20; declarable = -102.00 + 61.20 = -40.80.
        # El lote recomprado (20/07/2024) queda con coste 253.00 + 61.20 = 314.20.
        operaciones_por_valor, dividendos_por_valor, avisos = leer_operaciones(RUTA_EJEMPLO_PERDIDA, MAPEO_EJEMPLO)

        self.assertEqual(avisos, [])
        self.assertEqual(dividendos_por_valor, {})
        self.assertEqual(list(operaciones_por_valor.keys()), ["DE000ABCDEF1"])

        operaciones = operaciones_por_valor["DE000ABCDEF1"]
        self.assertEqual(len(operaciones), 3)

        with contextlib.redirect_stdout(io.StringIO()):
            ganancia, lotes_finales = calcular_detalle(operaciones)

        self.assertEqual(f"{ganancia:.2f}", "-40.80")
        self.assertEqual(len(lotes_finales), 1)

        lote = lotes_finales[0]
        self.assertEqual(lote["fecha"], "20/07/2024")
        coste_total = (lote["coste_accion"] * lote["acciones"]).quantize(Decimal("0.01"))
        self.assertEqual(coste_total, Decimal("314.20"))


if __name__ == "__main__":
    unittest.main()
