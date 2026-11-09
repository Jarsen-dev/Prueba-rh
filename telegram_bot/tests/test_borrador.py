import pytest

from bot.borrador import CambioPendiente


def test_delete_pide_dos_confirmaciones_y_avisa():
    p = CambioPendiente('delete', '/api/eliminar-empleado/3', 'Eliminar', ['Nombre: Ana'])
    assert p.metodo == 'DELETE'
    assert p.confirmaciones_faltantes == 2
    assert 'permanente' in p.resumen()
    assert '• Nombre: Ana' in p.resumen()


def test_post_pide_una():
    assert CambioPendiente('POST', '/api/guardar-ingreso', 'x').confirmaciones_faltantes == 1


@pytest.mark.parametrize('metodo,ruta', [
    ('GET', '/api/x'),
    ('POST', '/admin/normalize-encuestas'),
    ('POST', '/api/../admin'),
    ('POST', '/api//x'),
    ('POST', 'http://otro/api/x'),
])
def test_rechaza_lectura_y_rutas_raras(metodo, ruta):
    with pytest.raises(ValueError):
        CambioPendiente(metodo, ruta, 'x')


def test_caduca():
    p = CambioPendiente('POST', '/api/x', 'x')
    assert not p.caducado(30, ahora=p.creado + 60)
    assert p.caducado(30, ahora=p.creado + 31 * 60)


def test_ids_distintos():
    assert len({CambioPendiente('POST', '/api/x', 'x').id for _ in range(50)}) == 50
