import json

import httpx
import pytest

from bot import llm


def test_ida_y_vuelta_responses():
    mensajes = [
        {'role': 'system', 'content': 'reglas'},
        {'role': 'developer', 'content': 'contexto'},
        {'role': 'user', 'content': 'hola'},
        {'role': 'assistant', 'content': '', 'tool_calls': [
            {'id': 'c1', 'type': 'function', 'function': {'name': 'buscar_empleado', 'arguments': '{"texto":"ana"}'}}]},
        {'role': 'tool', 'tool_call_id': 'c1', 'content': 'datos'},
    ]
    herramientas = [{'type': 'function', 'function': {'name': 'buscar_empleado', 'description': 'd', 'parameters': {}}}]
    instrucciones, entrada, tools = llm.a_entrada_responses(mensajes, herramientas)
    assert instrucciones == 'reglas'
    assert entrada[0] == {'role': 'developer', 'content': 'contexto'}
    assert entrada[2] == {'type': 'function_call', 'call_id': 'c1', 'name': 'buscar_empleado', 'arguments': '{"texto":"ana"}'}
    assert entrada[3] == {'type': 'function_call_output', 'call_id': 'c1', 'output': 'datos'}
    assert tools[0]['name'] == 'buscar_empleado' and tools[0]['strict'] is False


def test_interpretar_responses():
    cuerpo = {'output': [
        {'type': 'function_call', 'call_id': 'x', 'name': 'ver_empleado', 'arguments': '{"registro_id": 3}'},
        {'type': 'message', 'content': [{'type': 'output_text', 'text': 'Listo'}]},
    ], 'usage': {'input_tokens': 100, 'output_tokens': 20, 'input_tokens_details': {'cached_tokens': 64}}}
    r = llm.interpretar_responses(cuerpo, 'gpt-4o-mini')
    assert r.contenido == 'Listo'
    assert r.llamadas[0].argumentos == {'registro_id': 3}
    assert (r.tokens_entrada, r.tokens_salida, r.tokens_cache) == (100, 20, 64)


def test_argumentos_rotos():
    with pytest.raises(llm.ErrorLLM):
        llm.interpretar_argumentos('{no json')
    with pytest.raises(llm.ErrorLLM):
        llm.interpretar_argumentos('[1]')


def test_temperatura_solo_en_modelos_que_la_aceptan():
    assert llm.admite_temperatura('gpt-4o-mini') and not llm.admite_temperatura('gpt-5-mini')
    assert not llm.admite_temperatura('o4-mini')


async def test_cliente_manda_store_false_y_clave_de_cache():
    enviado = {}

    def manejar(request):
        enviado.update(json.loads(request.content))
        enviado['auth'] = request.headers['authorization']
        return httpx.Response(200, json={'output': [{'type': 'message', 'content': [{'type': 'output_text', 'text': 'ok'}]}]})

    async with httpx.AsyncClient(transport=httpx.MockTransport(manejar)) as http:
        cliente = llm.ClienteOpenAI(api_key='sk-x', modelo='gpt-4o-mini', base_url='https://api.test/v1', http=http)
        r = await cliente.completar([{'role': 'user', 'content': 'hola'}], clave_cache=llm.clave_de_cache(5))
    assert r.contenido == 'ok'
    assert enviado['store'] is False and enviado['prompt_cache_key'].startswith('rh-') and '5' != enviado['prompt_cache_key']
    assert enviado['auth'] == 'Bearer sk-x'


async def test_error_http_es_errorllm():
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(429, text='rate'))) as http:
        cliente = llm.ClienteOpenAI(api_key='k', modelo='gpt-4o-mini', base_url='https://api.test/v1', http=http)
        with pytest.raises(llm.ErrorLLM):
            await cliente.completar([{'role': 'user', 'content': 'x'}])
