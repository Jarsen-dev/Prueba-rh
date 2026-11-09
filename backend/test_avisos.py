import os
import unittest
from datetime import date
from unittest.mock import MagicMock, patch

import auth
import avisos
import utily


class ReglasTests(unittest.TestCase):
    def test_fin_de_contrato_como_contrato_html(self):
        # Lunes 2 de noviembre de 2026 + 67 días hábiles (sin sábados ni domingos).
        self.assertEqual(avisos.fin_de_contrato(date(2026, 11, 2)), date(2027, 2, 3))
        # Empezar en viernes o sábado: el sábado y domingo no cuentan.
        self.assertEqual(avisos.sumar_dias_habiles(date(2026, 11, 6), 1), date(2026, 11, 9))
        self.assertEqual(avisos.sumar_dias_habiles(date(2026, 11, 7), 1), date(2026, 11, 9))

    def test_contratos_dentro_de_la_ventana(self):
        hoy = date(2026, 11, 9)
        ingreso_que_vence_hoy = next(d for d in (date(2026, 8, n) for n in range(1, 32))
                                     if avisos.fin_de_contrato(d) == hoy)
        ingreso_en_8_dias = next(date.fromordinal(o) for o in range(date(2026, 8, 1).toordinal(), hoy.toordinal())
                                 if avisos.fin_de_contrato(date.fromordinal(o)) > date(2026, 11, 16))
        filas = [(1, 'Hoy', '1', 'KRONOS', ingreso_que_vence_hoy), (2, 'Sin fecha', '2', 'KRONOS', None),
                 (3, 'Lejos', '3', 'KRONOS', ingreso_en_8_dias)]
        salida = avisos.contratos_por_vencer(filas, hoy, 7)
        self.assertEqual([(c['registro_id'], c['dias_restantes']) for c in salida], [(1, 0)])


def conexion(cursor):
    cursor.__enter__.return_value = cursor
    conn = MagicMock()
    conn.cursor.return_value = cursor
    conn.__enter__.return_value = conn
    return conn


class RutaTests(unittest.TestCase):
    def setUp(self):
        os.environ['AUTH_SECRET_KEY'] = 'clave-de-prueba'
        auth._clave_cache = None
        auth._esquema_listo = True
        self.client = utily.app.test_client()

    def _pedir(self, permisos):
        cur = MagicMock()
        cur.fetchone.side_effect = [(date(2026, 11, 9),), (4,)]
        cur.fetchall.side_effect = [[('fecha_registro',), ('estatus',)], [], [], [],
                                    [(9, 'Iván', '41', date(2026, 10, 1))]]
        usuario = {'id': 1, 'usuario': 'x', 'permisos': permisos, 'canal': 'web'}
        with patch.object(utily, 'get_db_connection', return_value=conexion(cur)), \
             patch.object(auth, 'leer_token', return_value={'u': 1}), \
             patch.object(auth, 'cargar_usuario', return_value=usuario):
            return self.client.get('/api/avisos', headers={'Authorization': 'Bearer t'})

    def test_admin_ve_todas_las_secciones_con_nombres(self):
        datos = self._pedir(['admin']).get_json()
        self.assertEqual(set(datos), {'fecha', 'contratos', 'contratos_dias', 'reingresos', 'inicios', 'candidatos'})
        self.assertEqual(datos['candidatos']['lista'][0]['dias_esperando'], 39)
        self.assertEqual(datos['candidatos']['registrados_ayer'], 4)

    def test_reclutamiento_solo_ve_conteos(self):
        cur = MagicMock()
        cur.fetchone.side_effect = [(date(2026, 11, 9),), (4,)]
        cur.fetchall.side_effect = [[('fecha_registro',)], [(9, 'Iván', '41', date(2026, 10, 1))]]
        usuario = {'id': 1, 'usuario': 'x', 'permisos': ['reclutamiento'], 'canal': 'web'}
        with patch.object(utily, 'get_db_connection', return_value=conexion(cur)), \
             patch.object(auth, 'leer_token', return_value={'u': 1}), \
             patch.object(auth, 'cargar_usuario', return_value=usuario):
            datos = self.client.get('/api/avisos', headers={'Authorization': 'Bearer t'}).get_json()
        self.assertEqual(set(datos), {'fecha', 'candidatos'})
        self.assertNotIn('lista', datos['candidatos'])
        self.assertEqual(datos['candidatos']['sin_asignar'], 1)

    def test_notificaciones_no_entra(self):
        with patch.object(auth, 'leer_token', return_value={'u': 1}), \
             patch.object(auth, 'cargar_usuario', return_value={'id': 1, 'usuario': 'x', 'permisos': ['notificaciones'],
                                                                 'canal': 'web'}), \
             patch.object(utily, 'get_db_connection', return_value=conexion(MagicMock())):
            self.assertEqual(self.client.get('/api/avisos', headers={'Authorization': 'Bearer t'}).status_code, 403)


class PreferenciasTests(unittest.TestCase):
    def setUp(self):
        auth._esquema_listo = True
        self.client = utily.app.test_client()
        self.cabeceras = {'X-Bot-Secreto': 's'}

    def test_valida_hora_y_activos(self):
        with patch.dict(os.environ, {'TELEGRAM_BOT_SECRETO_INTERNO': 's'}):
            for cuerpo in ({'telegram_id': 5, 'hora': 3}, {'telegram_id': 5, 'hora': '8'},
                           {'telegram_id': 5, 'activos': 'si'}, {'hora': 8}):
                r = self.client.post('/api/telegram/interno/avisos/preferencias', json=cuerpo, headers=self.cabeceras)
                self.assertEqual(r.status_code, 400, cuerpo)

    def test_guarda_y_devuelve(self):
        cur = MagicMock()
        cur.fetchone.return_value = (False, 9)
        with patch.dict(os.environ, {'TELEGRAM_BOT_SECRETO_INTERNO': 's'}), \
             patch.object(utily, 'get_db_connection', return_value=conexion(cur)):
            r = self.client.post('/api/telegram/interno/avisos/preferencias',
                                 json={'telegram_id': 5, 'activos': False, 'hora': 9}, headers=self.cabeceras)
        self.assertEqual(r.get_json(), {'activos': False, 'hora': 9})
        sql, valores = cur.execute.call_args_list[0].args
        self.assertIn('avisos_activos = %s, avisos_hora = %s', sql)
        self.assertEqual(valores, (False, 9, 5))

    def test_turno_exige_secreto(self):
        with patch.dict(os.environ, {'TELEGRAM_BOT_SECRETO_INTERNO': 's'}):
            self.assertEqual(self.client.post('/api/telegram/interno/avisos/turno').status_code, 401)


if __name__ == '__main__':
    unittest.main()
