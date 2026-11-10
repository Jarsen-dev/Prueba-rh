import json
import re
import unittest
from pathlib import Path

import catalogos

INGRESO_HTML = Path(__file__).resolve().parent.parent / 'frontend' / 'Ingreso.html'


def opciones_del_select(html, clase):
    bloque = re.search(rf'<select class="{clase}"[^>]*>(.*?)</select>', html, re.S).group(1)
    return [valor for valor in re.findall(r'<option value="([^"]*)"', bloque) if valor]


class CatalogosCoincidenConElPanelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.html = INGRESO_HTML.read_text(encoding='utf-8')

    def test_selects_simples(self):
        self.assertEqual(opciones_del_select(self.html, 'campo-empresa'), catalogos.EMPRESAS)
        self.assertEqual(opciones_del_select(self.html, 'campo-area'), list(catalogos.PUESTOS_POR_AREA))
        self.assertEqual(opciones_del_select(self.html, 'campo-turno'), catalogos.TURNOS)
        self.assertEqual(opciones_del_select(self.html, 'campo-pago'), catalogos.PAGOS)

    def test_puestos_por_area(self):
        objeto = re.search(r'const puestosPorArea = (\{.*?\n\s*\});', self.html, re.S).group(1)
        como_json = re.sub(r',(\s*[\]}])', r'\1', objeto.replace("'", '"'))
        self.assertEqual(json.loads(como_json), catalogos.PUESTOS_POR_AREA)

    def test_personal(self):
        self.assertIn(f"personal: '{catalogos.PERSONAL}'", self.html)


if __name__ == '__main__':
    unittest.main()
