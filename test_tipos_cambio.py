"""Tests de tipos_cambio.py.

No usan la cache real ni tocan la red: se construye un XML local con el
mismo formato del BCE (fixture) y se le dice a obtener_tipo_cambio que
lea de ahi con el parametro ruta_cache.
"""

import os
import tempfile
import unittest
from datetime import date, timedelta

from tipos_cambio import (
    DivisaNoDisponible,
    ErrorTipoCambio,
    FechaAnteriorAlHistorico,
    TipoAunNoDisponible,
    antiguedad_en_dias,
    fecha_mas_reciente,
    fichero_desactualizado,
    obtener_tipo_cambio,
    validar_fichero_bce,
)

XML_FIXTURE = """<?xml version="1.0" encoding="UTF-8"?>
<gesmes:Envelope xmlns:gesmes="http://www.gesmes.org/xml/2002-08-01"
                  xmlns="http://www.ecb.int/vocabulary/2002-08-01/eurofxref">
  <gesmes:subject>Reference rates</gesmes:subject>
  <Cube>
    <Cube time="2024-01-15">
      <Cube currency="USD" rate="1.0950"/>
      <Cube currency="GBP" rate="0.8580"/>
    </Cube>
    <Cube time="2024-01-12">
      <Cube currency="USD" rate="1.1000"/>
      <Cube currency="GBP" rate="0.8600"/>
    </Cube>
    <Cube time="2024-01-08">
      <Cube currency="USD" rate="1.0940"/>
      <Cube currency="GBP" rate="0.8570"/>
    </Cube>
    <!-- Datos reales del BCE (eurofxref-hist-90d.xml). 22 y 23/08/2026 no
         existen a proposito: son sabado y domingo. -->
    <Cube time="2026-08-25">
      <Cube currency="USD" rate="1.1662"/>
    </Cube>
    <Cube time="2026-08-24">
      <Cube currency="USD" rate="1.1664"/>
    </Cube>
    <Cube time="2026-08-21">
      <Cube currency="USD" rate="1.1699"/>
    </Cube>
    <Cube time="2026-08-20">
      <Cube currency="USD" rate="1.1681"/>
    </Cube>
    <Cube time="2026-08-19">
      <Cube currency="USD" rate="1.1605"/>
    </Cube>
  </Cube>
</gesmes:Envelope>
"""


class TestTiposCambio(unittest.TestCase):
    def setUp(self):
        directorio = tempfile.mkdtemp()
        self.ruta_cache = os.path.join(directorio, "eurofxref-hist-fixture.xml")
        with open(self.ruta_cache, "w", encoding="utf-8") as f:
            f.write(XML_FIXTURE)

    def _obtener(self, fecha, divisa="USD"):
        # url=None: si el test intentase descargar algo (no deberia, el
        # fixture ya existe) fallaria alto y claro en vez de ir a red.
        return obtener_tipo_cambio(fecha, divisa, ruta_cache=self.ruta_cache, url=None)

    def test_dia_laborable_normal_devuelve_el_tipo_de_ese_dia(self):
        # 15/01/2024 es lunes y tiene tipo publicado ese mismo dia.
        self.assertEqual(self._obtener(date(2024, 1, 15)), 1.0950)

    def test_domingo_devuelve_el_tipo_del_viernes_anterior(self):
        # 14/01/2024 es domingo; el BCE no publica ese dia, el viernes
        # anterior (12/01) es el ultimo publicado antes.
        self.assertEqual(self._obtener(date(2024, 1, 14)), 1.1000)

    def test_fecha_de_1990_lanza_error(self):
        with self.assertRaises(ValueError):
            self._obtener(date(1990, 1, 1))

    def test_viernes_21_agosto_2026_devuelve_el_tipo_de_ese_dia(self):
        self.assertEqual(self._obtener(date(2026, 8, 21)), 1.1699)

    def test_sabado_22_agosto_2026_cae_al_viernes_anterior(self):
        self.assertEqual(self._obtener(date(2026, 8, 22)), 1.1699)

    def test_domingo_23_agosto_2026_cae_al_viernes_anterior(self):
        self.assertEqual(self._obtener(date(2026, 8, 23)), 1.1699)

    def test_lunes_24_agosto_2026_devuelve_el_tipo_de_ese_dia(self):
        self.assertEqual(self._obtener(date(2026, 8, 24)), 1.1664)

    def test_martes_25_agosto_2026_devuelve_el_tipo_de_ese_dia(self):
        self.assertEqual(self._obtener(date(2026, 8, 25)), 1.1662)

    def test_fecha_de_1990_es_especificamente_anterior_al_historico(self):
        with self.assertRaises(FechaAnteriorAlHistorico):
            self._obtener(date(1990, 1, 1))

    def test_fecha_posterior_al_ultimo_dato_no_usa_el_mas_cercano(self):
        # El ultimo dato USD del fixture es 25/08/2026. Pedir el 26 no
        # debe caer al 25 silenciosamente: no hay dato para el 26 todavia.
        with self.assertRaises(TipoAunNoDisponible):
            self._obtener(date(2026, 8, 26))

    def test_divisa_no_disponible(self):
        with self.assertRaises(DivisaNoDisponible):
            self._obtener(date(2024, 1, 15), divisa="JPY")

    def test_fecha_mas_reciente_de_una_divisa(self):
        self.assertEqual(
            fecha_mas_reciente(divisa="USD", ruta_cache=self.ruta_cache, url=None),
            date(2026, 8, 25),
        )

    def test_fecha_mas_reciente_sin_divisa_toma_el_maximo_de_todas(self):
        # GBP en el fixture solo llega a 2024-01-15; USD llega a 2026-08-25.
        self.assertEqual(
            fecha_mas_reciente(ruta_cache=self.ruta_cache, url=None),
            date(2026, 8, 25),
        )

    def test_antiguedad_en_dias_con_hoy_fijo(self):
        dias = antiguedad_en_dias(divisa="USD", ruta_cache=self.ruta_cache, url=None, hoy=date(2026, 8, 30))
        self.assertEqual(dias, 5)

    def test_fichero_no_desactualizado_justo_en_el_umbral(self):
        desactualizado = fichero_desactualizado(
            umbral_dias=5, divisa="USD", ruta_cache=self.ruta_cache, url=None, hoy=date(2026, 8, 30)
        )
        self.assertFalse(desactualizado)

    def test_fichero_desactualizado_pasado_el_umbral(self):
        desactualizado = fichero_desactualizado(
            umbral_dias=5, divisa="USD", ruta_cache=self.ruta_cache, url=None, hoy=date(2026, 8, 31)
        )
        self.assertTrue(desactualizado)


def _xml_bce_grande(dias=150, hasta=None):
    """Construye un fixture XML grande y valido (USD y GBP, `dias` dias
    habiles seguidos terminando en `hasta` o antes), para probar el
    "camino feliz" de validar_fichero_bce. Devuelve (xml, fecha_mas_reciente):
    la fecha mas reciente puede ser anterior a `hasta` si `hasta` cae en
    fin de semana, asi que se devuelve calculada, no se asume."""
    hasta = hasta or date.today()
    filas = []
    fechas = []
    fecha = hasta
    while len(filas) < dias:
        if fecha.weekday() < 5:   # solo dias laborables, como el BCE real
            fechas.append(fecha)
            filas.append(
                f'    <Cube time="{fecha.isoformat()}">\n'
                f'      <Cube currency="USD" rate="1.1000"/>\n'
                f'      <Cube currency="GBP" rate="0.8500"/>\n'
                f'    </Cube>\n'
            )
        fecha -= timedelta(days=1)

    xml = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<gesmes:Envelope xmlns:gesmes="http://www.gesmes.org/xml/2002-08-01"\n'
        '                  xmlns="http://www.ecb.int/vocabulary/2002-08-01/eurofxref">\n'
        '  <Cube>\n' + "".join(filas) + "  </Cube>\n"
        "</gesmes:Envelope>\n"
    )
    return xml, fechas[0]   # fechas[0] es la mas reciente: se recorre hacia atras


class TestValidarFicheroBCE(unittest.TestCase):
    def setUp(self):
        self.directorio = tempfile.mkdtemp()

    def _escribir(self, nombre, contenido):
        ruta = os.path.join(self.directorio, nombre)
        with open(ruta, "w", encoding="utf-8") as f:
            f.write(contenido)
        return ruta

    def test_fichero_valido_devuelve_la_fecha_mas_reciente(self):
        xml, fecha_esperada = _xml_bce_grande()
        ruta = self._escribir("valido.xml", xml)

        ultima_fecha = validar_fichero_bce(ruta)

        self.assertEqual(ultima_fecha, fecha_esperada)

    def test_fichero_con_pocas_fechas_falla_ruidosamente(self):
        # El fixture pequeno de esta clase (XML_FIXTURE) solo tiene un
        # punado de fechas: no puede ser un historico real del BCE.
        ruta = self._escribir("incompleto.xml", XML_FIXTURE)

        with self.assertRaises(ErrorTipoCambio):
            validar_fichero_bce(ruta)

    def test_falta_una_divisa_esperada_falla_ruidosamente(self):
        xml, _ = _xml_bce_grande()
        ruta = self._escribir("sin_gbp.xml", xml)

        with self.assertRaises(ErrorTipoCambio):
            validar_fichero_bce(ruta, divisas_esperadas=("USD", "GBP", "JPY"))

    def test_xml_mal_formado_falla_ruidosamente(self):
        ruta = self._escribir("roto.xml", "esto no es XML valido <<<")

        with self.assertRaises(ErrorTipoCambio):
            validar_fichero_bce(ruta)

    def test_dato_demasiado_antiguo_falla_ruidosamente(self):
        hace_40_dias = date.today() - timedelta(days=40)
        xml, _ = _xml_bce_grande(hasta=hace_40_dias)
        ruta = self._escribir("viejo.xml", xml)

        with self.assertRaises(ErrorTipoCambio):
            validar_fichero_bce(ruta, dias_maximos_sin_publicar=30)


if __name__ == "__main__":
    unittest.main()
