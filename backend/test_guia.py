import os
import unittest
from unittest.mock import MagicMock, patch

import auth
import guia_api
import telegram_api
import utily


class GuiaTests(unittest.TestCase):
    def test_toda_ruta_esta_documentada_u_oculta(self):
        documentadas = {(e['metodo'], e['ruta']) for e in guia_api.GUIA}
        faltan = [
            (m, r) for m, r, _e in guia_api.reglas(utily.app)
            if not guia_api.oculta(m, r) and (m, guia_api.plantilla_de_regla(r)) not in documentadas
        ]
        self.assertEqual(faltan, [], 'Documenta estas rutas en guia_api.GUIA o agrégalas a las ocultas')

    def test_la_guia_se_construye_y_no_trae_ocultas(self):
        guia = guia_api.construir_guia(utily.app)
        self.assertEqual(len(guia['endpoints']), len(guia_api.GUIA))
        for e in guia['endpoints']:
            self.assertFalse(e['ruta'].startswith(guia_api.PREFIJOS_OCULTOS), e['ruta'])
            self.assertTrue(e['resumen'])
            self.assertIn('admin', e['permisos'])

    def test_sin_duplicados(self):
        claves = [(e['metodo'], e['ruta']) for e in guia_api.GUIA]
        self.assertEqual(len(claves), len(set(claves)))

    def test_permisos_salen_de_auth(self):
        por_ruta = {(e['metodo'], e['ruta']): e for e in guia_api.construir_guia(utily.app)['endpoints']}
        self.assertEqual(por_ruta[('GET', '/api/datos-grafica')]['permisos'], ['admin', 'reclutamiento'])
        self.assertEqual(por_ruta[('GET', '/api/empleados/{registro_id}/incidencias')]['permisos'], ['admin'])
        self.assertEqual(por_ruta[('DELETE', '/api/eliminar-empleado/{registro_id}')]['permisos'], ['admin'])

    def test_rutas_sensibles_ocultas(self):
        for metodo, regla in [
            ('GET', '/api/access-users'), ('POST', '/api/auth/login'), ('GET', '/api/documentos-db/<int:registro_id>/<doc_key>'),
            ('POST', '/api/telegram/interno/sesion'), ('GET', '/admin/preview-encuestas'), ('POST', '/api/escanear-documento'),
            ('GET', '/obtener-empleado/<int:registro_id>'),
        ]:
            self.assertTrue(guia_api.oculta(metodo, regla), regla)

    def test_archivos_marcados(self):
        archivos = {e['ruta'] for e in guia_api.GUIA if e.get('archivo')}
        self.assertEqual(archivos, {'/api/exportar-empleados/{empresa}', '/api/exportar-reclutamiento'})


def conexion(cursor):
    conn = MagicMock()
    conn.__enter__.return_value = conn
    conn.cursor.return_value.__enter__.return_value = cursor
    return conn


class RutasAgenteTests(unittest.TestCase):
    def setUp(self):
        os.environ['AUTH_SECRET_KEY'] = 'clave-de-prueba'
        auth._clave_cache = None
        auth._esquema_listo = True
        self.client = utily.app.test_client()
        self.cabeceras = {'X-Bot-Secreto': 's3'}

    def test_guia_requiere_secreto(self):
        with patch.dict(os.environ, {'TELEGRAM_BOT_SECRETO_INTERNO': 's3'}):
            self.assertEqual(self.client.get('/api/telegram/interno/guia').status_code, 401)
            respuesta = self.client.get('/api/telegram/interno/guia', headers=self.cabeceras)
        self.assertEqual(respuesta.status_code, 200)
        self.assertTrue(respuesta.get_json()['endpoints'])

    def test_traza_solo_metricas(self):
        base = {'telegram_id': 5, 'modelo': 'gpt-4o-mini', 'tokens_entrada': 10, 'tokens_salida': 2,
                'rondas': 1, 'herramientas': ['buscar_empleado'], 'segundos': 1.5, 'resultado': 'ok'}
        self.assertIsNotNone(telegram_api._traza_valida(base))
        for malo in ({'herramientas': ['hola mundo']}, {'modelo': 'x y'}, {'resultado': 'texto libre'},
                     {'tokens_entrada': -1}, {'tokens_salida': 'mucho'}, {'herramientas': 'x'}):
            self.assertIsNone(telegram_api._traza_valida({**base, **malo}), malo)

    def test_guardar_traza(self):
        cursor = MagicMock()
        cursor.fetchone.side_effect = [(7,), (99,)]
        with patch.dict(os.environ, {'TELEGRAM_BOT_SECRETO_INTERNO': 's3'}), \
                patch.object(utily, 'get_db_connection', return_value=conexion(cursor)):
            respuesta = self.client.post('/api/telegram/interno/trazas', headers=self.cabeceras, json={
                'telegram_id': 5, 'modelo': 'gpt-4o-mini', 'tokens_entrada': 10, 'tokens_salida': 2,
                'rondas': 1, 'herramientas': [], 'segundos': 1, 'resultado': 'ok'})
        self.assertEqual((respuesta.status_code, respuesta.get_json()), (201, {'id': 99}))

    def test_calificar_valida_valor(self):
        with patch.dict(os.environ, {'TELEGRAM_BOT_SECRETO_INTERNO': 's3'}):
            respuesta = self.client.post('/api/telegram/interno/trazas/1/calificacion', headers=self.cabeceras,
                                         json={'telegram_id': 5, 'valor': 5})
        self.assertEqual(respuesta.status_code, 400)

    def test_presupuesto(self):
        cursor = MagicMock()
        cursor.fetchone.return_value = (1200, 300)
        with patch.dict(os.environ, {'TELEGRAM_BOT_SECRETO_INTERNO': 's3'}), \
                patch.object(utily, 'get_db_connection', return_value=conexion(cursor)):
            respuesta = self.client.get('/api/telegram/interno/presupuesto?telegram_id=5', headers=self.cabeceras)
        self.assertEqual(respuesta.get_json(), {'tokens_hoy': 1200, 'tokens_hoy_persona': 300})


if __name__ == '__main__':
    unittest.main()
