import os
import unittest
from unittest.mock import MagicMock, patch

from werkzeug.security import generate_password_hash

import auth
import utily

HASH = generate_password_hash('secreto123')


class FakeCursor:
    def __init__(self, filas):
        self.filas = filas  # usuario_id -> fila (id, usuario, password_hash, permisos)
        self.vinculados = set()
        self.ultima = None

    def execute(self, sql, params=None):
        self.ultima = None
        if 'FROM usuarios_acceso' in sql and params:
            fila = self.filas.get(params[0])
            if 'telegram_vinculos' in sql and params[0] not in self.vinculados:
                fila = None
            self.ultima = fila

    def fetchone(self):
        return self.ultima

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False


class FakeConnection:
    def __init__(self, cursor):
        self._cursor = cursor

    def cursor(self):
        return self._cursor

    def commit(self):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False


class AuthTests(unittest.TestCase):
    def setUp(self):
        os.environ['AUTH_SECRET_KEY'] = 'clave-de-prueba'
        os.environ.pop('AUTH_ESTRICTO', None)
        auth._clave_cache = None
        auth._esquema_listo = True  # el esquema no se crea contra la BD falsa
        self.cursor = FakeCursor({
            1: (1, 'ana', HASH, ['admin']),
            2: (2, 'rita', HASH, ['reclutamiento']),
        })
        self.patcher = patch.object(utily, 'get_db_connection', return_value=FakeConnection(self.cursor))
        self.patcher.start()
        self.client = utily.app.test_client()

    def tearDown(self):
        self.patcher.stop()
        os.environ.pop('AUTH_ESTRICTO', None)

    def bearer(self, usuario_id, canal=auth.CANAL_WEB, password_hash=HASH):
        return {'Authorization': 'Bearer ' + auth.emitir_token(usuario_id, password_hash, canal)}

    # --- clasificación de rutas

    def test_todas_las_rutas_tienen_permiso_explicito(self):
        sin_clasificar = sorted({
            regla.endpoint for regla in utily.app.url_map.iter_rules()
            if regla.endpoint not in auth.PERMISOS_POR_ENDPOINT
        })
        self.assertEqual(sin_clasificar, [], 'Agrega estas rutas a auth.PERMISOS_POR_ENDPOINT')

    def test_ruta_desconocida_exige_admin(self):
        self.assertEqual(auth.permiso_requerido('ruta_nueva', 'GET'), auth.ADMIN)
        self.assertEqual(auth.permiso_requerido('manejar_reclutadores', 'DELETE'), auth.ADMIN)

    # --- token

    def test_token_ida_y_vuelta(self):
        payload = auth.leer_token(auth.emitir_token(1, HASH))
        self.assertEqual(payload['u'], 1)
        self.assertEqual(payload['c'], auth.CANAL_WEB)

    def test_token_alterado_o_vacio_es_invalido(self):
        token = auth.emitir_token(1, HASH)
        self.assertIsNone(auth.leer_token(token[:-2] + 'xx'))
        self.assertIsNone(auth.leer_token(''))

    def test_token_expira_segun_canal(self):
        token = auth.emitir_token(1, HASH, auth.CANAL_TELEGRAM)
        with patch.dict(auth.MINUTOS_POR_CANAL, {auth.CANAL_TELEGRAM: -1}):
            self.assertIsNone(auth.leer_token(token))

    # --- before_request

    def test_me_con_token_devuelve_usuario(self):
        respuesta = self.client.get('/api/auth/me', headers=self.bearer(1))
        self.assertEqual(respuesta.status_code, 200)
        self.assertEqual(respuesta.get_json()['usuario'], 'ana')

    def test_cookie_tambien_autentica(self):
        self.client.set_cookie(auth.COOKIE_SESION, auth.emitir_token(1, HASH))
        self.assertEqual(self.client.get('/api/auth/me').status_code, 200)

    def test_cambiar_password_invalida_sesiones(self):
        respuesta = self.client.get('/api/auth/me', headers=self.bearer(1, password_hash='hash-anterior'))
        self.assertEqual(respuesta.status_code, 401)

    def test_permiso_insuficiente_da_403(self):
        respuesta = self.client.get('/api/access-users', headers=self.bearer(2))
        self.assertEqual(respuesta.status_code, 403)

    def test_bearer_invalido_es_401_aun_en_modo_gradual(self):
        respuesta = self.client.get('/api/access-users', headers={'Authorization': 'Bearer basura'})
        self.assertEqual(respuesta.status_code, 401)

    def test_modo_estricto_sin_sesion_da_401(self):
        os.environ['AUTH_ESTRICTO'] = '1'
        self.assertEqual(self.client.get('/api/access-users').status_code, 401)

    def test_modo_gradual_sin_sesion_deja_pasar(self):
        self.assertNotEqual(self.client.get('/api/access-users').status_code, 401)

    def test_rutas_publicas_no_piden_sesion_en_modo_estricto(self):
        os.environ['AUTH_ESTRICTO'] = '1'
        self.assertEqual(self.client.get('/api/health').status_code, 200)
        self.assertNotEqual(self.client.post('/api/guardar-registro', data={}).status_code, 401)

    def test_telegram_sin_vinculo_es_401(self):
        respuesta = self.client.get('/api/auth/me', headers=self.bearer(1, auth.CANAL_TELEGRAM))
        self.assertEqual(respuesta.status_code, 401)

    def test_telegram_con_vinculo_entra(self):
        self.cursor.vinculados.add(1)
        respuesta = self.client.get('/api/auth/me', headers=self.bearer(1, auth.CANAL_TELEGRAM))
        self.assertEqual(respuesta.status_code, 200)
        self.assertEqual(respuesta.get_json()['canal'], auth.CANAL_TELEGRAM)


class LoginTests(unittest.TestCase):
    def setUp(self):
        os.environ['AUTH_SECRET_KEY'] = 'clave-de-prueba'
        auth._clave_cache = None
        auth._esquema_listo = True

    def test_login_devuelve_token_y_cookie(self):
        cursor = MagicMock()
        cursor.__enter__.return_value = cursor
        cursor.fetchone.return_value = (1, 'ana', HASH, ['admin'], '', '')
        conn = FakeConnection(cursor)
        with patch.object(utily, 'get_db_connection', return_value=conn):
            respuesta = utily.app.test_client().post(
                '/api/auth/login', json={'usuario': 'ana', 'password': 'secreto123'}
            )
        self.assertEqual(respuesta.status_code, 200)
        cuerpo = respuesta.get_json()
        self.assertEqual(auth.leer_token(cuerpo['token'])['u'], 1)
        self.assertNotIn('password_hash', cuerpo)
        cookie = respuesta.headers.get('Set-Cookie', '')
        self.assertIn(auth.COOKIE_SESION, cookie)
        self.assertIn('HttpOnly', cookie)
        self.assertIn('SameSite=Strict', cookie)

    def test_login_con_password_incorrecta(self):
        cursor = MagicMock()
        cursor.__enter__.return_value = cursor
        cursor.fetchone.return_value = (1, 'ana', HASH, ['admin'], '', '')
        with patch.object(utily, 'get_db_connection', return_value=FakeConnection(cursor)):
            respuesta = utily.app.test_client().post(
                '/api/auth/login', json={'usuario': 'ana', 'password': 'otra'}
            )
        self.assertEqual(respuesta.status_code, 401)

    def test_admin_inicial_solo_si_no_hay_cuentas(self):
        cursor = MagicMock()
        cursor.__enter__.return_value = cursor
        with patch.dict(os.environ, {'ADMIN_INICIAL_USUARIO': 'root', 'ADMIN_INICIAL_PASSWORD': 'larga-123'}):
            cursor.fetchone.return_value = (True,)
            utily.sembrar_admin_inicial(cursor)
            self.assertEqual(cursor.execute.call_count, 1)
            cursor.fetchone.return_value = (False,)
            utily.sembrar_admin_inicial(cursor)
            self.assertIn('INSERT INTO usuarios_acceso', cursor.execute.call_args[0][0])


if __name__ == '__main__':
    unittest.main()
