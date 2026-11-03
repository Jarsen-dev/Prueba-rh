import unittest
from unittest.mock import patch

import utily


class FakeCursor:
    def __init__(self, rows=None):
        self.queries = []
        self.rows = rows or []

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        return False

    def execute(self, query, params=None):
        self.queries.append((query, params))

    def fetchall(self):
        return self.rows


class FakeConnection:
    def __init__(self, cursor):
        self.fake_cursor = cursor
        self.committed = False

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        return False

    def cursor(self):
        return self.fake_cursor

    def commit(self):
        self.committed = True


class RegistroNombresTests(unittest.TestCase):
    def test_registration_saves_separate_names_and_combined_legacy_name(self):
        cursor = FakeCursor()
        connection = FakeConnection(cursor)
        client = utily.app.test_client()

        with patch.object(utily, 'get_db_connection', return_value=connection):
            response = client.post(
                '/api/guardar-registro',
                data={
                    'nombre': 'Ana María',
                    'apellido_paterno': 'López',
                    'apellido_materno': 'García',
                    'numero_temporal': 'TEMP - 123',
                },
            )

        self.assertEqual(response.status_code, 200)
        insert_query, values = next(
            (query, params)
            for query, params in cursor.queries
            if 'INSERT INTO registro' in query
        )
        self.assertIn(
            'nombre_completo, nombre, apellido_paterno, apellido_materno, numero_temporal',
            insert_query,
        )
        self.assertEqual(
            values[:5],
            ('Ana María López García', 'Ana María', 'López', 'García', 'TEMP - 123'),
        )
        self.assertTrue(connection.committed)

    def test_display_name_expression_falls_back_to_legacy_full_name(self):
        expression = utily.expresion_nombre_completo('r')

        self.assertIn('r.nombre', expression)
        self.assertIn('r.apellido_paterno', expression)
        self.assertIn('r.apellido_materno', expression)
        self.assertIn('r.nombre_completo', expression)

    def test_pending_candidates_include_separate_names_and_full_name(self):
        cursor = FakeCursor([('Ana María', 'López', 'García', 'Ana María López García', 'TEMP - 123', 12)])
        client = utily.app.test_client()

        with patch.object(utily, 'get_db_connection', return_value=FakeConnection(cursor)):
            response = client.get('/api/obtener-pendientes')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json(), [{
            'nombre': 'Ana María',
            'apellido_paterno': 'López',
            'apellido_materno': 'García',
            'nombre_completo': 'Ana María López García',
            'numero_temporal': 'TEMP - 123',
            'id': 12,
        }])


if __name__ == '__main__':
    unittest.main()
