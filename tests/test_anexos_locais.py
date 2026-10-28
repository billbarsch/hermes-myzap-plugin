import asyncio
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest

from hermes_myzap_plugin.adapter import MyZapAdapter, PlatformConfig


@pytest.fixture
def armazenamento_hermes(monkeypatch, tmp_path):
    modulo = ModuleType('gateway.platforms.base')
    chamadas = []

    def armazenar(conteudo, *, filename, mime_type, default_kind):
        arquivo = tmp_path / Path(filename).name
        arquivo.write_bytes(conteudo)
        return SimpleNamespace(path=str(arquivo))

    async def baixar(endereco, nome, tentativas, **opcoes):
        chamadas.append((endereco, nome, tentativas, opcoes['media_type']))
        return opcoes['cache_fn'](b'conteudo autorizado de teste', nome)

    modulo._cache_media_from_url = baixar
    modulo.cache_media_bytes = armazenar
    monkeypatch.setitem(sys.modules, 'gateway.platforms.base', modulo)
    monkeypatch.setenv('HERMES_HOME', str(tmp_path / 'perfil'))
    monkeypatch.setenv('HERMES_PROFILE', 'perfil-teste')
    return modulo, chamadas


@pytest.mark.parametrize('nome,tipo,tipo_midia', [
    ('nota.xml', 'documento', 'text/xml'),
    ('nota.pdf', 'documento', 'application/pdf'),
    ('imagem.jpg', 'imagem', 'image/jpeg'),
    ('video.mp4', 'video', 'video/mp4'),
])
def test_anexos_chegam_ao_ingresso_como_arquivos_locais_regulares(armazenamento_hermes, nome, tipo, tipo_midia):
    async def executar():
        atendente = MyZapAdapter(PlatformConfig(enabled=True, extra={'api_key': 'CHAVE_FICTICIA'}))
        mensagem = {'conteudo': 'Confira o anexo', 'arquivos': [{'nome': nome, 'tipo': tipo,
            'mimeType': tipo_midia, 'url': 'https://api.myzap.net/api/arquivos-publicos/ficticio'}]}
        resultado = await atendente._prepare_inbound_message(mensagem)
        assert resultado['text'] == 'Confira o anexo'
        assert resultado['media_types'] == [tipo_midia]
        assert len(resultado['media_urls']) == 1
        arquivo = Path(resultado['media_urls'][0])
        assert arquivo.is_absolute() and arquivo.is_file()
        assert arquivo.read_bytes() == b'conteudo autorizado de teste'
        assert armazenamento_hermes[1][0][2] == 2

    asyncio.run(executar())


def test_falha_de_download_nao_envia_url_como_caminho_local(armazenamento_hermes):
    async def falhar(*argumentos, **opcoes):
        raise ValueError('Arquivo bloqueado pelo limite ou protecao SSRF')

    armazenamento_hermes[0]._cache_media_from_url = falhar
    atendente = MyZapAdapter(PlatformConfig(enabled=True, extra={'api_key': 'CHAVE_FICTICIA'}))
    resultado = asyncio.run(atendente._prepare_inbound_message({'conteudo': 'Confira', 'arquivos': [
        {'nome': 'nota.xml', 'url': 'https://api.myzap.net/api/arquivos-publicos/ficticio'}]}))
    assert resultado['media_urls'] == resultado['media_types'] == []
    assert 'solicite o reenvio' in resultado['text']


def test_endereco_nao_http_nao_dispara_download(armazenamento_hermes):
    atendente = MyZapAdapter(PlatformConfig(enabled=True, extra={'api_key': 'CHAVE_FICTICIA'}))
    resultado = asyncio.run(atendente._armazenar_anexo_recebido({'nome': 'nota.xml', 'url': 'file:///segredo'}))
    assert resultado == ''
    assert armazenamento_hermes[1] == []
