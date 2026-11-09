import json
from datetime import UTC, datetime, timedelta

import httpx
import pytest

from bot.backend import ClienteBackend, ErrorBackend, NoVinculado, mensaje_de_error, nombre_de_descarga


def sesion_json(token='t1', minutos=15, permisos=('admin',)):
    return {
        'token': token,
        'expira_en': (datetime.now(UTC) + timedelta(minutes=minutos)).isoformat(),
        'usuario': {'id': 1, 'usuario': 'ana', 'permisos': list(permisos)},
    }


def cliente(manejador):
    http = httpx.AsyncClient(transport=httpx.MockTransport(manejador))
    return ClienteBackend(base_url='http://backend:8000/', secreto='s3', http=http)


async def test_rutas_internas_llevan_el_secreto():
    vistas = []

    def manejador(request):
        vistas.append(request)
        return httpx.Response(200, json=sesion_json())

    await cliente(manejador).sesion(10)
    assert str(vistas[0].url) == 'http://backend:8000/api/telegram/interno/sesion'
    assert vistas[0].headers['X-Bot-Secreto'] == 's3'
    assert json.loads(vistas[0].content) == {'telegram_id': 10}


async def test_no_vinculado():
    c = cliente(lambda r: httpx.Response(404, json={'error': 'Este Telegram no está vinculado.', 'codigo': 'NO_VINCULADO'}))
    with pytest.raises(NoVinculado):
        await c.sesion(10)


async def test_sesion_se_cachea_y_se_renueva_cerca_de_vencer():
    llamadas = []

    def manejador(request):
        llamadas.append(request)
        return httpx.Response(200, json=sesion_json(minutos=0.5 if len(llamadas) == 1 else 15))

    c = cliente(manejador)
    await c.sesion(10)       # vence en 30 s: dentro del margen de 60 s
    await c.sesion(10)       # se renueva
    await c.sesion(10)       # ya está vigente
    assert len(llamadas) == 2


async def test_peticion_usa_bearer_de_la_persona():
    vistas = []

    def manejador(request):
        if request.url.path.endswith('/sesion'):
            return httpx.Response(200, json=sesion_json(token='tok-ana'))
        vistas.append(request)
        return httpx.Response(200, json={'empleados': []})

    r = await cliente(manejador).peticion(10, 'GET', '/api/empleados/buscar', params={'q': 'ana', 'x': ''})
    assert r.datos == {'empleados': []}
    assert vistas[0].headers['Authorization'] == 'Bearer tok-ana'
    assert 'x=' not in str(vistas[0].url)
    assert 'X-Bot-Secreto' not in vistas[0].headers


async def test_401_renueva_una_vez_y_luego_falla():
    sesiones, api = [], []

    def manejador(request):
        if request.url.path.endswith('/sesion'):
            sesiones.append(request)
            return httpx.Response(200, json=sesion_json(token=f't{len(sesiones)}'))
        api.append(request)
        return httpx.Response(401, json={'error': 'La sesión expiró o fue revocada.'})

    with pytest.raises(ErrorBackend) as error:
        await cliente(manejador).peticion(10, 'GET', '/api/auth/me')
    assert error.value.status == 401
    assert len(sesiones) == 2 and len(api) == 2


async def test_403_trae_el_mensaje_del_backend():
    def manejador(request):
        if request.url.path.endswith('/sesion'):
            return httpx.Response(200, json=sesion_json())
        return httpx.Response(403, json={'error': 'No tienes permiso para esta acción.', 'codigo': 'SIN_PERMISO'})

    with pytest.raises(ErrorBackend) as error:
        await cliente(manejador).peticion(10, 'GET', '/api/access-users')
    assert (error.value.status, error.value.codigo) == (403, 'SIN_PERMISO')


async def test_archivo_se_devuelve_como_contenido():
    def manejador(request):
        if request.url.path.endswith('/sesion'):
            return httpx.Response(200, json=sesion_json())
        return httpx.Response(
            200,
            content=b'PK..',
            headers={'content-type': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
                     'content-disposition': 'attachment; filename=personal.xlsx'},
        )

    r = await cliente(manejador).peticion(10, 'GET', '/api/exportar-empleados/KRONOS')
    assert r.es_archivo and r.nombre_archivo == 'personal.xlsx'


def test_mensaje_de_error_texto_plano_y_html():
    assert mensaje_de_error(404, None, 'ERROR: Empleado no encontrado') == 'Empleado no encontrado'
    assert mensaje_de_error(500, None, '<!doctype html><title>500</title>') == 'El sistema respondió con error 500.'
    assert mensaje_de_error(400, {'error': 'Falta dato'}) == 'Falta dato'


def test_nombre_de_descarga():
    assert nombre_de_descarga("attachment; filename*=UTF-8''reporte%20rh.xlsx") == 'reporte rh.xlsx'
    assert nombre_de_descarga(None) is None


async def test_get_se_repite_si_la_conexion_estaba_cerrada_pero_una_escritura_no():
    import httpx
    import pytest

    from bot.backend import ClienteBackend

    llamadas = []

    def manejar(peticion):
        if peticion.url.path.endswith('/sesion'):
            return httpx.Response(200, json={'token': 't', 'expira_en': '2099-01-01T00:00:00+00:00',
                                             'usuario': {'id': 1, 'usuario': 'x', 'permisos': ['admin']}})
        llamadas.append(peticion.method)
        if len(llamadas) % 2 == 1:
            raise httpx.RemoteProtocolError('Server disconnected without sending a response.')
        return httpx.Response(200, json={'ok': True})

    async with httpx.AsyncClient(transport=httpx.MockTransport(manejar)) as http:
        backend = ClienteBackend(base_url='http://b', secreto='s', http=http)
        assert (await backend.peticion(1, 'GET', '/api/x')).datos == {'ok': True}
        assert llamadas == ['GET', 'GET']
        with pytest.raises(httpx.RemoteProtocolError):
            await backend.peticion(1, 'POST', '/api/x', json={})
        assert llamadas == ['GET', 'GET', 'POST']
