"""Tests de motor_web.py (el punto de entrada para la web/Pyodide).

No tocan la cache real del BCE ni la red: se mockean tipos_cambio y
calculadora.tipos_cambio donde haga falta, igual que en los otros tests
de este proyecto.
"""

import os
import unittest
from unittest.mock import patch

import tipos_cambio
from motor_web import procesar_csv

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

FRESCURA_FIJA = patch.multiple(
    "motor_web.tipos_cambio",
    fecha_mas_reciente=lambda *a, **k: __import__("datetime").date(2026, 8, 25),
    antiguedad_en_dias=lambda *a, **k: 2,
)


def _leer(ruta):
    with open(ruta, encoding="utf-8-sig") as f:
        return f.read()


class TestInfoFrescura(unittest.TestCase):
    def test_ok_cuando_tipos_cambio_responde(self):
        with FRESCURA_FIJA:
            from motor_web import info_frescura_bce
            info = info_frescura_bce()

        self.assertTrue(info["ok"])
        self.assertEqual(info["fecha_mas_reciente"], "25/08/2026")
        self.assertEqual(info["dias_de_antiguedad"], 2)
        self.assertFalse(info["desactualizado"])

    def test_error_claro_si_tipos_cambio_falla(self):
        with patch("motor_web.tipos_cambio.fecha_mas_reciente", side_effect=tipos_cambio.ErrorTipoCambio("boom")):
            from motor_web import info_frescura_bce
            info = info_frescura_bce()

        self.assertFalse(info["ok"])
        self.assertIn("boom", info["error"])
        self.assertIsNone(info["fecha_mas_reciente"])


class TestProcesarCSV(unittest.TestCase):
    def test_extracto_eur_sin_bce_da_totales_completos(self):
        with FRESCURA_FIJA:
            resultado = procesar_csv(_leer(RUTA_EJEMPLO), MAPEO_EJEMPLO)

        self.assertIsNone(resultado["error_lectura"])
        self.assertTrue(resultado["totales"]["completo"])
        self.assertEqual(resultado["totales"]["ganancia_patrimonial"], "154.41")
        self.assertEqual(resultado["dividendos"]["bruto_total"], "5.25")
        self.assertEqual(resultado["dividendos"]["retencion_total"], "0.00")

        valor = resultado["valores"]["IE00B4L5Y983"]
        self.assertIsNone(valor["error"])
        self.assertEqual(valor["ganancia"], "154.41")
        self.assertEqual(len(valor["desglose"]), 3)   # 3 ventas
        self.assertEqual(len(valor["lotes_pendientes"]), 5)

    def test_error_de_mapeo_no_calcula_nada_y_lo_dice(self):
        mapeo_incompleto = {k: v for k, v in MAPEO_EJEMPLO.items() if k != "precio"}

        with FRESCURA_FIJA:
            resultado = procesar_csv(_leer(RUTA_EJEMPLO), mapeo_incompleto)

        self.assertIsNotNone(resultado["error_lectura"])
        self.assertIn("precio", resultado["error_lectura"])
        self.assertFalse(resultado["totales"]["completo"])
        self.assertIsNone(resultado["totales"]["ganancia_patrimonial"])
        self.assertEqual(resultado["valores"], {})

    def test_ninguna_operacion_leida_no_muestra_0_00_como_si_fuera_valido(self):
        # Bug real reportado en producción: un mapeo que hace que TODAS las
        # filas se ignoren (p.ej. "Precio" apuntando a una columna con
        # texto no numerico) no da error_lectura -- lector_csv simplemente
        # acaba con operaciones_por_valor vacio y todo en avisos. Antes de
        # este fix, eso se traducia en "ganancia_patrimonial": "0.00" con
        # completo=True: un cero con pinta de resultado valido cuando en
        # realidad no se ha calculado nada.
        contenido = (
            "Fecha;Tipo;ISIN;Cantidad;Precio;Moneda;Comision\n"
            "10/01/2024;Compra;IE00B4L5Y983;10;no-es-un-numero;EUR;1\n"
        )

        with FRESCURA_FIJA:
            resultado = procesar_csv(contenido, MAPEO_EJEMPLO)

        self.assertIsNone(resultado["error_lectura"])   # el mapeo en si es valido
        self.assertEqual(len(resultado["avisos_lectura"]), 1)
        self.assertEqual(resultado["valores"], {})
        self.assertFalse(resultado["totales"]["completo"])
        self.assertIsNone(resultado["totales"]["ganancia_patrimonial"])
        self.assertIsNotNone(resultado["totales"]["motivo"])

    def test_fichero_solo_con_dividendos_no_muestra_0_00_como_si_fuera_valido(self):
        contenido = (
            "Fecha;Tipo;ISIN;Cantidad;Precio;Moneda;Comision\n"
            "10/01/2024;Dividendo;IE00B4L5Y983;10;1.00;EUR;1.90\n"
        )

        with FRESCURA_FIJA:
            resultado = procesar_csv(contenido, MAPEO_EJEMPLO)

        self.assertFalse(resultado["totales"]["completo"])
        self.assertIsNone(resultado["totales"]["ganancia_patrimonial"])
        self.assertEqual(resultado["dividendos"]["bruto_total"], "10.00")   # los dividendos SI se calculan

    def test_valor_sin_tipo_de_cambio_todavia_no_da_numero_a_medias(self):
        contenido = (
            "Fecha;Tipo;ISIN;Cantidad;Precio;Moneda;Comision\n"
            "10/01/2024;Compra;US0000000001;10;100;USD;1\n"
            "26/08/2026;Venta;US0000000001;10;110;USD;1\n"
        )

        with FRESCURA_FIJA, patch(
            "calculadora.tipos_cambio.obtener_tipo_cambio",
            side_effect=tipos_cambio.TipoAunNoDisponible("todavia no hay tipo para esa fecha"),
        ):
            resultado = procesar_csv(contenido, MAPEO_EJEMPLO)

        self.assertFalse(resultado["totales"]["completo"])
        self.assertIsNone(resultado["totales"]["ganancia_patrimonial"])

        valor = resultado["valores"]["US0000000001"]
        self.assertIsNotNone(valor["error"])
        self.assertIn("todavia", valor["error"])
        self.assertIsNone(valor["ganancia"])
        self.assertIsNone(valor["desglose"])

    def test_un_valor_que_falla_no_impide_calcular_los_demas(self):
        contenido = (
            "Fecha;Tipo;ISIN;Cantidad;Precio;Moneda;Comision\n"
            "10/01/2024;Compra;IE00B4L5Y983;10;80;EUR;1\n"
            "10/06/2024;Venta;IE00B4L5Y983;10;90;EUR;1\n"
            "10/01/2024;Compra;US0000000001;10;100;USD;1\n"
            "26/08/2026;Venta;US0000000001;10;110;USD;1\n"
        )

        with FRESCURA_FIJA, patch(
            "calculadora.tipos_cambio.obtener_tipo_cambio",
            side_effect=tipos_cambio.TipoAunNoDisponible("todavia no hay tipo para esa fecha"),
        ):
            resultado = procesar_csv(contenido, MAPEO_EJEMPLO)

        # El total conjunto no se puede dar por bueno...
        self.assertFalse(resultado["totales"]["completo"])
        self.assertIsNone(resultado["totales"]["ganancia_patrimonial"])

        # ...pero el valor que SI se pudo calcular entero, se muestra.
        valor_ok = resultado["valores"]["IE00B4L5Y983"]
        self.assertIsNone(valor_ok["error"])
        self.assertEqual(valor_ok["ganancia"], "98.00")

        valor_error = resultado["valores"]["US0000000001"]
        self.assertIsNotNone(valor_error["error"])
        self.assertIsNone(valor_error["ganancia"])


RUTA_PRUEBA_5_DOLARES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "pruebas", "prueba_5_dolares.csv")

# Tipos oficiales del BCE reales para las fechas de prueba_5_dolares.csv,
# verificados a mano contra eurofxref-hist.xml (incluido el 01/05/2024,
# festivo TARGET sin publicacion, que cae al ultimo tipo conocido del
# 30/04/2024). Se mockean aqui para que el test no dependa de la cache
# real ni de la red.
TIPOS_BCE_PRUEBA_5 = {
    "2024-03-15": 1.0892,
    "2024-04-02": 1.0749,
    "2024-05-01": 1.0718,
    "2024-01-02": 1.0956,
    "2024-12-02": 1.0507,
}


def _tipo_bce_prueba_5(fecha, divisa):
    return TIPOS_BCE_PRUEBA_5[fecha.isoformat()]


class TestComisionEnDivisaDeLaOperacionEnPruebaReal(unittest.TestCase):
    """Con la opcion por defecto (comision en EUR), prueba_5_dolares.csv
    tiene que seguir dando exactamente lo mismo que antes de añadir esta
    opcion: todas sus comisiones son 0, asi que activar o no la conversion
    no puede cambiar ni un centimo."""

    def test_por_defecto_no_cambia_ni_un_centimo(self):
        with FRESCURA_FIJA, patch("calculadora.tipos_cambio.obtener_tipo_cambio", side_effect=_tipo_bce_prueba_5):
            resultado = procesar_csv(_leer(RUTA_PRUEBA_5_DOLARES), MAPEO_EJEMPLO)

        self.assertTrue(resultado["totales"]["completo"])
        self.assertEqual(resultado["totales"]["ganancia_patrimonial"], "341.70")
        self.assertEqual(resultado["valores"]["US0378331005"]["ganancia"], "0.00")
        self.assertEqual(resultado["valores"]["US5949181045"]["ganancia"], "98.68")
        self.assertEqual(resultado["valores"]["US02079K3059"]["ganancia"], "195.02")
        self.assertEqual(resultado["valores"]["IE00B4L5Y983"]["ganancia"], "48.00")

    def test_activar_la_conversion_da_el_mismo_resultado_porque_las_comisiones_son_cero(self):
        with FRESCURA_FIJA, patch("calculadora.tipos_cambio.obtener_tipo_cambio", side_effect=_tipo_bce_prueba_5):
            resultado = procesar_csv(
                _leer(RUTA_PRUEBA_5_DOLARES), MAPEO_EJEMPLO, comision_en_divisa_operacion=True
            )

        self.assertEqual(resultado["totales"]["ganancia_patrimonial"], "341.70")


if __name__ == "__main__":
    unittest.main()
