"""Tests del lector de CSV generico (lector_csv.py)."""

import contextlib
import io
import os
import tempfile
import unittest

from calculadora import calcular_detalle
from lector_csv import (
    ErrorLectorCSV,
    cargar_preset,
    detectar_csv,
    guardar_preset,
    leer_operaciones,
    listar_presets,
)

RUTA_EJEMPLO = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ejemplos", "extracto_generico_ejemplo.csv")

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
        operaciones_por_valor, avisos = leer_operaciones(ruta, MAPEO_EJEMPLO)

        self.assertEqual(avisos, [])
        self.assertEqual(operaciones_por_valor["XX0000000000"][0]["fecha"], "05/01/2024")

    def test_fecha_aaaa_mm_dd(self):
        ruta = _csv_temporal(
            "Fecha,Tipo,ISIN,Cantidad,Precio,Moneda,Comision\n"
            "2024-01-05,Compra,XX0000000000,10,80,EUR,1\n"
        )
        operaciones_por_valor, avisos = leer_operaciones(ruta, MAPEO_EJEMPLO)

        self.assertEqual(avisos, [])
        self.assertEqual(operaciones_por_valor["XX0000000000"][0]["fecha"], "05/01/2024")

    def test_ordena_las_operaciones_por_fecha_aunque_el_csv_no_venga_ordenado(self):
        ruta = _csv_temporal(
            "Fecha,Tipo,ISIN,Cantidad,Precio,Moneda,Comision\n"
            "10/02/2024,Compra,XX0000000000,5,80,EUR,1\n"
            "05/01/2024,Compra,XX0000000000,10,80,EUR,1\n"
        )
        operaciones_por_valor, _ = leer_operaciones(ruta, MAPEO_EJEMPLO)

        fechas = [op["fecha"] for op in operaciones_por_valor["XX0000000000"]]
        self.assertEqual(fechas, ["05/01/2024", "10/02/2024"])


class TestDecimales(unittest.TestCase):
    def test_decimal_con_punto(self):
        ruta = _csv_temporal(
            "Fecha,Tipo,ISIN,Cantidad,Precio,Moneda,Comision\n"
            "05/01/2024,Compra,XX0000000000,2.5,80.75,EUR,1\n"
        )
        operaciones_por_valor, _ = leer_operaciones(ruta, MAPEO_EJEMPLO)

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
        operaciones_por_valor, _ = leer_operaciones(ruta, MAPEO_EJEMPLO)

        op = operaciones_por_valor["XX0000000000"][0]
        self.assertEqual(op["acciones"], "2.5")
        self.assertEqual(op["precio_usd"], "80.75")

    def test_miles_con_punto_y_decimal_con_coma(self):
        ruta = _csv_temporal(
            "Fecha;Tipo;ISIN;Cantidad;Precio;Moneda;Comision\n"
            "05/01/2024;Compra;XX0000000000;10;1.234,56;EUR;1\n"
        )
        operaciones_por_valor, _ = leer_operaciones(ruta, MAPEO_EJEMPLO)

        self.assertEqual(operaciones_por_valor["XX0000000000"][0]["precio_usd"], "1234.56")


class TestFilasQueNoSonOperaciones(unittest.TestCase):
    def test_dividendo_se_ignora_y_avisa_sin_colarse_en_el_calculo(self):
        ruta = _csv_temporal(
            "Fecha,Tipo,ISIN,Cantidad,Precio,Moneda,Comision\n"
            "05/01/2024,Compra,XX0000000000,10,80,EUR,1\n"
            "10/01/2024,Dividendo,XX0000000000,10,0.5,EUR,0\n"
        )
        operaciones_por_valor, avisos = leer_operaciones(ruta, MAPEO_EJEMPLO)

        self.assertEqual(len(operaciones_por_valor["XX0000000000"]), 1)
        self.assertEqual(len(avisos), 1)
        self.assertIn("Dividendo", avisos[0])

    def test_tipo_desconocido_se_ignora_y_avisa(self):
        ruta = _csv_temporal(
            "Fecha,Tipo,ISIN,Cantidad,Precio,Moneda,Comision\n"
            "05/01/2024,Compra,XX0000000000,10,80,EUR,1\n"
            "10/01/2024,Splits,XX0000000000,10,0,EUR,0\n"
        )
        operaciones_por_valor, avisos = leer_operaciones(ruta, MAPEO_EJEMPLO)

        self.assertEqual(len(operaciones_por_valor["XX0000000000"]), 1)
        self.assertEqual(len(avisos), 1)
        self.assertIn("no reconocido", avisos[0])


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

        operaciones_por_valor, _ = leer_operaciones(
            RUTA_EJEMPLO, preset="mi_broker", ruta_presets=self.ruta_presets
        )

        self.assertIn("IE00B4L5Y983", operaciones_por_valor)


class TestExtractoDeEjemploCompleto(unittest.TestCase):
    def test_10_filas_compras_ventas_dividendo_y_fraccion_dan_154_41(self):
        # Verificado a mano: 3 ventas con ganancia (78.20 + 28.80 + 47.41),
        # el dividendo se ignora, quedan lotes fraccionarios sin vender.
        operaciones_por_valor, avisos = leer_operaciones(RUTA_EJEMPLO, MAPEO_EJEMPLO)

        self.assertEqual(len(avisos), 1)   # solo la fila del dividendo
        self.assertEqual(list(operaciones_por_valor.keys()), ["IE00B4L5Y983"])

        operaciones = operaciones_por_valor["IE00B4L5Y983"]
        self.assertEqual(len(operaciones), 9)   # 10 filas - 1 dividendo ignorado

        with contextlib.redirect_stdout(io.StringIO()):
            ganancia, lotes_finales = calcular_detalle(operaciones)

        self.assertEqual(f"{ganancia:.2f}", "154.41")
        self.assertEqual(len(lotes_finales), 5)


if __name__ == "__main__":
    unittest.main()
