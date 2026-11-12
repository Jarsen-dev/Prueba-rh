import os
import unittest
from unittest import mock
from unittest.mock import patch

import utily


class AccessUserPermissionsTests(unittest.TestCase):
    def test_legacy_system_authentication_endpoint_is_removed(self):
        response = utily.app.test_client().post(
            '/api/auth/system',
            json={'usuario': 'admin', 'password': 'legacy-password'},
        )

        self.assertEqual(response.status_code, 404)

    def test_removed_attendance_permission_is_not_accepted(self):
        self.assertEqual(
            utily.normalizar_permisos(['recursos_humanos', 'control_asistencias', 'notificaciones']),
            ['admin', 'notificaciones'],
        )

    def test_legacy_permissions_are_mapped_to_admin_for_clients(self):
        usuario = utily.usuario_acceso_dict(
            (7, 'usuario', 'hash', ['recursos_humanos', 'control_asistencias'], 'Almacen', '5551234')
        )

        self.assertEqual(usuario['permisos'], ['admin'])
        self.assertNotIn('area_responsable', usuario)

    def test_admin_permission_is_accepted_without_duplicates(self):
        self.assertEqual(
            utily.normalizar_permisos(['admin', 'recursos_humanos', 'reclutamiento']),
            ['admin', 'reclutamiento'],
        )

    def test_admin_permission_is_case_insensitive(self):
        self.assertEqual(
            utily.normalizar_permisos([' Admin ', 'RECLUTAMIENTO']),
            ['admin', 'reclutamiento'],
        )

    def test_create_rejects_only_the_removed_permission(self):
        client = utily.app.test_client()
        response = client.post(
            '/api/access-users',
            json={
                'usuario': 'usuario',
                'password': 'password',
                'permisos': ['control_asistencias'],
                'area_responsable': 'Almacen',
            },
        )

        self.assertEqual(response.status_code, 401)
        self.assertIn('iniciar sesión', response.get_json()['error'])

    def test_account_routes_require_authenticated_admin(self):
        client = utily.app.test_client()
        with patch.dict('os.environ', {'AUTH_ESTRICTO': '1'}):
            requests = [
                client.get('/api/access-users'),
                client.post('/api/access-users', json={}),
                client.post('/api/access-users/import', json={'usuarios': []}),
                client.put('/api/access-users/1', json={}),
                client.delete('/api/access-users/1'),
            ]

        self.assertEqual([response.status_code for response in requests], [401] * 5)

    def test_account_routes_reject_non_admin_users(self):
        with utily.app.test_request_context('/api/access-users'):
            utily.g.usuario = {
                'id': 8,
                'usuario': 'reclutador',
                'permisos': ['reclutamiento'],
            }
            response, status = utily.listar_usuarios_acceso()

        self.assertEqual(status, 403)
        self.assertIn('administrador', response.get_json()['error'])

    def test_session_endpoint_requires_authentication(self):
        response = utily.app.test_client().get('/api/auth/me')

        self.assertEqual(response.status_code, 401)

    def test_cross_host_login_is_rejected(self):
        response = utily.app.test_client().post(
            '/api/auth/login',
            json={'usuario': 'usuario', 'password': 'password'},
            headers={'Origin': 'https://otro-sitio.example'},
        )

        self.assertEqual(response.status_code, 403)

    def test_cross_port_login_is_rejected(self):
        response = utily.app.test_client().post(
            '/api/auth/login',
            base_url='http://localhost:8001',
            json={'usuario': 'usuario', 'password': 'password'},
            headers={'Origin': 'http://localhost:3001'},
        )

        self.assertEqual(response.status_code, 403)

    def test_same_host_and_port_origin_is_accepted(self):
        with utily.app.test_request_context(
            '/',
            base_url='http://localhost:3001',
            headers={'Origin': 'http://localhost:3001'},
        ):
            self.assertTrue(utily.origen_es_mismo_host())

    def _es_segura(self, base_url, env=None):
        origen = base_url
        with mock.patch.dict(os.environ, env or {}, clear=False):
            with utily.app.test_request_context('/', base_url=base_url, headers={'Origin': origen}):
                return utily.solicitud_acceso_es_segura()

    def test_http_login_allowed_on_tailscale_and_lan(self):
        for url in ('http://100.111.35.87:3001', 'http://192.168.1.20:3001',
                    'http://10.0.0.5:3001', 'http://localhost:3001', 'http://127.0.0.1:3001'):
            with self.subTest(url=url):
                self.assertTrue(self._es_segura(url, {'AUTH_EXIGIR_HTTPS': ''}))

    def test_http_login_rejected_on_public_host(self):
        self.assertFalse(self._es_segura('http://8.8.8.8:3001', {'AUTH_EXIGIR_HTTPS': ''}))
        self.assertFalse(self._es_segura('http://rh.example.com', {'AUTH_EXIGIR_HTTPS': '', 'AUTH_HOSTS_HTTP': ''}))
        self.assertTrue(self._es_segura('https://rh.example.com'))

    def test_http_hosts_configurables(self):
        self.assertTrue(self._es_segura('http://servidor-rh:3001', {'AUTH_HOSTS_HTTP': 'servidor-rh, otro'}))

    def test_exigir_https_solo_permite_localhost(self):
        env = {'AUTH_EXIGIR_HTTPS': '1'}
        self.assertFalse(self._es_segura('http://100.111.35.87:3001', env))
        self.assertTrue(self._es_segura('http://localhost:3001', env))

    def test_login_from_tailscale_reaches_credentials_check(self):
        with mock.patch.object(utily, 'get_db_connection', side_effect=RuntimeError('sin bd')):
            response = utily.app.test_client().post(
                '/api/auth/login',
                base_url='http://100.111.35.87:3001',
                json={'usuario': 'usuario', 'password': 'password'},
                headers={'Origin': 'http://100.111.35.87:3001'},
            )
        self.assertNotEqual(response.status_code, 400)


if __name__ == '__main__':
    unittest.main()
