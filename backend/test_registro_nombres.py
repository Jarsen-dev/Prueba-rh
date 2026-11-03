import unittest
from unittest.mock import patch

import utily


class FakeCursor:
    def __init__(self):
        self.queries = []

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        return False

    def execute(self, query, params=None):
        self.queries.append((query, params))


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
                },
            )

        self.assertEqual(response.status_code, 200)
        insert_query, values = next(
            (query, params)
            for query, params in cursor.queries
            if 'INSERT INTO registro' in query
        )
        self.assertIn('nombre_completo, nombre, apellido_paterno, apellido_materno', insert_query)
        self.assertEqual(values[:4], ('Ana María López García', 'Ana María', 'López', 'García'))
        self.assertTrue(connection.committed)

    def test_display_name_expression_falls_back_to_legacy_full_name(self):
        expression = utily.expresion_nombre_completo('r')

        self.assertIn('r.nombre', expression)
        self.assertIn('r.apellido_paterno', expression)
        self.assertIn('r.apellido_materno', expression)
        self.assertIn('r.nombre_completo', expression)


if __name__ == '__main__':
    unittest.main()
