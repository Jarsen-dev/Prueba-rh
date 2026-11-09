import os
import unittest
from unittest.mock import MagicMock, patch

import auth
import memoria
import utily


def conexion(cursor):
    cursor.__enter__.return_value = cursor
    conn = MagicMock()
    conn.cursor.return_value = cursor
    conn.__enter__.return_value = conn
    return conn


class MemoriaTests(unittest.TestCase):
    def setUp(self):
        os.environ['AUTH_SECRET_KEY'] = 'clave-de-prueba'
        auth._clave_cache = None
        auth._esquema_listo = True
        self.client = utily.app.test_client()

    def pedir(self, cur, metodo, ruta, **kw):
        usuario = {'id': 3, 'usuario': 'x', 'permisos': ['reclutamiento'], 'canal': 'telegram'}
        with patch.object(utily, 'get_db_connection', return_value=conexion(cur)), \
             patch.object(auth, 'leer_token', return_value={'u': 3}), \
             patch.object(auth, 'cargar_usuario', return_value=usuario):
            return getattr(self.client, metodo)(ruta, headers={'Authorization': 'Bearer t'}, **kw)

    def test_guardar_normaliza_y_limita(self):
        cur = MagicMock()
        cur.fetchone.side_effect = [(0,), (8,)]
        r = self.pedir(cur, 'post', '/api/memoria', json={'texto': '  la planta   = Kronos '})
        self.assertEqual((r.status_code, r.get_json()), (201, {'id': 8, 'texto': 'la planta = Kronos'}))
        self.assertEqual(self.pedir(MagicMock(), 'post', '/api/memoria', json={'texto': 'x' * 201}).status_code, 400)
        cur = MagicMock()
        cur.fetchone.side_effect = [(memoria.MAX_NOTAS,)]
        self.assertEqual(self.pedir(cur, 'post', '/api/memoria', json={'texto': 'x'}).status_code, 409)

    def test_listar_y_borrar_solo_las_propias(self):
        cur = MagicMock()
        cur.fetchall.return_value = [(1, 'a')]
        self.assertEqual(self.pedir(cur, 'get', '/api/memoria').get_json()['notas'], [{'id': 1, 'texto': 'a'}])
        cur = MagicMock()
        cur.fetchone.return_value = None
        self.assertEqual(self.pedir(cur, 'delete', '/api/memoria/5').status_code, 404)
        sql, valores = [c.args for c in cur.execute.call_args_list if c.args[0].startswith('DELETE')][0]
        self.assertEqual(valores, (5, 3))


if __name__ == '__main__':
    unittest.main()
