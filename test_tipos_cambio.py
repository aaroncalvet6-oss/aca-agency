"""Tests de tipos_cambio.py.

No usan la cache real ni tocan la red: se construye un XML local con el
mismo formato del BCE (fixture) y se le dice a obtener_tipo_cambio que
lea de ahi con el parametro ruta_cache.
"""

import os
import tempfile
import unittest
from datetime import date

from tipos_cambio import obtener_tipo_cambio

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


if __name__ == "__main__":
    unittest.main()
