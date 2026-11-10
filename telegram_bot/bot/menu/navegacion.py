"""Árbol del menú /sistema.

Las claves de los nodos viajan en ``callback_data`` de mensajes ya enviados:
renombrar cambia el título, nunca la clave. Los permisos solo deciden qué
botones se muestran; quien decide de verdad es el backend.
"""

from dataclasses import dataclass

TODOS = frozenset({'admin', 'reclutamiento'})
ADMIN = frozenset({'admin'})


@dataclass(frozen=True)
class Nodo:
    clave: str
    titulo: str
    permisos: frozenset[str] = TODOS
    hijos: tuple[str, ...] = ()
    #: Acción de ``pantallas`` que ejecuta este nodo (si no tiene hijos).
    accion: str | None = None


NODOS: dict[str, Nodo] = {n.clave: n for n in (
    Nodo('raiz', 'Menú principal', hijos=('empleados', 'ingresos', 'reclutamiento', 'descargas')),
    Nodo('empleados', '👥 Empleados', accion='buscar_empleado'),
    Nodo('ingresos', '📝 Ingresos pendientes', ADMIN, accion='pendientes'),
    Nodo('reclutamiento', '📊 Reclutamiento', hijos=('ind_mes', 'ind_team')),
    Nodo('ind_mes', 'Ingresos del mes por fuente', accion='ind_mes'),
    Nodo('ind_team', 'Ingresos del mes por equipo', accion='ind_team'),
    Nodo('descargas', '📥 Descargas', hijos=('dl_cw', 'dl_kronos', 'dl_reclu')),
    Nodo('dl_cw', 'Listado general Cheong Woon', ADMIN, accion='dl_cw'),
    Nodo('dl_kronos', 'Listado general Kronos', ADMIN, accion='dl_kronos'),
    Nodo('dl_reclu', 'Reporte de reclutamiento del mes', accion='dl_reclu'),
)}


def puede(nodo: Nodo, permisos: list[str] | set[str]) -> bool:
    return bool(nodo.permisos & set(permisos))


def hijos_visibles(clave: str, permisos: list[str] | set[str]) -> list[Nodo]:
    """Hijos que la persona puede ver; una rama sin hojas visibles se oculta."""
    visibles = []
    for hijo in NODOS[clave].hijos:
        nodo = NODOS[hijo]
        if not puede(nodo, permisos):
            continue
        if nodo.hijos and not hijos_visibles(hijo, permisos):
            continue
        visibles.append(nodo)
    return visibles


def padre_de(clave: str) -> str | None:
    for nodo in NODOS.values():
        if clave in nodo.hijos:
            return nodo.clave
    return None
