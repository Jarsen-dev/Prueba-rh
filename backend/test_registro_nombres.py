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


class RegistroNombresTests(unittest.TestCase):
    def test_registration_saves_only_separate_name_fields(self):
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
        self.assertNotIn('nombre_completo', insert_query)
        self.assertIn('nombre, apellido_paterno, apellido_materno, numero_temporal', insert_query)
        self.assertEqual(
            values[:4],
            ('Ana María', 'López', 'García', 'TEMP - 123'),
        )
        self.assertTrue(connection.committed)

    def test_legacy_name_parser_separates_names_and_compound_surnames(self):
        self.assertEqual(
            utily.separar_nombre_legado('Ana María López García'),
            ('Ana María', 'López', 'García'),
        )
        self.assertEqual(
            utily.separar_nombre_legado('Ana María de la Cruz García'),
            ('Ana María', 'de la Cruz', 'García'),
        )
        self.assertEqual(
            utily.separar_nombre_legado('José de la Cruz'),
            ('José', '', 'de la Cruz'),
        )

    def test_legacy_migration_copies_missing_parts_before_dropping_old_column(self):
        columns = [(name,) for name in (
            'id', 'nombre', 'apellido_paterno', 'apellido_materno', 'nombre_completo',
        )]
        cursor = FakeCursor(fetchall_results=[
            columns,
            columns,
            [(7, 'Ana María López García')],
        ])

        utily.asegurar_columnas_nombre(cursor)

        update_query, update_params = next(
            (query, params)
            for query, params in cursor.queries
            if 'UPDATE registro' in query
        )
        self.assertIn('COALESCE(NULLIF(BTRIM(nombre)', update_query)
        self.assertEqual(update_params, ('Ana María', 'López', 'García', 7))
        self.assertEqual(cursor.queries[-1][0], 'ALTER TABLE registro DROP COLUMN nombre_completo')

    def test_pending_candidates_include_only_separate_name_fields(self):
        cursor = FakeCursor(fetchall_results=[
            [],
            [('Ana María', 'López', 'García', 'TEMP - 123', 12)],
        ])
        client = utily.app.test_client()

        with patch.object(utily, 'get_db_connection', return_value=FakeConnection(cursor)):
            response = client.get('/api/obtener-pendientes')

        self.assertEqual(response.status_code, 200)
        data = response.get_json()
        self.assertEqual(data, [{
            'nombre': 'Ana María',
            'apellido_paterno': 'López',
            'apellido_materno': 'García',
            'numero_temporal': 'TEMP - 123',
            'id': 12,
        }])
        self.assertNotIn('nombre_completo', data[0])

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
