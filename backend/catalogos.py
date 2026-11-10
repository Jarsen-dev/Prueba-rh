"""Catálogos de captura que hoy viven en el HTML del panel.

El bot de Telegram los pide a GET /api/catalogos/ingreso para no copiarlos.
`test_catalogos.py` compara estas listas con las de frontend/Ingreso.html y
falla si alguien cambia una sin la otra.
"""

EMPRESAS = ['CHEONG WOON', 'KRONOS']

PUESTOS_POR_AREA = {
    'EPS': [
        'AYUDANTE GENERAL', 'ALMACENISTA', 'BLOCK', 'CALDERA', 'CAPTURISTA',
        'CUARTO DE SECADO', 'GERENTE', 'JEFE', 'LIDER', 'LIDER DE CUARTO DE SECADO',
        'MANTENIMIENTO', 'MOLDERO', 'MONTACARGUISTA', 'MONTADOR', 'OPERARIO',
        'PRE-EXPANSION', 'REPARACION', 'STAFF', 'SUPERVISOR', 'TECNICO', 'CORTE',
    ],
    'ASSY': [
        'AYUDANTE GENERAL', 'CHOFER', 'COMEDOR', 'INSPECTOR DE CALIDAD', 'JARDINERO',
        'LIMPIEZA', 'RECLUTAMIENTO', 'SEGURIDAD', 'SOLDADOR', 'CAPTURISTA', 'GERENTE',
        'OPERARIO', 'STAFF', 'SUPERVISOR',
    ],
    'EPS CUTTING': ['AYUDANTE GENERAL', 'GERENTE', 'MANTENIMIENTO', 'CORTE'],
    'EMBARQUE': ['AYUDANTE GENERAL', 'CAPTURISTA', 'GERENTE', 'OPERARIO', 'STAFF'],
    'MATERIAL': ['AYUDANTE GENERAL', 'GERENTE', 'STAFF', 'AYUDANTE'],
    'CALIDAD': [
        'INSPECTOR DE CALIDAD', 'OQC SISTEMA', 'CAPTURISTA', 'GERENTE',
        'JEFE', 'LIDER', 'STAFF', 'SUPERVISOR',
    ],
    'RECURSOS HUMANOS': ['GERENTE', 'STAFF', 'AYUDANTE'],
    'EPS ALMACEN': ['CAPTURISTA', 'GERENTE', 'LIDER', 'STAFF', 'AYUDANTE'],
}

TURNOS = ['DÍA', 'NOCHE']
PAGOS = ['SEMANAL', 'QUINCENAL', 'MENSUAL']
PERSONAL = 'OPERATIVO'


def catalogo_ingreso():
    return {
        'empresas': EMPRESAS,
        'areas': list(PUESTOS_POR_AREA),
        'puestos_por_area': PUESTOS_POR_AREA,
        'turnos': TURNOS,
        'pagos': PAGOS,
        'personal': PERSONAL,
    }
