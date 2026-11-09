from bot.menu import pantallas
from bot.menu.navegacion import NODOS, hijos_visibles, padre_de


def test_todo_hijo_existe_y_las_claves_coinciden():
    for clave, nodo in NODOS.items():
        assert nodo.clave == clave
        for hijo in nodo.hijos:
            assert hijo in NODOS, f'{clave} apunta a {hijo}, que no existe'


def test_cada_hoja_tiene_una_accion_implementada():
    hojas = {n.accion for n in NODOS.values() if not n.hijos}
    assert None not in hojas
    assert hojas == set(pantallas.ACCIONES)


def test_callback_data_de_nodos_cabe_en_64_bytes():
    assert all(len(f'm:{clave}'.encode()) <= 64 for clave in NODOS)


def test_cada_nodo_es_alcanzable_desde_la_raiz():
    alcanzables, pendientes = set(), ['raiz']
    while pendientes:
        clave = pendientes.pop()
        alcanzables.add(clave)
        pendientes.extend(NODOS[clave].hijos)
    assert alcanzables == set(NODOS)


def test_reclutamiento_no_ve_lo_de_admin():
    raiz = [n.clave for n in hijos_visibles('raiz', ['reclutamiento'])]
    assert raiz == ['empleados', 'reclutamiento', 'descargas']
    assert [n.clave for n in hijos_visibles('descargas', ['reclutamiento'])] == ['dl_reclu']


def test_admin_ve_todo():
    assert [n.clave for n in hijos_visibles('raiz', ['admin'])] == ['empleados', 'ingresos', 'reclutamiento', 'descargas']


def test_sin_permisos_no_ve_nada():
    assert hijos_visibles('raiz', ['notificaciones']) == []


def test_padre():
    assert padre_de('dl_cw') == 'descargas'
    assert padre_de('raiz') is None
