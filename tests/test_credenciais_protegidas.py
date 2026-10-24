import json
import logging
import sqlite3

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
    return {
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
