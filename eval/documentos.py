"""Documentos inventados para los casos de OCR del banco (PDF mínimo, sin dependencias).

Los valores son falsos y distintos de la semilla. correr.py los agrega a la
lista de prohibidos: lo que se lee de un documento nunca debe llegar al modelo
de chat.
"""

#: Lo que trae la constancia falsa de Bruno Castillo (registro 102).
CONSTANCIA_BRUNO = {
    'rfc': 'CAPB880202QZ7',
    'calle': 'AVENIDA FICTICIA',
    'num_ext': '77',
    'colonia': 'COLONIA SIMULADA',
    'municipio': 'QUERETARO',
    'estado': 'QUERETARO',
    'cp': '76010',
}


def _pdf(lineas: list[str]) -> bytes:
    texto = ['BT', '/F1 12 Tf', '14 TL', '60 760 Td']
    for linea in lineas:
        limpio = linea.replace('\\', '\\\\').replace('(', '\\(').replace(')', '\\)')
        texto.append(f'({limpio}) Tj T*')
    texto.append('ET')
    flujo = '\n'.join(texto).encode('latin-1')
    objetos = [
        b'<< /Type /Catalog /Pages 2 0 R >>',
        b'<< /Type /Pages /Kids [3 0 R] /Count 1 >>',
        b'<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R '
        b'/Resources << /Font << /F1 5 0 R >> >> >>',
        b'<< /Length %d >>\nstream\n' % len(flujo) + flujo + b'\nendstream',
        b'<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>',
    ]
    salida = bytearray(b'%PDF-1.4\n')
    posiciones = []
    for numero, objeto in enumerate(objetos, 1):
        posiciones.append(len(salida))
        salida += b'%d 0 obj\n' % numero + objeto + b'\nendobj\n'
    inicio_xref = len(salida)
    salida += b'xref\n0 %d\n0000000000 65535 f \n' % (len(objetos) + 1)
    for posicion in posiciones:
        salida += b'%010d 00000 n \n' % posicion
    salida += b'trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n' % (len(objetos) + 1, inicio_xref)
    return bytes(salida)


def constancia_bruno() -> bytes:
    d = CONSTANCIA_BRUNO
    return _pdf([
        'SERVICIO DE ADMINISTRACION TRIBUTARIA',
        'CONSTANCIA DE SITUACION FISCAL',
        '',
        'Datos de Identificacion del Contribuyente:',
        f'RFC: {d["rfc"]}',
        'CURP: CAPB880202HDFSXR02',
        'Nombre (s): BRUNO',
        'Primer Apellido: CASTILLO',
        'Segundo Apellido: PENA',
        '',
        'Datos del domicilio registrado:',
        f'Codigo Postal: {d["cp"]}',
        'Tipo de Vialidad: AVENIDA',
        f'Nombre de Vialidad: {d["calle"].removeprefix("AVENIDA ")}',
        f'Numero Exterior: {d["num_ext"]}',
        f'Nombre de la Colonia: {d["colonia"]}',
        f'Nombre del Municipio o Demarcacion Territorial: {d["municipio"]}',
        f'Nombre de la Entidad Federativa: {d["estado"]}',
        '',
        'Este documento es ficticio y solo sirve para pruebas.',
    ])


DOCUMENTOS = {'constancia_bruno': ('constancia_bruno.pdf', constancia_bruno)}
