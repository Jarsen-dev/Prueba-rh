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
        self.queries.append(query)

    def fetchall(self):
        return []


class FakeConnection:
    def __init__(self, cursor):
        self.fake_cursor = cursor

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        return False

    def cursor(self):
        return self.fake_cursor


class ListadoGeneralTests(unittest.TestCase):
    def setUp(self):
        self.cursor = FakeCursor()
        self.connection = FakeConnection(self.cursor)
        self.client = utily.app.test_client()

    def exportar(self, query=''):
        with patch.object(utily, 'get_db_connection', return_value=self.connection):
            return self.client.get(f'/api/exportar-empleados/CHEONG_WOON{query}')

    def test_listado_general_includes_inactive_employees_without_time_limit(self):
        response = self.exportar('?todos=1')

        self.assertEqual(response.status_code, 200)
        query = next(sql for sql in self.cursor.queries if 'FROM registro AS r' in sql)
        self.assertNotIn("fecha_inactivo >= CURRENT_TIMESTAMP - INTERVAL '7 days'", query)

    def test_existing_employee_export_keeps_recent_inactive_filter(self):
        response = self.exportar()

        self.assertEqual(response.status_code, 200)
        query = next(sql for sql in self.cursor.queries if 'FROM registro AS r' in sql)
        self.assertIn("fecha_inactivo >= CURRENT_TIMESTAMP - INTERVAL '7 days'", query)


if __name__ == '__main__':
    unittest.main()
