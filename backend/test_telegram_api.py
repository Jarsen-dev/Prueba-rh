import os
import unittest
from unittest.mock import MagicMock, patch

import auth
import telegram_api
import utily


def conexion_con(cursor):
    cursor.__enter__.return_value = cursor
    conn = MagicMock()
    conn.cursor.return_value = cursor
    conn.__enter__.return_value = conn
    return conn


class CodigoTests(unittest.TestCase):
    def test_codigo_generado_es_valido_y_sin_ambiguos(self):
        for _ in range(50):
            codigo = telegram_api.generar_codigo()
            self.assertEqual(telegram_api.normalizar_codigo(codigo), codigo)
            self.assertFalse(set(codigo) & set('01OI'))

    def test_normalizar_acepta_minusculas_espacios_y_guion(self):
        self.assertEqual(telegram_api.normalizar_codigo(' abcd-efgh '), 'ABCDEFGH')
        self.assertEqual(telegram_api.normalizar_codigo('ABCD EFGH'), 'ABCDEFGH')

    def test_normalizar_rechaza_formatos_invalidos(self):
        for valor in ('', None, 'ABC', 'ABCDEFGHJ', 'ABCD-EFG0', 'emp_123'):
            self.assertIsNone(telegram_api.normalizar_codigo(valor))

    def test_deep_link_cabe_en_payload_de_start(self):
        with patch.dict(os.environ, {'TELEGRAM_BOT_USERNAME': '@RhBot'}):
            enlace = telegram_api.enlace_start('ABCDEFGH')
        self.assertEqual(enlace, 'https://t.me/RhBot?start=ABCDEFGH')

    def test_telegram_id_valido(self):
        self.assertEqual(telegram_api._telegram_id({'telegram_id': '9876543210'}), 9876543210)
        for valor in (None, 'abc', -5, 0, True):
            self.assertIsNone(telegram_api._telegram_id({'telegram_id': valor}))


class RutasInternasTests(unittest.TestCase):
    def setUp(self):
        os.environ['AUTH_SECRET_KEY'] = 'clave-de-prueba'
        auth._clave_cache = None
        auth._esquema_listo = True
        self.client = utily.app.test_client()

    def test_sin_secreto_configurado_responde_503(self):
        with patch.dict(os.environ, {'TELEGRAM_BOT_SECRETO_INTERNO': ''}):
            respuesta = self.client.post('/api/telegram/interno/sesion', json={'telegram_id': 1})
        self.assertEqual(respuesta.status_code, 503)

    def test_secreto_incorrecto_responde_401(self):
        with patch.dict(os.environ, {'TELEGRAM_BOT_SECRETO_INTERNO': 'correcto'}):
            for cabeceras in ({}, {'X-Bot-Secreto': 'otro'}):
                respuesta = self.client.post('/api/telegram/interno/sesion', json={'telegram_id': 1}, headers=cabeceras)
                self.assertEqual(respuesta.status_code, 401)

    def test_sesion_de_telegram_no_vinculado_es_404(self):
        cursor = MagicMock()
        cursor.fetchone.return_value = None
        with patch.dict(os.environ, {'TELEGRAM_BOT_SECRETO_INTERNO': 's3'}), \
                patch.object(utily, 'get_db_connection', return_value=conexion_con(cursor)):
            respuesta = self.client.post(
                '/api/telegram/interno/sesion', json={'telegram_id': 55}, headers={'X-Bot-Secreto': 's3'}
            )
        self.assertEqual(respuesta.status_code, 404)
        self.assertEqual(respuesta.get_json()['codigo'], 'NO_VINCULADO')

    def test_sesion_emite_token_de_canal_telegram(self):
        cursor = MagicMock()
        cursor.fetchone.side_effect = [(7,), (7, 'ana', 'hash', ['admin'])]
        with patch.dict(os.environ, {'TELEGRAM_BOT_SECRETO_INTERNO': 's3'}), \
                patch.object(utily, 'get_db_connection', return_value=conexion_con(cursor)):
            respuesta = self.client.post(
                '/api/telegram/interno/sesion', json={'telegram_id': 55}, headers={'X-Bot-Secreto': 's3'}
            )
        self.assertEqual(respuesta.status_code, 200)
        cuerpo = respuesta.get_json()
        self.assertEqual(cuerpo['usuario']['permisos'], ['admin'])
        payload = auth.leer_token(cuerpo['token'])
        self.assertEqual((payload['u'], payload['c']), (7, auth.CANAL_TELEGRAM))

    def test_vincular_con_codigo_usado_o_expirado(self):
        cursor = MagicMock()
        cursor.fetchone.return_value = None
        with patch.dict(os.environ, {'TELEGRAM_BOT_SECRETO_INTERNO': 's3'}), \
                patch.object(utily, 'get_db_connection', return_value=conexion_con(cursor)):
            respuesta = self.client.post(
                '/api/telegram/interno/vincular',
                json={'codigo': 'ABCD-EFGH', 'telegram_id': 55},
                headers={'X-Bot-Secreto': 's3'},
            )
        self.assertEqual(respuesta.status_code, 400)
        sql = cursor.execute.call_args_list[0][0][0]
        self.assertIn('usado_en IS NULL', sql)
        self.assertIn('expira_en > now()', sql)

    def test_vincular_rechaza_telegram_de_otra_cuenta(self):
        cursor = MagicMock()
        cursor.fetchone.side_effect = [(7,), (9,)]
        with patch.dict(os.environ, {'TELEGRAM_BOT_SECRETO_INTERNO': 's3'}), \
                patch.object(utily, 'get_db_connection', return_value=conexion_con(cursor)):
            respuesta = self.client.post(
                '/api/telegram/interno/vincular',
                json={'codigo': 'ABCDEFGH', 'telegram_id': 55},
                headers={'X-Bot-Secreto': 's3'},
            )
        self.assertEqual(respuesta.status_code, 409)

    def test_generar_codigo_requiere_sesion_web(self):
        token = auth.emitir_token(7, 'hash', auth.CANAL_TELEGRAM)
        cursor = MagicMock()
        cursor.fetchone.return_value = (7, 'ana', 'hash', ['admin'])
        with patch.object(utily, 'get_db_connection', return_value=conexion_con(cursor)):
            respuesta = self.client.post('/api/telegram/codigo', headers={'Authorization': f'Bearer {token}'})
        self.assertEqual(respuesta.status_code, 403)


if __name__ == '__main__':
    unittest.main()
