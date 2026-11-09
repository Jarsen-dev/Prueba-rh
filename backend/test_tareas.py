import os
import unittest
from datetime import datetime
from unittest.mock import MagicMock, patch

import auth
import tareas
import utily

Z = tareas.ZONA


def conexion(cursor):
    cursor.__enter__.return_value = cursor
    conn = MagicMock()
    conn.cursor.return_value = cursor
    conn.__enter__.return_value = conn
    return conn


class ReglasTests(unittest.TestCase):
    def test_validar(self):
        t = tareas.validar({'instruccion': '  ingresos   de la semana ', 'frecuencia': 'semanal', 'hora': 9,
                            'dias_semana': [4, 0, 0]})
        self.assertEqual((t['instruccion'], t['dias_semana'], t['minuto']), ('ingresos de la semana', [0, 4], 0))
        for malo in ({'instruccion': '', 'frecuencia': 'diaria', 'hora': 9},
                     {'instruccion': 'x', 'frecuencia': 'cada rato', 'hora': 9},
                     {'instruccion': 'x', 'frecuencia': 'diaria', 'hora': 24},
                     {'instruccion': 'x', 'frecuencia': 'diaria', 'hora': '9'},
                     {'instruccion': 'x', 'frecuencia': 'semanal', 'hora': 9, 'dias_semana': []},
                     {'instruccion': 'x', 'frecuencia': 'mensual', 'hora': 9, 'dia_mes': 31},
                     {'instruccion': 'x' * 301, 'frecuencia': 'diaria', 'hora': 9}):
            with self.assertRaises(tareas.TareaInvalida, msg=malo):
                tareas.validar(malo)

    def test_proxima(self):
        lunes_10 = datetime(2026, 11, 9, 10, 0, tzinfo=Z)
        diaria = tareas.validar({'instruccion': 'x', 'frecuencia': 'diaria', 'hora': 9})
        self.assertEqual(tareas.proxima(diaria, lunes_10), datetime(2026, 11, 10, 9, 0, tzinfo=Z))
        diaria_tarde = tareas.validar({'instruccion': 'x', 'frecuencia': 'diaria', 'hora': 18, 'minuto': 30})
        self.assertEqual(tareas.proxima(diaria_tarde, lunes_10), datetime(2026, 11, 9, 18, 30, tzinfo=Z))
        semanal = tareas.validar({'instruccion': 'x', 'frecuencia': 'semanal', 'hora': 9, 'dias_semana': [0, 4]})
        self.assertEqual(tareas.proxima(semanal, lunes_10), datetime(2026, 11, 13, 9, 0, tzinfo=Z))
        mensual = tareas.validar({'instruccion': 'x', 'frecuencia': 'mensual', 'hora': 8, 'dia_mes': 1})
        self.assertEqual(tareas.proxima(mensual, lunes_10), datetime(2026, 12, 1, 8, 0, tzinfo=Z))

    def test_descripcion(self):
        semanal = tareas.validar({'instruccion': 'x', 'frecuencia': 'semanal', 'hora': 9, 'dias_semana': [0, 2, 4]})
        self.assertEqual(tareas.descripcion(semanal), 'cada lunes, miércoles y viernes a las 09:00')


class RutasTests(unittest.TestCase):
    def setUp(self):
        os.environ['AUTH_SECRET_KEY'] = 'clave-de-prueba'
        auth._clave_cache = None
        auth._esquema_listo = True
        self.client = utily.app.test_client()

    def _con_sesion(self, cur):
        usuario = {'id': 3, 'usuario': 'x', 'permisos': ['notificaciones'], 'canal': 'telegram'}
        return (patch.object(utily, 'get_db_connection', return_value=conexion(cur)),
                patch.object(auth, 'leer_token', return_value={'u': 3}),
                patch.object(auth, 'cargar_usuario', return_value=usuario))

    def test_crear_con_limite_y_validacion(self):
        cur = MagicMock()
        cur.fetchone.side_effect = [(0,), (7, 'x', 'diaria', '', None, 9, 0, datetime(2026, 11, 10, 9, tzinfo=Z))]
        a, b, c = self._con_sesion(cur)
        with a, b, c:
            r = self.client.post('/api/tareas', json={'instruccion': 'x', 'frecuencia': 'diaria', 'hora': 9},
                                 headers={'Authorization': 'Bearer t'})
            self.assertEqual(r.status_code, 201)
            self.assertEqual(r.get_json()['descripcion'], 'todos los días a las 09:00')
            r = self.client.post('/api/tareas', json={'instruccion': 'x', 'frecuencia': 'diaria', 'hora': 99},
                                 headers={'Authorization': 'Bearer t'})
            self.assertEqual(r.status_code, 400)
        cur = MagicMock()
        cur.fetchone.side_effect = [(tareas.MAX_TAREAS,)]
        a, b, c = self._con_sesion(cur)
        with a, b, c:
            r = self.client.post('/api/tareas', json={'instruccion': 'x', 'frecuencia': 'diaria', 'hora': 9},
                                 headers={'Authorization': 'Bearer t'})
        self.assertEqual(r.status_code, 409)

    def test_borrar_solo_las_propias(self):
        cur = MagicMock()
        cur.fetchone.return_value = None
        a, b, c = self._con_sesion(cur)
        with a, b, c:
            r = self.client.delete('/api/tareas/5', headers={'Authorization': 'Bearer t'})
        self.assertEqual(r.status_code, 404)
        sql, valores = [ca.args for ca in cur.execute.call_args_list if ca.args[0].startswith('DELETE')][0]
        self.assertIn('usuario_id = %s', sql)
        self.assertEqual(valores, (5, 3))

    def test_sin_sesion_401(self):
        with patch.dict(os.environ, {'AUTH_ESTRICTO': '0'}):
            self.assertEqual(self.client.get('/api/tareas').status_code, 401)

    def test_turno_reprograma_antes_de_devolver(self):
        cur = MagicMock()
        cur.fetchall.return_value = [(4, 'ingresos', 'diaria', '', None, 9, 0, datetime(2026, 11, 9, 9, tzinfo=Z), 777)]
        with patch.dict(os.environ, {'TELEGRAM_BOT_SECRETO_INTERNO': 's'}), \
             patch.object(utily, 'get_db_connection', return_value=conexion(cur)):
            r = self.client.post('/api/telegram/interno/tareas/turno', headers={'X-Bot-Secreto': 's'})
        self.assertEqual(r.get_json()['tareas'], [{'id': 4, 'telegram_id': 777, 'instruccion': 'ingresos',
                                                   'descripcion': 'todos los días a las 09:00'}])
        update = [ca.args for ca in cur.execute.call_args_list if ca.args[0].startswith('UPDATE bot_tareas')][0]
        self.assertGreater(update[1][0], datetime.now(Z))


if __name__ == '__main__':
    unittest.main()
