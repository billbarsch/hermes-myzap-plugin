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


def test_redigir_credencial_antes_de_limitar_texto():
    credencial = 'credencial-ficticia-que-nao-pode-ser-truncada'
    mensagem = criar_mensagem('geranet_joias', credencial)
    mensagem['contextoExterno']['texto'] = 'a' * 1990 + credencial
    contexto = adaptador.texto_contexto_externo_widget(mensagem)
    assert credencial not in contexto
    assert 'a' * 1990 + 'credencial' not in contexto
