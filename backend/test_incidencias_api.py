import os
import unittest
from datetime import date, datetime
from io import BytesIO
from unittest.mock import patch

import utily


class FakeCursor:
    def __init__(self):
        self.result = None
        self.rows = []
        self.inserted = None
        self.insert_sql = ""
        self.delete_sql = ""

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        return False

    def execute(self, query, params=None):
        if 'FROM information_schema.columns' in query:
            self.result = ('YES',)
        elif 'SELECT ip.no_empleado' in query:
            self.result = ('1001',)
        elif 'INSERT INTO incidencias_empleado' in query:
            self.insert_sql = query
            self.inserted = params
            self.result = (
                17,
                params[2],
                params[3],
                params[4],
                params[5],
                params[6],
                params[7],
                params[8],
                [],
                params[10],
                params[11],
                datetime(2026, 10, 27, 10, 0),
            )
        elif 'SELECT id, tipo, motivo' in query:
            self.rows = [
                (
                    17,
                    'vacaciones',
                    'Vacaciones',
                    date(2026, 11, 1),
                    date(2026, 11, 3),
                    3,
                    None,
                    None,
                    [],
                    '',
                    None,
                    datetime(2026, 10, 27, 10, 0),
                )
            ]
        elif 'DELETE FROM incidencias_empleado' in query:
            self.delete_sql = query
            self.result = (17,) if params == (17, 1) else None
        else:
            self.result = None

    def fetchone(self):
        return self.result

    def fetchall(self):
        return self.rows


class FakeConnection:
    def __init__(self, cursor):
        self.fake_cursor = cursor

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        return False

    def cursor(self):
        return self.fake_cursor

    def commit(self):
        pass


class IncidenciasApiTests(unittest.TestCase):
    def setUp(self):
        self.write_lock_patch = patch.dict(os.environ, {'DB_WRITE_LOCK': '0'})
        self.write_lock_patch.start()
        self.cursor = FakeCursor()
        self.connection = FakeConnection(self.cursor)
        self.client = utily.app.test_client()

    def tearDown(self):
        self.write_lock_patch.stop()

    def test_post_persists_employee_incident_in_database(self):
        with patch.object(utily, 'get_db_connection', return_value=self.connection):
            response = self.client.post(
                '/api/empleados/1/incidencias',
                json={
                    'tipo': 'vacaciones',
                    'motivo': 'Vacaciones',
                    'fecha_inicio': '2026-11-01',
                    'fecha_reingreso': '2026-11-03',
                },
            )

        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.get_json()['incidencia']['id'], 17)
        self.assertEqual(self.cursor.inserted[0:6], (
            1,
            '1001',
            'vacaciones',
            'Vacaciones',
            date(2026, 11, 1),
            date(2026, 11, 3),
        ))
        self.assertEqual(len(self.cursor.inserted), 14)
        self.assertIn('dias, opcion, hora, motivos, otro_motivo, archivo_nombre', self.cursor.insert_sql)
        self.assertEqual(self.cursor.inserted[6], 3)

    def test_post_persists_suspension_without_chatbot_notification(self):
        with patch.object(utily, 'get_db_connection', return_value=self.connection):
            response = self.client.post(
                '/api/empleados/1/incidencias',
                json={
                    'tipo': 'suspension',
                    'motivo': 'Incumplimiento',
                    'fecha_inicio': '2026-11-01',
                    'fecha_reingreso': '2026-11-04',
                },
            )

        self.assertEqual(response.status_code, 201)
        self.assertEqual(self.cursor.inserted[2], 'suspension')
        self.assertEqual(self.cursor.inserted[6], 3)
        self.assertEqual(self.cursor.inserted[0], 1)

    def test_post_persists_commitment_letter_incident(self):
        with patch.object(utily, 'get_db_connection', return_value=self.connection):
            response = self.client.post(
                '/api/empleados/1/incidencias',
                json={
                    'tipo': 'carta-suspension',
                    'motivo': 'Compromiso de puntualidad',
                    'fecha_inicio': '2026-11-01',
                    'fecha_reingreso': '2026-11-04',
                },
            )

        self.assertEqual(response.status_code, 201)
        self.assertEqual(self.cursor.inserted[2], 'carta-suspension')
        self.assertEqual(self.cursor.inserted[3], 'Compromiso de puntualidad')
        self.assertEqual(self.cursor.inserted[6], 3)

    def test_get_returns_database_incident_history(self):
        with patch.object(utily, 'get_db_connection', return_value=self.connection):
            response = self.client.get('/api/empleados/1/incidencias')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()['incidencias'][0]['id'], 17)
        self.assertEqual(response.get_json()['incidencias'][0]['fecha_inicio'], '2026-11-01')
        self.assertEqual(response.get_json()['incidencias'][0]['dias'], 3)

    def test_delete_uses_database_record_id_and_employee_id(self):
        with patch.object(utily, 'get_db_connection', return_value=self.connection):
            response = self.client.delete('/api/empleados/1/incidencias/17')

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.get_json()['ok'])
        self.assertIn('DELETE FROM incidencias_empleado', self.cursor.delete_sql)

    def test_post_stores_entry_exit_fields_and_uploaded_file(self):
        with patch.object(utily, 'get_db_connection', return_value=self.connection):
            response = self.client.post(
                '/api/empleados/1/incidencias',
                data={
                    'tipo': 'entrada-salida',
                    'motivo': 'Entrada a las 08:15 AM. Asistencia a cita Medica',
                    'detalles': '{"opcion":"entrada","hora":"08:15","motivos":["Asistencia a cita Medica"],"otro_motivo":"","categoria":""}',
                    'archivo': (BytesIO(b'%PDF-1.4 test'), 'comprobante.pdf'),
                },
                content_type='multipart/form-data',
            )

        self.assertEqual(response.status_code, 201)
        self.assertEqual(self.cursor.inserted[7], 'entrada')
        self.assertEqual(self.cursor.inserted[8], '08:15')
        self.assertEqual(self.cursor.inserted[11], 'comprobante.pdf')
        self.assertEqual(self.cursor.inserted[13], b'%PDF-1.4 test')

    def test_chatbot_notification_endpoint_is_removed(self):
        response = self.client.post(
            '/api/incidencias',
            json={'tipo': 'indisciplina', 'no_empleado': '1001', 'motivo': 'Defectos'},
        )

        self.assertEqual(response.status_code, 404)


if __name__ == '__main__':
    unittest.main()
