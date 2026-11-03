import unittest
from unittest.mock import patch

import utily


class FakeCursor:
    def __init__(self, rows=None, fetchall_results=None):
        self.queries = []
        self.rows = rows or []
        self.fetchall_results = list(fetchall_results or [])

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        return False

    def execute(self, query, params=None):
        self.queries.append((query, params))

    def fetchall(self):
        if self.fetchall_results:
            return self.fetchall_results.pop(0)
        return self.rows

    def fetchone(self):
        return None


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


class RegistroNombreCompletoTests(unittest.TestCase):
    def test_registration_saves_full_name_and_temporary_number(self):
        cursor = FakeCursor()
        connection = FakeConnection(cursor)
        client = utily.app.test_client()

        with patch.object(utily, 'get_db_connection', return_value=connection):
            response = client.post(
                '/api/guardar-registro',
                data={
                    'nombre_completo': 'Ana María López García',
                    'numero_temporal': 'TEMP - 123',
                },
            )

        self.assertEqual(response.status_code, 200)
        insert_query, values = next(
            (query, params)
            for query, params in cursor.queries
            if 'INSERT INTO registro' in query
        )
        self.assertIn('nombre_completo, numero_temporal', insert_query)
        self.assertNotIn('apellido_paterno', insert_query)
        self.assertEqual(values[:2], ('Ana María López García', 'TEMP - 123'))
        self.assertEqual(insert_query.count('%s'), len(values))
        self.assertTrue(connection.committed)

    def test_registration_rejects_missing_full_name(self):
        client = utily.app.test_client()

        with patch.object(utily, 'get_db_connection') as get_db_connection:
            response = client.post('/api/guardar-registro', data={'curp': 'TEST'})

        self.assertEqual(response.status_code, 400)
        self.assertIn('nombre completo', response.get_data(as_text=True).lower())
        get_db_connection.assert_not_called()

    def test_registration_combines_legacy_split_name_fields(self):
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
        self.assertIn('nombre_completo, numero_temporal', insert_query)
        self.assertEqual(values[0], 'Ana María López García')

    def test_registration_rejects_name_longer_than_database_column(self):
        client = utily.app.test_client()

        response = client.post(
            '/api/guardar-registro',
            data={'nombre_completo': 'A' * 101},
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn('100 caracteres', response.get_data(as_text=True))

    def test_existing_split_name_columns_are_combined_without_dropping_data(self):
        columns = [(name,) for name in (
            'id', 'nombre', 'apellido_paterno', 'apellido_materno',
        )]
        cursor = FakeCursor(fetchall_results=[columns])

        utily.asegurar_columna_nombre_completo(cursor)

        self.assertTrue(any(
            'ADD COLUMN IF NOT EXISTS nombre_completo' in query
            for query, _ in cursor.queries
        ))
        update_query = next(query for query, _ in cursor.queries if 'UPDATE registro' in query)
        self.assertIn('CONCAT_WS', update_query)
        self.assertFalse(any('DROP COLUMN' in query for query, _ in cursor.queries))

    def test_pending_candidates_return_full_name_field(self):
        cursor = FakeCursor(fetchall_results=[
            [],
            [('Ana María López García', 'TEMP - 123', 12)],
        ])
        client = utily.app.test_client()

        with patch.object(utily, 'get_db_connection', return_value=FakeConnection(cursor)):
            response = client.get('/api/obtener-pendientes')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json(), [{
            'nombre_completo': 'Ana María López García',
            'numero_temporal': 'TEMP - 123',
            'id': 12,
        }])

    def test_pending_candidates_keep_duplicate_full_names(self):
        cursor = FakeCursor(fetchall_results=[
            [],
            [
                ('Ana López García', 'TEMP - 123', 12),
                ('Ana López García', 'TEMP - 124', 13),
            ],
        ])
        client = utily.app.test_client()

        with patch.object(utily, 'get_db_connection', return_value=FakeConnection(cursor)):
            response = client.get('/api/obtener-pendientes')

        self.assertEqual(response.status_code, 200)
        self.assertEqual([item['id'] for item in response.get_json()], [12, 13])

    def test_income_schema_adds_registration_link_for_existing_databases(self):
        cursor = FakeCursor()

        utily.asegurar_esquema_ingresos_puesto(cursor)

        self.assertTrue(any(
            'CREATE TABLE IF NOT EXISTS ingresos_puesto' in query
            for query, _ in cursor.queries
        ))
        self.assertTrue(any(
            'ADD COLUMN IF NOT EXISTS registro_id' in query
            for query, _ in cursor.queries
        ))

    def test_saving_income_inserts_assignment_linked_to_registration(self):
        cursor = FakeCursor()
        connection = FakeConnection(cursor)
        client = utily.app.test_client()
        payload = {
            'nombre_candidato': 'Ana María López García',
            'empresa': 'CHEONG WOON',
            'no_empleado': 'TEMP - 123',
            'personal': 'OPERATIVO',
            'puesto': 'OPERARIO',
            'area': 'EPS',
            'turno': 'DÍA',
            'salario': '1000',
            'pago': 'SEMANAL',
            'registro_id': '12',
        }

        with patch.object(utily, 'get_db_connection', return_value=connection):
            response = client.post('/api/guardar-ingreso', json=payload)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_data(as_text=True), 'OK')
        insert_query, insert_values = next(
            (query, params)
            for query, params in cursor.queries
            if 'INSERT INTO ingresos_puesto' in query
        )
        self.assertIn('registro_id', insert_query)
        self.assertEqual(insert_values[-1], 12)
        self.assertTrue(connection.committed)


if __name__ == '__main__':
    unittest.main()
