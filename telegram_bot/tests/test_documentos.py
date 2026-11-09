"""OCR: el código lee, compara y arma el cambio; el modelo nunca ve lo leído."""

import pytest

from bot import documentos, herramientas, principal
from bot.borrador import Archivo
from bot.config import Settings
from bot.estado import Estados
from bot.guardas import Vistos
from tests.fakes import Chat, FakeBackend, archivo, contexto, pulsar
from tests.test_escrituras import BRUNO, HOY

CSF = documentos.TIPOS['csf']
FOTO = Archivo('archivo', 'constancia.jpg', b'\xff\xd8 foto', 'image/jpeg')
ACTUAL = {**BRUNO, 'curp': 'CAPB880202HDFSXR02', 'rfc': 'CAPB880202CD2', 'calle': 'Calle Falsa', 'num_ext': '12',
          'num_int': None, 'colonia': 'Centro', 'municipio': 'Apodaca', 'estado': 'NUEVO LEÓN', 'codigo_postal': '66600'}
LEIDO = {'nombre_completo': 'BRUNO CASTILLO', 'rfc': 'CAPB880202XY9', 'curp': 'CAPB880202HDFSXR02',
         'calle': 'Av. Nueva', 'num_ext': '45', 'num_int': '3', 'colonia': 'centro', 'municipio': 'Apodaca',
         'estado': 'Nuevo León', 'cp': '66601'}


def test_solo_cambia_lo_distinto_y_respeta_los_campos_compuestos():
    p = documentos.pendiente_de_documento(ACTUAL, CSF, LEIDO, FOTO)
    assert (p.metodo, p.ruta) == ('POST', '/api/actualizar-empleado/102')
    assert p.cuerpo == {'rfc': 'CAPB880202XY9', 'calle': 'Av. Nueva',          # nombre: solo cambian mayúsculas
                        'numeracion': '45/3', 'ubicacion': 'Apodaca/NUEVO LEÓN/66601'}
    resumen = p.resumen()
    assert 'RFC: ****CD2 → CAPB880202XY9' in resumen                     # el nuevo completo, para revisarlo
    assert 'CAPB880202CD2' not in resumen                                 # el anterior, enmascarado
    final = p.resumen(final=True)
    assert 'CAPB880202XY9' not in final and 'Av. Nueva' not in final and 'RFC: ****XY9' in final
    s = p.siguiente
    assert (s.metodo, s.ruta, s.formulario) == ('POST', '/api/employee-files', {'registro_id': '102', 'doc_key': 'rfc_doc'})
    assert s.archivo.campo == 'file' and s.archivo.contenido is FOTO.contenido


def test_si_todo_coincide_solo_ofrece_guardar_el_archivo():
    p = documentos.pendiente_de_documento(ACTUAL, CSF, {'rfc': 'capb880202cd2'}, FOTO)
    assert p.ruta == '/api/employee-files' and 'ya coinciden' in p.resumen()


def test_documento_ilegible():
    with pytest.raises(ValueError, match='No pude leer'):
        documentos.pendiente_de_documento(ACTUAL, CSF, {}, FOTO)


def test_tipo_por_texto_y_formato():
    assert documentos.tipo_por_texto('Constancia de situacion fiscal') is CSF
    assert documentos.tipo_por_texto('nss').doc_key == 'nss_doc'
    with pytest.raises(ValueError):
        documentos.tipo_por_texto('pasaporte')
    with pytest.raises(ValueError, match='foto'):
        documentos.validar(Archivo('archivo', 'x.docx', b'', 'x'))


async def test_escanear_manda_el_formulario_del_panel():
    backend = FakeBackend(rutas={('POST', '/api/escanear-documento'): {'campos': {'rfc': ' X1 ', 'curp': None, 'otro': 'y',
                                                                                   'num_int': 'null', 'num_ext': 'S/N'}}})
    assert await documentos.escanear(backend, 7, CSF, FOTO) == {'rfc': 'X1', 'num_ext': 'S/N'}
    _m, _r, kw = backend.llamadas[0]
    assert kw['data'] == {'tipo_documento': 'Constancia de Situación Fiscal'}
    assert kw['files'] == [('archivo', ('constancia.jpg', FOTO.contenido, 'image/jpeg'))]


RUTAS = {('GET', '/api/obtener-empleado/102'): ACTUAL, ('POST', '/api/escanear-documento'): {'campos': LEIDO}}


def ctx_agente(adjunto=FOTO, permisos=('admin',)):
    v = Vistos()
    v.registrar_empleado(BRUNO)
    return herramientas.Contexto(backend=FakeBackend(permisos, RUTAS), telegram_id=1, permisos=list(permisos),
                                 hoy=HOY, textos=['actualiza a bruno con su constancia'], vistos=v, adjunto=adjunto)


async def test_el_agente_prepara_sin_ver_lo_leido():
    c = ctx_agente()
    salida = await herramientas.ejecutar(c, 'preparar_datos_de_documento',
                                         {'registro_id': 102, 'tipo': 'Constancia de Situación Fiscal'})
    assert 'NO ESTÁ GUARDADO' in salida
    for valor in LEIDO.values():
        assert valor not in salida or valor in ('Apodaca',)                # nada de lo leído vuelve
    assert c.pendiente.cuerpo['rfc'] == 'CAPB880202XY9'
    assert [(m, r) for m, r, _ in c.backend.llamadas] == [('GET', '/api/obtener-empleado/102'),
                                                         ('POST', '/api/escanear-documento')]
    assert ('POST', '/api/escanear-documento', {}) in c.peticiones


@pytest.mark.parametrize('ajuste,error', [
    ({'adjunto': None}, 'no ha enviado'),
    ({'permisos': ('reclutamiento',)}, 'no tiene permiso'),
])
async def test_el_agente_necesita_documento_y_permiso(ajuste, error):
    c = ctx_agente(**ajuste)
    salida = await herramientas.ejecutar(c, 'preparar_datos_de_documento',
                                         {'registro_id': 102, 'tipo': 'Constancia de Situación Fiscal'})
    assert error in salida and c.pendiente is None
    assert not any(r == '/api/escanear-documento' for _m, r, _k in c.backend.llamadas)


async def test_flujo_del_menu_con_confirmar_y_expediente():
    from bot.backend import RespuestaBackend
    ok = RespuestaBackend(200, 'application/json', datos={})
    backend = FakeBackend(rutas={**RUTAS, ('POST', '/api/actualizar-empleado/102'): ok,
                                 ('POST', '/api/employee-files'): ok})
    ctx = contexto(backend, Estados(), Settings(), archivos={'f1': b'\xff\xd8 foto'})
    chat = Chat()
    estado = ctx.application.bot_data['estados'].de(7)
    estado.seleccionado = BRUNO
    from bot.menu import pantallas
    await pantallas.al_boton_accion_empleado(pulsar(chat, 'ea:doc'), ctx)
    tipo = chat.botones()['Constancia de Situación Fiscal']
    await pantallas.al_boton_asistente(pulsar(chat, tipo), ctx)
    await principal.al_archivo(archivo(chat, 'constancia.jpg'), ctx)
    textos = [m for m, _ in chat.mensajes]
    assert '🔍 Leyendo el documento…' in textos
    resumen, _teclado = chat.ultimo()
    assert 'RFC: ****CD2 → CAPB880202XY9' in resumen
    assert not [l for l in backend.llamadas if l[0] != 'GET' and l[1] != '/api/escanear-documento']

    await principal.al_boton(pulsar(chat, next(v for v in chat.botones().values() if v.startswith('ok:'))), ctx)
    hecho, _ = chat.mensajes[-2]
    assert hecho.endswith('✅ Hecho.') and 'CAPB880202XY9' not in hecho        # en el chat queda enmascarado
    expediente, _ = chat.ultimo()
    assert expediente.startswith('📎 Guardar el documento en el expediente')
    await principal.al_boton(pulsar(chat, next(v for v in chat.botones().values() if v.startswith('ok:'))), ctx)
    escrituras = [(m, r, kw.get('headers')) for m, r, kw in backend.llamadas if m == 'POST' and 'escanear' not in r]
    assert escrituras == [('POST', '/api/actualizar-empleado/102', {'X-Origen-Telegram': 'menu'}),
                          ('POST', '/api/employee-files', {'X-Origen-Telegram': 'menu'})]
