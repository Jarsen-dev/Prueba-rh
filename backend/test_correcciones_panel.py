"""Correcciones del panel: gráficas del día 1 y teléfonos de reclutadores."""
import os
import unittest
from unittest.mock import MagicMock, patch

import auth
import utily


def conexion(cursor):
    cursor.__enter__.return_value = cursor
    conn = MagicMock()
    conn.cursor.return_value = cursor
    conn.__enter__.return_value = conn
    return conn


class FechaLocalTests(unittest.TestCase):
    def test_date_no_se_convierte_de_zona(self):
        cur = MagicMock()
        cur.fetchone.return_value = ('date',)
        self.assertEqual(utily.expresion_fecha_local(cur, 't', 'fecha_registro', 'America/Mexico_City'),
                         ('fecha_registro', ()))

    def test_timestamptz_si_se_convierte(self):
        cur = MagicMock()
        cur.fetchone.return_value = ('timestamp with time zone',)
        self.assertEqual(utily.expresion_fecha_local(cur, 't', 'fecha_registro', 'America/Mexico_City'),
                         ('timezone(%s, fecha_registro)', ('America/Mexico_City',)))

    def test_grafica_12_meses_usa_la_fecha_tal_cual(self):
        auth._esquema_listo = True
        cur = MagicMock()
        cur.fetchone.return_value = ('date',)
        cur.fetchall.return_value = [(11, 5)]
        with patch.object(utily, 'get_db_connection', return_value=conexion(cur)):
            r = utily.app.test_client().get('/api/datos-grafica?periodo=12meses')
        self.assertEqual(r.get_json()[10], 5)
        sql, params = cur.execute.call_args_list[-1].args
        self.assertNotIn('timezone(%s, fecha_registro)', sql)
        self.assertEqual(params, ('America/Mexico_City',))


class TelefonosReclutadoresTests(unittest.TestCase):
    def setUp(self):
        os.environ['AUTH_SECRET_KEY'] = 'clave-de-prueba'
        auth._clave_cache = None
        auth._esquema_listo = True

    def pedir(self, usuario):
        cur = MagicMock()
        cur.fetchall.return_value = [(1, 'Laura', 1, '5511112222')]
        cabeceras = {'Authorization': 'Bearer t'} if usuario else {}
        with patch.object(utily, 'get_db_connection', return_value=conexion(cur)), \
             patch.object(auth, 'leer_token', return_value={'u': 1} if usuario else None), \
             patch.object(auth, 'cargar_usuario', return_value=usuario):
            return utily.app.test_client().get('/api/reclutadores-nombres', headers=cabeceras).get_json()

    def test_publico_sin_telefono(self):
        self.assertEqual(self.pedir(None), [{'id': 1, 'nombre': 'Laura', 'team': 1, 'activo': True}])

    def test_reclutamiento_con_telefono(self):
        usuario = {'id': 1, 'usuario': 'r', 'permisos': ['reclutamiento'], 'canal': 'web'}
        self.assertEqual(self.pedir(usuario)[0]['telefono'], '5511112222')

    def test_otro_permiso_sin_telefono(self):
        usuario = {'id': 1, 'usuario': 'n', 'permisos': ['notificaciones'], 'canal': 'web'}
        self.assertNotIn('telefono', self.pedir(usuario)[0])


if __name__ == '__main__':
    unittest.main()
