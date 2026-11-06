import io
import json
import os
import unittest
from unittest.mock import Mock, patch

import utily


class EscanearDocumentoTests(unittest.TestCase):
    def setUp(self):
        self.client = utily.app.test_client()

    @patch.dict(os.environ, {'OPENAI_API_KEY': 'test-key'})
    @patch('utily.requests.post')
    def test_document_types_send_their_exact_field_guidance_and_schema(self, post_openai):
        casos = {
            'Acta de Nacimiento': {
                'nombre_completo', 'fecha_nac', 'genero',
            },
            'Comprobante de Domicilio': {
                'cp', 'colonia', 'calle', 'num_ext', 'num_int', 'municipio', 'estado',
            },
            'Constancia de Situación Fiscal': {
                'nombre_completo', 'rfc', 'curp', 'cp', 'colonia', 'calle',
                'num_ext', 'num_int', 'municipio', 'estado',
            },
            'Número de Seguro Social': {'nss'},
        }

        for tipo_documento, campos_esperados in casos.items():
            with self.subTest(tipo_documento=tipo_documento):
                valores_vacios = json.dumps({
                    campo: None for campo in campos_esperados
                })
                respuesta = Mock()
                respuesta.json.return_value = {'output_text': valores_vacios}
                post_openai.return_value = respuesta

                response = self.client.post(
                    '/api/escanear-documento',
                    data={
                        'tipo_documento': tipo_documento,
                        'archivo': (
                            io.BytesIO(b'%PDF-1.7 documento'),
                            'documento.pdf',
                        ),
                    },
                    content_type='multipart/form-data',
                )

                self.assertEqual(response.status_code, 200)
                self.assertEqual(set(response.get_json()['campos']), campos_esperados)
                solicitud = post_openai.call_args.kwargs['json']
                esquema = solicitud['text']['format']['schema']
                self.assertEqual(set(esquema['properties']), campos_esperados)
                self.assertEqual(set(esquema['required']), campos_esperados)
                texto = solicitud['input'][0]['content'][0]['text']
                self.assertIn(tipo_documento, texto)
                self.assertIn(
                    utily.INSTRUCCIONES_TIPO_DOCUMENTO[tipo_documento],
                    texto,
                )
                for campo in campos_esperados:
                    self.assertIn(
                        utily.INSTRUCCIONES_CAMPOS_DOCUMENTO[campo],
                        esquema['properties'][campo]['description'],
                    )

    @patch.dict(os.environ, {'OPENAI_API_KEY': 'test-key'})
    @patch('utily.requests.post')
    def test_scans_pdf_and_returns_only_fields_for_selected_document(self, post_openai):
        respuesta = Mock()
        respuesta.json.return_value = {
            'output_text': (
                '{"nombre_completo":"Ana López","fecha_nac":"2000-01-02",'
                '"genero":"FEMENINO"}'
            ),
        }
        post_openai.return_value = respuesta

        response = self.client.post(
            '/api/escanear-documento',
            data={
                'tipo_documento': 'Acta de Nacimiento',
                'archivo': (io.BytesIO(b'%PDF-1.7 documento'), 'acta.pdf'),
            },
            content_type='multipart/form-data',
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()['campos'], {
            'nombre_completo': 'Ana López',
            'fecha_nac': '2000-01-02',
            'genero': 'FEMENINO',
        })
        solicitud = post_openai.call_args.kwargs['json']
        self.assertEqual(
            solicitud['input'][0]['content'][1]['type'],
            'input_file',
        )
        self.assertEqual(
            set(solicitud['text']['format']['schema']['properties']),
            {'nombre_completo', 'fecha_nac', 'genero'},
        )

    @patch.dict(os.environ, {'OPENAI_API_KEY': 'test-key'})
    @patch('utily.requests.post')
    def test_scans_image_as_image_input(self, post_openai):
        respuesta = Mock()
        respuesta.json.return_value = {'output_text': '{"nss":"12345678901"}'}
        post_openai.return_value = respuesta

        response = self.client.post(
            '/api/escanear-documento',
            data={
                'tipo_documento': 'Número de Seguro Social',
                'archivo': (
                    io.BytesIO(b'\x89PNG\r\n\x1a\ncontenido'),
                    'nss.png',
                ),
            },
            content_type='multipart/form-data',
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()['campos'], {'nss': '12345678901'})
        self.assertEqual(
            post_openai.call_args.kwargs['json']['input'][0]['content'][1]['type'],
            'input_image',
        )

    @patch.dict(os.environ, {'OPENAI_API_KEY': 'test-key'})
    @patch('utily.requests.post')
    def test_ignores_invalid_dates_and_values_longer_than_form_limits(self, post_openai):
        respuesta = Mock()
        respuesta.json.return_value = {
            'output_text': (
                '{"nombre_completo":"' + ('A' * 101) + '",'
                '"fecha_nac":"2026-99-99","genero":"FEMENINO"}'
            ),
        }
        post_openai.return_value = respuesta

        response = self.client.post(
            '/api/escanear-documento',
            data={
                'tipo_documento': 'Acta de Nacimiento',
                'archivo': (
                    io.BytesIO(b'\x89PNG\r\n\x1a\ncontenido'),
                    'acta.png',
                ),
            },
            content_type='multipart/form-data',
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()['campos'], {
            'nombre_completo': None,
            'fecha_nac': None,
            'genero': 'FEMENINO',
        })

    @patch.dict(os.environ, {'OPENAI_API_KEY': 'test-key'})
    @patch('utily.requests.post')
    def test_returns_error_for_unexpected_openai_response_shape(self, post_openai):
        respuesta = Mock()
        respuesta.json.return_value = []
        post_openai.return_value = respuesta

        response = self.client.post(
            '/api/escanear-documento',
            data={
                'tipo_documento': 'Número de Seguro Social',
                'archivo': (
                    io.BytesIO(b'\x89PNG\r\n\x1a\ncontenido'),
                    'nss.png',
                ),
            },
            content_type='multipart/form-data',
        )

        self.assertEqual(response.status_code, 502)
        self.assertIn('extraer datos', response.get_json()['error'])

    @patch('utily.requests.post')
    def test_rejects_unknown_document_type_before_calling_openai(self, post_openai):
        response = self.client.post(
            '/api/escanear-documento',
            data={'tipo_documento': 'INE'},
        )

        self.assertEqual(response.status_code, 400)
        post_openai.assert_not_called()

    @patch.dict(os.environ, {'OPENAI_API_KEY': 'test-key'})
    @patch('utily.requests.post')
    def test_rejects_file_with_mismatched_content(self, post_openai):
        response = self.client.post(
            '/api/escanear-documento',
            data={
                'tipo_documento': 'Comprobante de Domicilio',
                'archivo': (io.BytesIO(b'no es una imagen'), 'comprobante.jpg'),
            },
            content_type='multipart/form-data',
        )

        self.assertEqual(response.status_code, 400)
        post_openai.assert_not_called()

    @patch.dict(os.environ, {'OPENAI_API_KEY': 'test-key'})
    @patch('utily.requests.post')
    def test_rejects_request_larger_than_global_upload_limit(self, post_openai):
        response = self.client.post(
            '/api/escanear-documento',
            data={
                'tipo_documento': 'Número de Seguro Social',
                'archivo': (
                    io.BytesIO(b'\x89PNG\r\n\x1a\n' + b'x' * (10 * 1024 * 1024)),
                    'nss.png',
                ),
            },
            content_type='multipart/form-data',
        )

        self.assertEqual(response.status_code, 413)
        self.assertIn('10 MB', response.get_json()['error'])
        post_openai.assert_not_called()

    @patch.dict(os.environ, {}, clear=True)
    @patch('utily.requests.post')
    def test_requires_openai_api_key(self, post_openai):
        response = self.client.post(
            '/api/escanear-documento',
            data={
                'tipo_documento': 'Número de Seguro Social',
                'archivo': (
                    io.BytesIO(b'\x89PNG\r\n\x1a\ncontenido'),
                    'nss.png',
                ),
            },
            content_type='multipart/form-data',
        )

        self.assertEqual(response.status_code, 503)
        post_openai.assert_not_called()


if __name__ == '__main__':
    unittest.main()
