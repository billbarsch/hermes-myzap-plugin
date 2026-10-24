import sys
from types import ModuleType

import pytest

from hermes_myzap_plugin import adapter as adaptador


@pytest.fixture
def configuracao_hermes(monkeypatch):
    pacote = ModuleType('hermes_cli')
    pacote.__path__ = []
    configuracao = ModuleType('hermes_cli.config')
    configuracao.get_env_value = lambda nome: None
    pacote.config = configuracao
    monkeypatch.setitem(sys.modules, 'hermes_cli', pacote)
    monkeypatch.setitem(sys.modules, 'hermes_cli.config', configuracao)
    return configuracao


def test_nao_herdar_chave_do_processo_se_escopo_nao_possui_chave(monkeypatch, configuracao_hermes):
    monkeypatch.setenv('MYZAP_API_KEY', 'chave-do-perfil-principal')
    assert adaptador._env('MYZAP_API_KEY') == ''
    assert adaptador._env('MYZAP_API_KEY', 'padrao') == 'padrao'


def test_estado_usa_diretorio_do_perfil_no_contexto(monkeypatch, tmp_path):
    constantes = ModuleType('hermes_constants')
    diretorio_perfil = tmp_path / 'profiles' / 'atendente'
    constantes.get_hermes_home = lambda: diretorio_perfil
    monkeypatch.setitem(sys.modules, 'hermes_constants', constantes)
    monkeypatch.setenv('HERMES_HOME', str(tmp_path))
    assert adaptador._env('HERMES_HOME') == str(diretorio_perfil)
    assert adaptador._state_path_from() == diretorio_perfil / 'runtime' / adaptador.DEFAULT_STATE_FILENAME


def test_nao_ocultar_falha_de_configuracao_com_chave_do_processo(monkeypatch, configuracao_hermes):
    def falhar(nome):
        raise RuntimeError('Escopo indisponivel')

    configuracao_hermes.get_env_value = falhar
    monkeypatch.setenv('MYZAP_API_KEY', 'chave-do-perfil-principal')
    with pytest.raises(RuntimeError, match='Escopo indisponivel'):
        adaptador._env('MYZAP_API_KEY')
