import asyncio
import json
import logging
import sqlite3
from types import FunctionType, SimpleNamespace

import pytest

from hermes_myzap_plugin import adapter as adaptador


SERVIDORES_PROJETOS = [
    'geranet_autoescolas', 'geranet_clinica', 'geranet_igrejas', 'geranet_joias',
    'geranet_locacao', 'geranet_manutencao', 'geranet_odonto', 'geranet_oficina',
    'geranet_petshop', 'geranet_ponto', 'geranet_recorrencia', 'geranet_restaurantes',
    'geranet_salao', 'geranet_transportadora', 'geranet_vestuario', 'geranet_agentes',
    'geranet_docs', 'geranet_nfe', 'geranet_admin', 'geranet_trading', 'geranet_nfe_plugin',
]


@pytest.fixture(autouse=True)
def isolar_credenciais():
    adaptador._credenciais_mcp_por_sessao.clear()
    yield
    adaptador._credenciais_mcp_por_sessao.clear()


def criar_mensagem(servidor, credencial):
    mensagem = {
        'usuarioExternoId': credencial,
        'usuarioExternoNome': 'Pessoa autenticada',
        'contextoExterno': {
            'mcp_preferido': servidor,
            f'mcp_{servidor}_parametro_credencial': 'chave_api',
            'empresa_id': '17',
            'credencial_repetida': credencial,
            'texto': f'Contexto de atendimento: {credencial}',
            'estrutura': {'itens': [{'valor': credencial}]},
        },
    }
    if servidor == 'geranet_ponto':
        mensagem['contextoExterno']['mcp_geranet_ponto_parametros_contexto'] = {'empresa_id': 17}
    return mensagem


def test_ponto_fixa_empresa_selecionada_em_sessoes_intercaladas_e_nao_outro_projeto():
    for sessao, empresa in [('primeira-empresa', 11), ('segunda-empresa', 23)]:
        mensagem = criar_mensagem('geranet_ponto', 'credencial-pessoal-ficticia')
        mensagem['contextoExterno']['mcp_geranet_ponto_parametros_contexto'] = {'empresa_id': empresa, 'administrador': True}
        adaptador.registrar_credencial_mcp_sessao(sessao, mensagem)
    for sessao, empresa in [('segunda-empresa', '23'), ('primeira-empresa', '11')]:
        argumentos = {'empresa_id': 999, 'consulta': {'empresa_id': 999}, 'dados': {'empresa_id': 999, 'nome': 'Registro fictício'}}
        resultado = adaptador.injetar_credencial_mcp_widget(tool_name='mcp__geranet_ponto__consultar', session_id=sessao, args=argumentos)['args']
        assert resultado['empresa_id'] == resultado['consulta']['empresa_id'] == resultado['dados']['empresa_id'] == empresa
        assert 'administrador' not in resultado
        assert argumentos['empresa_id'] == argumentos['dados']['empresa_id'] == 999
        assert adaptador.injetar_credencial_mcp_widget(tool_name='mcp__geranet_salao__consultar', session_id=sessao, args=argumentos) is None
    mensagem = criar_mensagem('geranet_salao', 'credencial-ficticia-do-salao')
    mensagem['contextoExterno']['mcp_geranet_salao_parametros_contexto'] = {'empresa_id': 11}
    adaptador.registrar_credencial_mcp_sessao('outro-projeto', mensagem)
    assert 'empresa_id' not in adaptador.injetar_credencial_mcp_widget(tool_name='mcp__geranet_salao__consultar', session_id='outro-projeto')['args']


@pytest.mark.parametrize('empresa', [None, '', 0, -1, True, 'outra-empresa'])
def test_ponto_sem_empresa_valida_remove_injecao_anterior_sem_usar_argumento_do_modelo(empresa):
    mensagem = criar_mensagem('geranet_ponto', 'credencial-ficticia')
    adaptador.registrar_credencial_mcp_sessao('sessao-ponto', mensagem)
    mensagem['contextoExterno']['mcp_geranet_ponto_parametros_contexto'] = {'empresa_id': empresa}
    adaptador.registrar_credencial_mcp_sessao('sessao-ponto', mensagem)
    assert adaptador.injetar_credencial_mcp_widget(tool_name='mcp__geranet_ponto__consultar', session_id='sessao-ponto', args={'empresa_id': 17}) is None


@pytest.mark.parametrize('servidor', SERVIDORES_PROJETOS)
def test_proteger_contexto_e_injetar_credencial_entre_sessoes_intercaladas(servidor):
    primeira = criar_mensagem(servidor, 'credencial-ficticia-da-primeira-sessao')
    segunda = criar_mensagem(servidor, 'credencial-ficticia-da-segunda-sessao')
    adaptador.registrar_credencial_mcp_sessao('sessao-primeira', primeira)
    adaptador.registrar_credencial_mcp_sessao('sessao-segunda', segunda)

    for identificador, mensagem in [('sessao-segunda', segunda), ('sessao-primeira', primeira), ('sessao-segunda', segunda)]:
        contexto = adaptador.texto_contexto_externo_widget(mensagem)
        assert mensagem['usuarioExternoId'] not in contexto
        assert 'credencial protegida; injeção automática por sessão' in contexto
        assert 'empresa_id: 17' in contexto
        argumentos = {'chave_api': 'valor-forjado-pelo-modelo', 'dados': {'nome': 'Cadastro de teste'}}
        resultado = adaptador.injetar_credencial_mcp_widget(
            tool_name=f'mcp__{servidor}__consultar_dados', args=argumentos, session_id=identificador,
        )
        assert resultado['args']['chave_api'] == mensagem['usuarioExternoId']
        assert resultado['args']['dados'] == argumentos['dados']
        assert argumentos['chave_api'] == 'valor-forjado-pelo-modelo'
        assert adaptador.injetar_credencial_mcp_widget(
            tool_name='mcp__outro_projeto__consultar_dados', args=argumentos, session_id=identificador,
        ) is None


def test_preservar_contexto_legado_sem_injecao_automatica():
    mensagem = {'usuarioExternoId': 'identificador-legado', 'contextoExterno': {'empresa_id': '17'}}
    assert 'usuarioExternoId: identificador-legado' in adaptador.texto_contexto_externo_widget(mensagem)
    adaptador.registrar_credencial_mcp_sessao('sessao-legada', mensagem)
    assert adaptador.injetar_credencial_mcp_widget(tool_name='mcp__geranet_ponto__listar', session_id='sessao-legada') is None


@pytest.mark.parametrize('servidor', SERVIDORES_PROJETOS)
def test_identidade_gateway_nao_recebe_credencial_mas_injecao_e_sessao_permanecem(servidor):
    mensagem = criar_mensagem(servidor, 'credencial-ficticia-que-nao-e-identidade')
    mensagem.update({'id': 9182, 'remoteJid': 'widget_1234567890abcd', 'direcao': 'RECEBIDA', 'conteudo': '/help'})
    atendente = adaptador.MyZapAdapter(adaptador.PlatformConfig(enabled=True, extra={'api_key': 'chave-myzap-ficticia', 'text_batch_delay_seconds': 0}))
    atendente.set_owner_profile('atendente-joias')
    eventos = []

    async def capturar(evento):
        eventos.append(evento)

    atendente.handle_message = capturar
    assert asyncio.run(atendente._dispatch_if_relevant(mensagem)) is True
    assert len(eventos) == 1
    evento = eventos[0]
    assert evento.source.user_id == 'widget_1234567890abcd'
    assert mensagem['usuarioExternoId'] not in str(evento.source)
    assert mensagem['usuarioExternoId'] not in evento.channel_context
    chave = atendente._chave_agrupamento_texto(evento)
    assert chave == atendente._chave_sessao_mensagem(mensagem)
    assert chave == 'agent:atendente-joias:myzap:dm:widget_1234567890abcd'
    resultado = adaptador.injetar_credencial_mcp_widget(tool_name=f'mcp__{servidor}__consultar', session_id=chave)
    assert resultado['args']['chave_api'] == mensagem['usuarioExternoId']


def test_preservar_identidade_operacional_de_legado_e_canais_sem_widget():
    legado = {'usuarioExternoId': 'identidade-legada'}
    assert adaptador.identificador_remetente_mensagem(legado, 'widget_1234567890abcd', 'conversa') == 'identidade-legada'
    mensagem = criar_mensagem('geranet_joias', 'identidade-canal-sem-widget')
    assert adaptador.identificador_remetente_mensagem(mensagem, '5562999990000', 'conversa') == 'identidade-canal-sem-widget'


def test_widget_sem_credencial_nao_pode_forjar_identidade_operacional_em_perfil_restrito():
    mensagem = {'usuarioExternoId': 'administracao-interna-desabilitada', 'remoteJid': 'widget_1234567890abcd', 'id': 9183, 'direcao': 'RECEBIDA', 'conteudo': '/help'}
    atendente = adaptador.MyZapAdapter(adaptador.PlatformConfig(enabled=True, extra={'api_key': 'chave-ficticia', 'identificar_widget_pelo_destino': True, 'text_batch_delay_seconds': 0}))
    eventos = []

    async def capturar(evento):
        eventos.append(evento)

    atendente.handle_message = capturar
    assert asyncio.run(atendente._dispatch_if_relevant(mensagem)) is True
    assert eventos[0].source.user_id == 'widget_1234567890abcd'
    assert adaptador.configuracao_credencial_mcp_widget(mensagem) is None


def test_visitante_sem_credencial_nao_recebe_injecao_de_sessao_alheia():
    adaptador.registrar_credencial_mcp_sessao('sessao-autenticada', criar_mensagem('geranet_joias', 'credencial-ficticia-do-usuario'))
    adaptador.registrar_credencial_mcp_sessao('sessao-visitante', {'contextoExterno': {'mcp_preferido': 'geranet_joias'}})
    assert adaptador.injetar_credencial_mcp_widget(tool_name='mcp__geranet_joias__listar', session_id='sessao-visitante') is None


def test_injetar_por_sessao_viva_do_gateway_sqlite_sem_espelho_legado(tmp_path, monkeypatch):
    banco = tmp_path / 'state.db'
    with sqlite3.connect(banco) as conexao:
        conexao.execute('CREATE TABLE sessions (id TEXT PRIMARY KEY, session_key TEXT, ended_at REAL)')
        conexao.executemany('INSERT INTO sessions VALUES (?, ?, ?)', [
            ('interna-primeira', 'logica-primeira', None),
            ('interna-segunda', 'logica-segunda', None),
            ('interna-encerrada', 'logica-primeira', 1234),
            ('interna-sem-contexto', 'outra-conversa', None),
        ])
    monkeypatch.setattr(adaptador, 'caminho_registro_sessoes_hermes', lambda: tmp_path / 'sessions/sessions.json')
    adaptador.registrar_credencial_mcp_sessao('logica-primeira', criar_mensagem('geranet_joias', 'credencial-primeira'))
    adaptador.registrar_credencial_mcp_sessao('logica-segunda', criar_mensagem('geranet_joias', 'credencial-segunda'))
    for interna, credencial in [('interna-segunda', 'credencial-segunda'), ('interna-primeira', 'credencial-primeira')]:
        resultado = adaptador.injetar_credencial_mcp_widget(tool_name='mcp__geranet_joias__clientes_salvar', args={'chave_api': 'forjada'}, session_id=interna)
        assert resultado['args']['chave_api'] == credencial
    for interna in ['interna-encerrada', 'interna-sem-contexto', 'interna-inexistente']:
        assert adaptador.injetar_credencial_mcp_widget(tool_name='mcp__geranet_joias__clientes_salvar', session_id=interna) is None
    with sqlite3.connect(banco) as conexao:
        conexao.execute('UPDATE sessions SET ended_at=1234 WHERE id=?', ('interna-primeira',))
    assert adaptador.injetar_credencial_mcp_widget(tool_name='mcp__geranet_joias__clientes_salvar', session_id='interna-primeira') is None
    assert not (tmp_path / 'sessions/sessions.json').exists()


def test_sqlite_atual_nao_usa_mapa_legado_quando_sessao_foi_encerrada(tmp_path, monkeypatch):
    with sqlite3.connect(tmp_path / 'state.db') as conexao:
        conexao.execute('CREATE TABLE sessions (id TEXT PRIMARY KEY, session_key TEXT, ended_at REAL)')
        conexao.execute('INSERT INTO sessions VALUES (?, ?, ?)', ('interna', 'logica', 1234))
    registro = tmp_path / 'sessions/sessions.json'
    registro.parent.mkdir()
    registro.write_text(json.dumps({'logica': {'session_id': 'interna'}}))
    monkeypatch.setattr(adaptador, 'caminho_registro_sessoes_hermes', lambda: registro)
    adaptador.registrar_credencial_mcp_sessao('logica', criar_mensagem('geranet_joias', 'credencial-encerrada'))
    assert adaptador.injetar_credencial_mcp_widget(tool_name='mcp__geranet_joias__listar', session_id='interna') is None


def test_mensagem_usa_namespace_do_perfil_proprietario_antes_de_entrar_no_gateway(tmp_path, monkeypatch):
    banco = tmp_path / 'state.db'
    chave_gateway = 'agent:atendente-joias:myzap:dm:widget_1234567890abcd'
    with sqlite3.connect(banco) as conexao:
        conexao.execute('CREATE TABLE sessions (id TEXT PRIMARY KEY, session_key TEXT, ended_at REAL)')
        conexao.execute('INSERT INTO sessions VALUES (?, ?, ?)', ('sessao-interna-gateway', chave_gateway, None))
    monkeypatch.setattr(adaptador, 'caminho_registro_sessoes_hermes', lambda: tmp_path / 'sessions/sessions.json')
    mensagem = criar_mensagem('geranet_joias', 'credencial-ficticia-do-roteamento')
    mensagem['remoteJid'] = 'widget_1234567890abcd'
    atendente = adaptador.MyZapAdapter(adaptador.PlatformConfig(enabled=True, extra={'api_key': 'chave-myzap-ficticia'}))
    atendente.set_owner_profile('atendente-joias')
    chave_registrada = atendente._chave_sessao_mensagem(mensagem)
    assert chave_registrada == chave_gateway
    adaptador.registrar_credencial_mcp_sessao(chave_registrada, mensagem)
    resultado = adaptador.injetar_credencial_mcp_widget(tool_name='mcp__geranet_joias__clientes_salvar', session_id='sessao-interna-gateway')
    assert resultado['args']['chave_api'] == mensagem['usuarioExternoId']
    outro_atendente = adaptador.MyZapAdapter(adaptador.PlatformConfig(enabled=True, extra={'api_key': 'outra-chave-myzap-ficticia'}))
    outro_atendente.set_owner_profile('atendente-petshop')
    assert outro_atendente._chave_sessao_mensagem(mensagem) != chave_gateway


def test_diagnostico_opcional_nao_registra_credencial_nem_argumentos(tmp_path, monkeypatch, caplog):
    monkeypatch.setattr(adaptador, '__file__', str(tmp_path / 'adapter.py'))
    (tmp_path / 'diagnostico-mcp.ativo').touch()
    caplog.set_level(logging.INFO, logger=adaptador.logger.name)
    credencial = 'credencial-ficticia-protegida-no-diagnostico'
    adaptador.registrar_credencial_mcp_sessao('logica-diagnostico', criar_mensagem('geranet_joias', credencial))
    adaptador.injetar_credencial_mcp_widget(tool_name='mcp__geranet_joias__listar', args={'dado_privado': 'nao-registar-argumento'}, session_id='logica-diagnostico')
    assert 'MCP contexto' in caplog.text and 'MCP middleware' in caplog.text
    assert credencial not in caplog.text and 'nao-registar-argumento' not in caplog.text


def test_prova_runtime_reflete_metodos_da_instancia_e_nao_apenas_arquivo_em_disco(tmp_path, monkeypatch):
    monkeypatch.setattr(adaptador, 'caminho_registro_sessoes_hermes', lambda: tmp_path / 'sessions/sessions.json')
    atendente = adaptador.MyZapAdapter(adaptador.PlatformConfig(enabled=True, extra={'api_key': 'chave-myzap-ficticia-protegida'}))
    async def despacho_de_outra_origem(self, mensagem):
        return False
    monkeypatch.setattr(adaptador.MyZapAdapter, '_dispatch_if_relevant', despacho_de_outra_origem)
    prova = atendente._registrar_origem_conector()
    persistido = json.loads((tmp_path / 'runtime/myzap-conector-carregado.json').read_text())
    assert persistido == prova
    assert prova['origem_conexao'] == type(atendente).connect.__code__.co_filename
    assert prova['origem_despacho'] == despacho_de_outra_origem.__code__.co_filename
    assert prova['origem_despacho'] != adaptador.__file__
    assert 'chave-myzap-ficticia-protegida' not in json.dumps(prova)
    assert prova['identidade_widget_pelo_destino'] is False
    assert prova['comandos_administrativos_restritos'] is False


def test_prova_runtime_verifica_politica_real_carregada_no_adaptador(tmp_path, monkeypatch):
    monkeypatch.setattr(adaptador, 'caminho_registro_sessoes_hermes', lambda: tmp_path / 'sessions/sessions.json')
    configuracao = {'api_key': 'chave-ficticia', 'identificar_widget_pelo_destino': True, 'allow_admin_from': ['administracao-interna-desabilitada'], 'user_allowed_commands': [], 'group_allow_admin_from': ['administracao-interna-desabilitada'], 'group_user_allowed_commands': []}
    atendente = adaptador.MyZapAdapter(adaptador.PlatformConfig(enabled=True, extra=configuracao))
    assert atendente._registrar_origem_conector()['comandos_administrativos_restritos'] is True
    assert atendente._registrar_origem_conector()['identidade_widget_pelo_destino'] is True
    atendente.config.extra['user_allowed_commands'] = ['exec']
    assert atendente._registrar_origem_conector()['comandos_administrativos_restritos'] is False


def test_impressao_compilada_independe_do_caminho_e_distingue_classe_antiga_no_mesmo_caminho(tmp_path, monkeypatch):
    primeira = compile('def consultar():\n return 11\n', '/primeiro/adapter.py', 'exec')
    segunda = compile('\n\ndef consultar():\n return 11\n', '/segundo/adapter.py', 'exec')
    primeira = FunctionType(next(valor for valor in primeira.co_consts if hasattr(valor, 'co_code')), {})
    segunda = FunctionType(next(valor for valor in segunda.co_consts if hasattr(valor, 'co_code')), {})
    assert adaptador.impressao_codigo_funcao(primeira) == adaptador.impressao_codigo_funcao(segunda)
    monkeypatch.setattr(adaptador, 'caminho_registro_sessoes_hermes', lambda: tmp_path / 'sessions/sessions.json')
    original = adaptador.MyZapAdapter._dispatch_if_relevant
    codigo_antigo = compile('async def _dispatch_if_relevant(self, mensagem):\n return False\n', original.__code__.co_filename, 'exec')
    despacho_antigo = FunctionType(next(valor for valor in codigo_antigo.co_consts if hasattr(valor, 'co_code')), original.__globals__)
    monkeypatch.setattr(adaptador.MyZapAdapter, '_dispatch_if_relevant', despacho_antigo)
    atendente = adaptador.MyZapAdapter(adaptador.PlatformConfig(enabled=True, extra={'api_key': 'chave-ficticia'}), interceptador_registrado=adaptador.injetar_credencial_mcp_widget)
    prova = atendente._registrar_origem_conector()
    assert prova['origem_despacho'] == original.__code__.co_filename
    assert prova['funcoes_compiladas']['_dispatch_if_relevant'] != adaptador.impressao_codigo_funcao(original)


def test_prova_usa_interceptador_real_capturado_no_registro_e_nao_global_do_connect(tmp_path, monkeypatch):
    monkeypatch.setattr(adaptador, 'caminho_registro_sessoes_hermes', lambda: tmp_path / 'sessions/sessions.json')
    registro = {}
    contexto = SimpleNamespace(register_middleware=lambda evento, funcao: registro.update(interceptador=funcao), register_platform=lambda **parametros: registro.update(plataforma=parametros))
    adaptador.register(contexto)
    interceptador_real = registro['interceptador']

    def interceptador_de_outro_registro(**parametros):
        return None

    monkeypatch.setattr(adaptador, 'injetar_credencial_mcp_widget', interceptador_de_outro_registro)
    atendente = registro['plataforma']['adapter_factory'](adaptador.PlatformConfig(enabled=True, extra={'api_key': 'chave-ficticia'}))
    prova = atendente._registrar_origem_conector()
    assert prova['middleware_registrado_confirmado'] is True
    assert prova['origem_middleware'] == interceptador_real.__code__.co_filename
    assert prova['funcoes_compiladas']['injetar_credencial_mcp_widget'] == adaptador.impressao_codigo_funcao(interceptador_real)
    assert prova['funcoes_compiladas']['injetar_credencial_mcp_widget'] != adaptador.impressao_codigo_funcao(interceptador_de_outro_registro)


def test_prova_recusa_cache_de_outro_modulo_mesmo_com_bytecode_identico(tmp_path, monkeypatch):
    monkeypatch.setattr(adaptador, 'caminho_registro_sessoes_hermes', lambda: tmp_path / 'sessions/sessions.json')
    interceptador = adaptador.injetar_credencial_mcp_widget
    outro_modulo = {**interceptador.__globals__, '_credenciais_mcp_por_sessao': {}}
    outro_interceptador = FunctionType(interceptador.__code__, outro_modulo)
    atendente = adaptador.MyZapAdapter(adaptador.PlatformConfig(enabled=True, extra={'api_key': 'chave-ficticia'}), interceptador_registrado=outro_interceptador)
    prova = atendente._registrar_origem_conector()
    assert adaptador.impressao_codigo_funcao(outro_interceptador) == adaptador.impressao_codigo_funcao(interceptador)
    assert prova['middleware_registrado_confirmado'] is True
    assert prova['registro_middleware_mesmo_cache'] is False


def test_impressao_compilada_representa_fatias_constantes_do_python_314():
    def consultar():
        return None

    primeira = FunctionType(consultar.__code__.replace(co_consts=consultar.__code__.co_consts + (slice(1, 7, None),)), {})
    segunda = FunctionType(consultar.__code__.replace(co_consts=consultar.__code__.co_consts + (slice(2, 7, None),)), {})
    estrutura = adaptador.estrutura_codigo_executavel(primeira.__code__)
    assert estrutura['constantes'][-1] == {'fatia': [1, 7, None]}
    assert adaptador.impressao_codigo_funcao(primeira) != adaptador.impressao_codigo_funcao(segunda)


def test_redigir_credencial_antes_de_limitar_texto():
    credencial = 'credencial-ficticia-que-nao-pode-ser-truncada'
    mensagem = criar_mensagem('geranet_joias', credencial)
    mensagem['contextoExterno']['texto'] = 'a' * 1990 + credencial
    contexto = adaptador.texto_contexto_externo_widget(mensagem)
    assert credencial not in contexto
    assert 'a' * 1990 + 'credencial' not in contexto


@pytest.mark.parametrize('servidor', ['geranet_petshop', 'geranet_recorrencia'])
def test_fixar_empresa_e_unidade_do_contexto_sem_aceitar_outros_parametros(servidor):
    mensagem = criar_mensagem(servidor, 'credencial-ficticia-do-usuario')
    mensagem['contextoExterno'][f'mcp_{servidor}_parametros_contexto'] = {
        'empresa_codigo': 'EMPRESA-17', 'unidade_codigo': 'UNIDADE-23', 'administrador': True, 'caminho': '/api/admin',
    }
    adaptador.registrar_credencial_mcp_sessao('sessao-empresa', mensagem)
    argumentos = {'empresa_codigo': 'EMPRESA-INDEVIDA', 'unidade_codigo': 'UNIDADE-INDEVIDA', 'consulta': {'empresa_codigo': 'EMPRESA-INDEVIDA'}, 'dados': {'empresa_codigo': 'EMPRESA-INDEVIDA', 'nome': 'Cadastro'}}
    resultado = adaptador.injetar_credencial_mcp_widget(tool_name=f'mcp__{servidor}__consultar', args=argumentos, session_id='sessao-empresa')['args']
    assert resultado['empresa_codigo'] == 'EMPRESA-17'
    assert resultado['unidade_codigo'] == 'UNIDADE-23'
    assert resultado['consulta']['empresa_codigo'] == 'EMPRESA-17'
    assert resultado['dados']['empresa_codigo'] == 'EMPRESA-17'
    assert resultado['dados']['nome'] == 'Cadastro'
    assert 'administrador' not in resultado
    assert 'caminho' not in resultado
    assert argumentos['consulta']['empresa_codigo'] == 'EMPRESA-INDEVIDA'


def test_atualizar_escopo_e_revogar_alias_interno_apos_troca_de_contexto(monkeypatch, tmp_path):
    registros = tmp_path / 'sessoes.json'
    registros.write_text(json.dumps({'sessao-logica': {'session_id': 'identificador-interno'}}), encoding='utf-8')
    monkeypatch.setattr(adaptador, 'caminho_registro_sessoes_hermes', lambda: registros)
    mensagem = criar_mensagem('geranet_recorrencia', 'credencial-ficticia-do-usuario')
    mensagem['contextoExterno']['mcp_geranet_recorrencia_parametros_contexto'] = {'empresa_codigo': 'EMPRESA-17'}
    adaptador.registrar_credencial_mcp_sessao('sessao-logica', mensagem)
    primeira = adaptador.injetar_credencial_mcp_widget(tool_name='mcp__geranet_recorrencia__listar', session_id='identificador-interno')['args']
    assert primeira['empresa_codigo'] == 'EMPRESA-17'
    mensagem['contextoExterno']['mcp_geranet_recorrencia_parametros_contexto'] = {'empresa_codigo': 'EMPRESA-28'}
    adaptador.registrar_credencial_mcp_sessao('sessao-logica', mensagem)
    segunda = adaptador.injetar_credencial_mcp_widget(tool_name='mcp__geranet_recorrencia__listar', session_id='identificador-interno')['args']
    assert segunda['empresa_codigo'] == 'EMPRESA-28'
    adaptador.registrar_credencial_mcp_sessao('sessao-logica', {'contextoExterno': {'origem': 'landing_publica'}})
    assert adaptador.injetar_credencial_mcp_widget(tool_name='mcp__geranet_recorrencia__listar', session_id='identificador-interno') is None


def test_isolar_empresas_entre_sessoes_do_mesmo_usuario():
    for sessao, empresa in [('sessao-empresa-a', 'EMPRESA-A'), ('sessao-empresa-b', 'EMPRESA-B')]:
        mensagem = criar_mensagem('geranet_petshop', 'mesma-credencial-ficticia')
        mensagem['contextoExterno']['mcp_geranet_petshop_parametros_contexto'] = {'empresa_codigo': empresa}
        adaptador.registrar_credencial_mcp_sessao(sessao, mensagem)
    for sessao, empresa in [('sessao-empresa-b', 'EMPRESA-B'), ('sessao-empresa-a', 'EMPRESA-A')]:
        resultado = adaptador.injetar_credencial_mcp_widget(tool_name='mcp__geranet_petshop__listar', session_id=sessao)['args']
        assert resultado['empresa_codigo'] == empresa
