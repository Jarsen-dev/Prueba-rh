import unittest

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
            ['recursos_humanos', 'notificaciones'],
        )

    def test_legacy_attendance_permission_is_not_returned_to_clients(self):
        usuario = utily.usuario_acceso_dict(
            (7, 'usuario', 'hash', ['recursos_humanos', 'control_asistencias'], 'Almacen', '5551234')
        )

        self.assertEqual(usuario['permisos'], ['recursos_humanos'])
        self.assertNotIn('area_responsable', usuario)

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

        self.assertEqual(response.status_code, 400)
        self.assertIn('al menos un permiso', response.get_json()['error'])


if __name__ == '__main__':
    unittest.main()
