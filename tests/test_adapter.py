import asyncio
import json
from datetime import datetime, timezone

import pytest

import hermes_myzap_plugin.adapter as adapter_module
from hermes_myzap_plugin.adapter import (
    MyZapAdapter,
    PlatformConfig,
    check_requirements,
    extract_messages,
    iso_utc,
    is_filtered_runtime_status,
    is_public_operational_notice,
    is_widget_destination,
    message_destination,
    message_attachments,
    message_text,
    normalize_number,
    texto_contexto_externo_widget,
    usuario_externo_id,
    verify_webhook_signature,
)


@pytest.fixture(autouse=True)
def isolated_hermes_home(monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "hermes-home"))


def test_profile_guard_blocks_wrong_profile(monkeypatch):
    monkeypatch.setenv("MYZAP_API_KEY", "x")
    monkeypatch.setenv("HERMES_PROFILE", "atendimento")
    monkeypatch.setenv("MYZAP_HERMES_PROFILE", "suporte")
    assert check_requirements() is False


def test_profile_guard_allows_any_profile_by_default(monkeypatch):
    monkeypatch.setenv("MYZAP_API_KEY", "x")
    monkeypatch.setenv("HERMES_PROFILE", "atendimento")
    assert check_requirements() is True


def test_profile_guard_allows_configured_profile(monkeypatch):
    monkeypatch.setenv("MYZAP_API_KEY", "x")
    monkeypatch.setenv("HERMES_PROFILE", "suporte")
    monkeypatch.setenv("MYZAP_HERMES_PROFILE", "suporte")
    assert check_requirements() is True


def test_normalize_number():
    assert normalize_number("+55 (62) 99999-0000@s.whatsapp.net") == "5562999990000"


def test_message_destination_preserves_widget_remote_jid():
    msg = {"remoteJid": "widget_abc123def45678", "conversaId": 7}
    assert message_destination(msg) == "widget_abc123def45678"


def test_widget_destination_requires_exact_hash_shape():
    assert is_widget_destination("widget_abc123def45678") is True
    assert is_widget_destination("widget_abc123def456") is False
    assert is_widget_destination("widget_ABC123DEF45678") is False
    assert is_widget_destination("widget_abc123def4567890") is False


def test_message_destination_normalizes_whatsapp_jid():
    msg = {"remoteJid": "+55 (62) 99999-0000@s.whatsapp.net"}
    assert message_destination(msg) == "5562999990000"


def test_iso_utc_milliseconds():
    dt = datetime(2026, 5, 31, 12, 0, 0, tzinfo=timezone.utc)
    assert iso_utc(dt) == "2026-05-31T12:00:00.000Z"


def test_extract_messages_nested():
    assert extract_messages({"data": {"mensagens": [{"id": 1}]}}) == [{"id": 1}]


def test_message_text_gera_resumo_para_anexo_sem_texto():
    mensagem = {
        "id": 2,
        "direcao": "RECEBIDA",
        "conteudo": "",
        "arquivos": [
            {
                "nome": "audio.webm",
                "tipo": "audio",
                "mimeType": "audio/webm"
            }
        ]
    }
    assert "áudio" in message_text(mensagem).lower()
    assert message_attachments(mensagem)[0]["mimeType"] == "audio/webm"


def test_verify_webhook_signature():
    secret = "s3"
    body = b'{"ok":true}'
    import hmac, hashlib
    sig = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    assert verify_webhook_signature(secret, body, sig) is True
    assert verify_webhook_signature(secret, body, "sha256=bad") is False


class FakeResponse:
    def __init__(self, status_code=200, payload=None, text="", content=b""):
        self.status_code = status_code
        self._payload = payload or {}
        self.text = text or json.dumps(self._payload)
        self.content = content or self.text.encode("utf-8")
        self.headers = {}

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class FakeClient:
    def __init__(self, payload):
        self.payload = payload
        self.get_calls = []
        self.posts = []

    async def get(self, url, params=None, headers=None, **kwargs):
        self.get_calls.append((url, dict(params or {}), headers, kwargs))
        return FakeResponse(payload=self.payload)

    async def post(self, url, json=None, data=None, files=None, headers=None, **kwargs):
        self.posts.append({"url": url, "json": json, "data": data, "files": files, "headers": headers, "kwargs": kwargs})
        return FakeResponse(status_code=201, payload={"messageId": "sent-1"})


class SequenceClient(FakeClient):
    def __init__(self, responses):
        super().__init__({})
        self.responses = list(responses)

    async def get(self, url, params=None, headers=None, **kwargs):
        self.get_calls.append((url, dict(params or {}), headers, kwargs))
        return self.responses.pop(0)


def test_poll_state_survives_restart_and_blocks_replay(monkeypatch, tmp_path):
    async def run():
        monkeypatch.setenv("HERMES_PROFILE", "atendimento")
        state_path = tmp_path / "myzap-state.json"
        payload = {
            "mensagens": [
                {"id": 100, "direcao": "RECEBIDA", "conteudo": "Oi", "remoteJid": "widget_abc123def45678", "conversaId": 8, "criadoEm": "2026-05-31T12:00:00.000Z"},
            ],
            "meta": {"nextCursor": "2026-05-31T12:00:00.000Z,100"},
        }
        cfg = PlatformConfig(enabled=True, extra={"base_url": "https://example.test/api/v1", "api_key": "key", "state_path": str(state_path)})

        first = MyZapAdapter(cfg)
        first._http_client = FakeClient(payload)
        first_events = []
        first.handle_message = lambda event: first_events.append(event) or asyncio.sleep(0)
        assert await first.poll_once() == 1
        assert len(first_events) == 1

        restarted = MyZapAdapter(cfg)
        restarted._http_client = FakeClient(payload)
        restarted_events = []
        restarted.handle_message = lambda event: restarted_events.append(event) or asyncio.sleep(0)
        assert await restarted.poll_once() == 0
        assert restarted_events == []

    asyncio.run(run())


def test_poll_once_clears_rejected_cursor_and_retries(monkeypatch, tmp_path):
    async def run():
        monkeypatch.setenv("HERMES_PROFILE", "atendimento")
        state_path = tmp_path / "myzap-state.json"
        state_path.write_text(json.dumps({"cursor": "1081", "since": "2026-05-31T12:00:00.000Z", "seen": []}), encoding="utf-8")
        cfg = PlatformConfig(enabled=True, extra={"base_url": "https://example.test/api/v1", "api_key": "key", "state_path": str(state_path)})
        adapter = MyZapAdapter(cfg)
        adapter._http_client = SequenceClient([
            FakeResponse(status_code=400, payload={"erro": "CURSOR_INVALIDO"}, text="CURSOR_INVALIDO"),
            FakeResponse(payload={"mensagens": [], "meta": {"nextCursor": "2026-05-31T12:00:01.000Z,1082"}}),
        ])
        assert await adapter.poll_once() == 0
        assert "cursor" in adapter._http_client.get_calls[0][1]
        assert "cursor" not in adapter._http_client.get_calls[1][1]
        assert adapter._cursor == "2026-05-31T12:00:01.000Z,1082"

    asyncio.run(run())


def test_poll_once_dispatches_inbound_text(monkeypatch):
    async def run():
        monkeypatch.setenv("HERMES_PROFILE", "atendimento")
        cfg = PlatformConfig(enabled=True, extra={"base_url": "https://example.test/api/v1", "api_key": "key"})
        a = MyZapAdapter(cfg)
        fake = FakeClient({
            "mensagens": [
                {"id": 10, "direcao": "RECEBIDA", "conteudo": "Oi", "numero": "+55 62 99999-0000", "conversaId": 7, "criadoEm": "2026-05-31T12:00:00.000Z"},
                {"id": 11, "direcao": "ENVIADA", "conteudo": "eco", "numero": "+55 62 99999-0000", "conversaId": 7, "criadoEm": "2026-05-31T12:00:01.000Z"},
            ]
        })
        a._http_client = fake
        events = []

        async def capture(event):
            events.append(event)

        a.handle_message = capture
        count = await a.poll_once()
        assert count == 1
        assert events[0].text == "Oi"
        assert events[0].source.chat_id == "7"
        assert events[0].source.user_id == "5562999990000"

    asyncio.run(run())


def test_poll_once_dispatches_inbound_audio_without_text(monkeypatch):
    async def run():
        monkeypatch.setenv("HERMES_PROFILE", "atendimento")
        cfg = PlatformConfig(enabled=True, extra={"base_url": "https://example.test/api/v1", "api_key": "key"})
        a = MyZapAdapter(cfg)
        fake = FakeClient({
            "mensagens": [
                {
                    "id": 12,
                    "direcao": "RECEBIDA",
                    "conteudo": "",
                    "remoteJid": "widget_abc123def45678",
                    "conversaId": 8,
                    "criadoEm": "2026-05-31T12:00:00.000Z",
                    "arquivos": [
                        {
                            "id": 501,
                            "nome": "nota.mp3",
                            "tipo": "audio",
                            "mimeType": "audio/mpeg",
                            "url": "https://storage/nota.mp3"
                        }
                    ]
                }
            ]
        })
        a._http_client = fake
        events = []

        async def capture(event):
            events.append(event)

        a.handle_message = capture
        count = await a.poll_once()
        assert count == 1
        assert events[0].text == "(The user sent a message with no text content)"
        assert events[0].message_type.name == "VOICE"
        assert events[0].media_urls == ["https://storage/nota.mp3"]
        assert events[0].media_types == ["audio/mpeg"]
        assert events[0].source.chat_id == "widget_abc123def45678"
        assert events[0].raw_message["arquivos"][0]["mimeType"] == "audio/mpeg"

    asyncio.run(run())


def test_poll_once_dispatches_inbound_audio_for_hermes_transcription(monkeypatch):
    async def run():
        monkeypatch.setenv("HERMES_PROFILE", "atendimento")
        cache_calls = []

        async def fake_cache_audio_from_url(url, ext=".ogg", retries=2):
            cache_calls.append({"url": url, "ext": ext, "retries": retries})
            return "/tmp/hermes/audio/audio_123.webm"

        monkeypatch.setattr(adapter_module, "cache_audio_from_url", fake_cache_audio_from_url)
        cfg = PlatformConfig(enabled=True, extra={"base_url": "https://example.test/api/v1", "api_key": "key"})
        a = MyZapAdapter(cfg)
        fake = FakeClient({
            "mensagens": [
                {
                    "id": 13,
                    "direcao": "RECEBIDA",
                    "conteudo": "🎤 Áudio enviado: audio-123.webm",
                    "remoteJid": "widget_abc123def45678",
                    "conversaId": 8,
                    "criadoEm": "2026-05-31T12:00:00.000Z",
                    "arquivos": [
                        {
                            "id": 502,
                            "nome": "audio-123.webm",
                            "tipo": "audio",
                            "mimeType": "audio/webm",
                            "url": "https://storage/audio.webm"
                        }
                    ]
                }
            ]
        })
        a._http_client = fake
        events = []

        async def capture(event):
            events.append(event)

        a.handle_message = capture
        count = await a.poll_once()
        assert count == 1
        assert events[0].text == "(The user sent a message with no text content)"
        assert events[0].message_type.name == "VOICE"
        assert events[0].media_urls == ["/tmp/hermes/audio/audio_123.webm"]
        assert events[0].media_types == ["audio/webm"]
        assert cache_calls == [{"url": "https://storage/audio.webm", "ext": ".webm", "retries": 2}]
        assert fake.posts == []

    asyncio.run(run())


def test_poll_once_dispatches_inbound_image_media(monkeypatch):
    async def run():
        monkeypatch.setenv("HERMES_PROFILE", "atendimento")
        cfg = PlatformConfig(enabled=True, extra={"base_url": "https://example.test/api/v1", "api_key": "key"})
        a = MyZapAdapter(cfg)
        fake = FakeClient({
            "mensagens": [
                {
                    "id": 14,
                    "direcao": "RECEBIDA",
                    "conteudo": "Comprovante",
                    "remoteJid": "widget_abc123def45678",
                    "conversaId": 8,
                    "criadoEm": "2026-05-31T12:00:00.000Z",
                    "arquivos": [
                        {
                            "id": 503,
                            "nome": "foto.jpg",
                            "tipo": "imagem",
                            "mimeType": "image/jpeg",
                            "url": "https://storage/foto.jpg"
                        }
                    ]
                }
            ]
        })
        a._http_client = fake
        events = []

        async def capture(event):
            events.append(event)

        a.handle_message = capture
        count = await a.poll_once()
        assert count == 1
        assert events[0].text == "Comprovante"
        assert events[0].media_urls == ["https://storage/foto.jpg"]
        assert events[0].media_types == ["image/jpeg"]

    asyncio.run(run())


def test_poll_once_dispatches_widget_inbound_with_replyable_chat_id(monkeypatch):
    async def run():
        monkeypatch.setenv("HERMES_PROFILE", "atendimento")
        cfg = PlatformConfig(enabled=True, extra={"base_url": "https://example.test/api/v1", "api_key": "key"})
        a = MyZapAdapter(cfg)
        fake = FakeClient({
            "mensagens": [
                {"id": 20, "direcao": "RECEBIDA", "conteudo": "Oi widget", "remoteJid": "widget_abc123def45678", "conversaId": 8, "criadoEm": "2026-05-31T12:00:00.000Z"},
            ]
        })
        a._http_client = fake
        events = []

        async def capture(event):
            events.append(event)

        a.handle_message = capture
        count = await a.poll_once()
        assert count == 1
        assert events[0].text == "Oi widget"
        assert events[0].source.chat_id == "widget_abc123def45678"
        assert events[0].source.user_id == "widget_abc123def45678"

    asyncio.run(run())


def test_poll_once_dispatches_widget_inbound_with_external_identity(monkeypatch):
    async def run():
        monkeypatch.setenv("HERMES_PROFILE", "atendimento")
        cfg = PlatformConfig(enabled=True, extra={"base_url": "https://example.test/api/v1", "api_key": "key"})
        a = MyZapAdapter(cfg)
        mensagem = {
            "id": 23,
            "direcao": "RECEBIDA",
            "conteudo": "Consultar meus dados",
            "remoteJid": "widget_abc123def45678",
            "conversaId": 8,
            "criadoEm": "2026-05-31T12:00:00.000Z",
            "usuarioExternoId": "api-token-usuario",
            "usuarioExternoNome": "Maria Cliente",
            "contextoExterno": {"sistema": "agilcontabil"},
        }
        fake = FakeClient({"mensagens": [mensagem]})
        a._http_client = fake
        events = []

        async def capture(event):
            events.append(event)

        a.handle_message = capture
        count = await a.poll_once()
        assert count == 1
        assert usuario_externo_id(mensagem) == "api-token-usuario"
        assert events[0].source.chat_id == "widget_abc123def45678"
        assert events[0].source.user_id == "api-token-usuario"
        assert events[0].source.user_name == "Maria Cliente"
        assert "usuarioExternoId: api-token-usuario" in events[0].channel_context
        assert "sistema: agilcontabil" in events[0].channel_context
        assert texto_contexto_externo_widget(mensagem).startswith("Dados de identificação")

    asyncio.run(run())


def test_poll_once_filters_widget_by_allowed_context(monkeypatch):
    async def run():
        monkeypatch.setenv("HERMES_PROFILE", "atendentecompraragora")
        cfg = PlatformConfig(
            enabled=True,
            extra={
                "base_url": "https://example.test/api/v1",
                "api_key": "key",
                "widget_context_allow": "origem=compraragora_*|teste_producao_codex",
            },
        )
        a = MyZapAdapter(cfg)
        fake = FakeClient({
            "mensagens": [
                {
                    "id": 24,
                    "direcao": "RECEBIDA",
                    "conteudo": "Oi ComprarAgora",
                    "remoteJid": "widget_abc123def45678",
                    "conversaId": 8,
                    "criadoEm": "2026-05-31T12:00:00.000Z",
                    "contextoExterno": {"origem": "compraragora_publico"},
                },
            ]
        })
        a._http_client = fake
        events = []

        async def capture(event):
            events.append(event)

        a.handle_message = capture
        count = await a.poll_once()
        assert count == 1
        assert events[0].text == "Oi ComprarAgora"
        assert "origem: compraragora_publico" in events[0].channel_context

    asyncio.run(run())


def test_poll_once_blocks_widget_outside_allowed_context(monkeypatch):
    async def run():
        monkeypatch.setenv("HERMES_PROFILE", "atendentecompraragora")
        cfg = PlatformConfig(
            enabled=True,
            extra={
                "base_url": "https://example.test/api/v1",
                "api_key": "key",
                "widget_context_allow": "origem=compraragora_*",
            },
        )
        a = MyZapAdapter(cfg)
        fake = FakeClient({
            "mensagens": [
                {
                    "id": 25,
                    "direcao": "RECEBIDA",
                    "conteudo": "Oi Salão",
                    "remoteJid": "widget_abc123def45678",
                    "conversaId": 8,
                    "criadoEm": "2026-05-31T12:00:00.000Z",
                    "contextoExterno": {"origem": "geranet_salao"},
                },
            ]
        })
        a._http_client = fake
        events = []

        async def capture(event):
            events.append(event)

        a.handle_message = capture
        count = await a.poll_once()
        assert count == 0
        assert events == []

    asyncio.run(run())


def test_poll_once_denies_widget_by_context(monkeypatch):
    async def run():
        monkeypatch.setenv("HERMES_PROFILE", "atendente-salao")
        cfg = PlatformConfig(
            enabled=True,
            extra={
                "base_url": "https://example.test/api/v1",
                "api_key": "key",
                "widget_context_deny": "origem=compraragora_*",
            },
        )
        a = MyZapAdapter(cfg)
        fake = FakeClient({
            "mensagens": [
                {
                    "id": 26,
                    "direcao": "RECEBIDA",
                    "conteudo": "Oi ComprarAgora",
                    "remoteJid": "widget_abc123def45678",
                    "conversaId": 8,
                    "criadoEm": "2026-05-31T12:00:00.000Z",
                    "contextoExterno": {"origem": "compraragora_publico"},
                },
            ]
        })
        a._http_client = fake
        events = []

        async def capture(event):
            events.append(event)

        a.handle_message = capture
        count = await a.poll_once()
        assert count == 0
        assert events == []

    asyncio.run(run())


def test_poll_once_allows_valid_widget_even_when_numbers_allowlisted(monkeypatch):
    async def run():
        monkeypatch.setenv("HERMES_PROFILE", "atendimento")
        cfg = PlatformConfig(enabled=True, extra={"base_url": "https://example.test/api/v1", "api_key": "key", "allowed_numbers": "5562999990000"})
        a = MyZapAdapter(cfg)
        fake = FakeClient({
            "mensagens": [
                {"id": 21, "direcao": "RECEBIDA", "conteudo": "Oi widget", "remoteJid": "widget_abc123def45678", "conversaId": 8, "criadoEm": "2026-05-31T12:00:00.000Z"},
            ]
        })
        a._http_client = fake
        events = []

        async def capture(event):
            events.append(event)

        a.handle_message = capture
        count = await a.poll_once()
        assert count == 1
        assert events[0].source.chat_id == "widget_abc123def45678"

    asyncio.run(run())


def test_poll_once_rejects_malformed_widget_destination(monkeypatch):
    async def run():
        monkeypatch.setenv("HERMES_PROFILE", "atendimento")
        cfg = PlatformConfig(enabled=True, extra={"base_url": "https://example.test/api/v1", "api_key": "key"})
        a = MyZapAdapter(cfg)
        fake = FakeClient({
            "mensagens": [
                {"id": 22, "direcao": "RECEBIDA", "conteudo": "Oi widget", "remoteJid": "widget_abc123def456", "conversaId": 8, "criadoEm": "2026-05-31T12:00:00.000Z"},
            ]
        })
        a._http_client = fake
        events = []

        async def capture(event):
            events.append(event)

        a.handle_message = capture
        count = await a.poll_once()
        assert count == 0
        assert events == []

    asyncio.run(run())


def test_runtime_status_filter_blocks_only_known_runtime_noise_case_insensitive():
    assert is_filtered_runtime_status("Preflight compression starting") is True
    assert is_filtered_runtime_status("status: PRE-API COMPRESSION before reply") is True
    assert is_filtered_runtime_status("status: COMPACTING CONTEXT before reply") is True
    assert is_filtered_runtime_status("Codex gpt-5.5 caps context at 272K, so auto-compaction was raised") is True
    assert is_filtered_runtime_status("Opt back out: hermes config set compression.codex_gpt55_autoraise false") is True
    assert is_filtered_runtime_status("gateway SHUTTING down") is True
    assert is_filtered_runtime_status("status: SKIPPING CONCURRENT COMPRESSION already running") is True
    assert is_filtered_runtime_status("Agent note: INTERRUPTING CURRENT TASK to reload context") is True
    assert is_filtered_runtime_status("Self-improvement review: patched plugin successfully") is True
    assert is_filtered_runtime_status("Cliente perguntou sobre compactação de contexto") is False
    assert is_filtered_runtime_status("Resposta normal ao cliente") is False


def test_send_posts_widget_destination(monkeypatch):
    async def run():
        monkeypatch.setenv("HERMES_PROFILE", "atendimento")
        cfg = PlatformConfig(enabled=True, extra={"base_url": "https://example.test/api/v1", "api_key": "key"})
        a = MyZapAdapter(cfg)
        fake = FakeClient({})
        a._http_client = fake
        result = await a.send("widget_abc123def45678", "Resposta widget")
        assert result.success is True
        assert fake.posts[0]["url"] == "https://example.test/api/v1/mensagens/texto"
        assert fake.posts[0]["json"] == {"numero": "widget_abc123def45678", "texto": "Resposta widget"}

    asyncio.run(run())


def test_send_posts_text(monkeypatch):
    async def run():
        monkeypatch.setenv("HERMES_PROFILE", "atendimento")
        cfg = PlatformConfig(enabled=True, extra={"base_url": "https://example.test/api/v1", "api_key": "key"})
        a = MyZapAdapter(cfg)
        fake = FakeClient({})
        a._http_client = fake
        result = await a.send("+55 62 99999-0000", "Resposta")
        assert result.success is True
        assert fake.posts[0]["url"] == "https://example.test/api/v1/mensagens/texto"
        assert fake.posts[0]["json"] == {"numero": "5562999990000", "texto": "Resposta"}

    asyncio.run(run())

def test_send_suppresses_filtered_runtime_status(monkeypatch):
    async def run():
        monkeypatch.setenv("HERMES_PROFILE", "atendimento")
        cfg = PlatformConfig(enabled=True, extra={"base_url": "https://example.test/api/v1", "api_key": "key"})
        a = MyZapAdapter(cfg)
        fake = FakeClient({})
        a._http_client = fake
        result = await a.send("+55 62 99999-0000", "Status: Skipping concurrent compression already running")
        assert result.success is True
        assert result.message_id == "suppressed-runtime-status"
        assert fake.posts == []

        result = await a.send("+55 62 99999-0000", "Agent note: INTERRUPTING CURRENT TASK to reload context")
        assert result.success is True
        assert result.message_id == "suppressed-runtime-status"
        assert fake.posts == []

        result = await a.send("+55 62 99999-0000", "Self-improvement review: patched plugin successfully")
        assert result.success is True
        assert result.message_id == "suppressed-runtime-status"
        assert fake.posts == []

        result = await a.send("+55 62 99999-0000", "Resposta normal ao cliente")
        assert result.success is True
        assert result.message_id != "suppressed-runtime-status"
        assert len(fake.posts) == 1
        assert fake.posts[0]["url"] == "https://example.test/api/v1/mensagens/texto"
        assert fake.posts[0]["json"] == {"numero": "5562999990000", "texto": "Resposta normal ao cliente"}

    asyncio.run(run())


def test_send_document_posts_media(monkeypatch, tmp_path):
    async def run():
        monkeypatch.setenv("HERMES_PROFILE", "atendimento")
        cfg = PlatformConfig(enabled=True, extra={"base_url": "https://example.test/api/v1", "api_key": "key"})
        a = MyZapAdapter(cfg)
        fake = FakeClient({})
        a._http_client = fake
        arquivo = tmp_path / "boleto.pdf"
        arquivo.write_bytes(b"%PDF-1.4\nTESTE")

        result = await a.send_document("+55 62 99999-0000", str(arquivo), caption="Boleto atualizado")
        assert result.success is True
        assert fake.posts[0]["url"] == "https://example.test/api/v1/mensagens/midia"
        assert fake.posts[0]["data"] == {"numero": "5562999990000", "legenda": "Boleto atualizado"}
        assert fake.posts[0]["files"]["arquivo"][0] == "boleto.pdf"
        assert fake.posts[0]["files"]["arquivo"][1] == b"%PDF-1.4\nTESTE"
        assert fake.posts[0]["files"]["arquivo"][2] == "application/pdf"

    asyncio.run(run())

def test_public_operational_notice_detection():
    assert is_public_operational_notice("No home channel is set for Myzap. Type /sethome to configure it.") is True
    assert is_public_operational_notice("Confirmado, atendimento normal") is False


def test_send_suppresses_home_channel_notice_for_widget(monkeypatch):
    async def run():
        monkeypatch.setenv("HERMES_PROFILE", "atendimento")
        cfg = PlatformConfig(enabled=True, extra={"base_url": "https://example.test/api/v1", "api_key": "key"})
        a = MyZapAdapter(cfg)
        fake = FakeClient({})
        a._http_client = fake
        result = await a.send("widget_abc123def45678", "No home channel is set for Myzap. Type /sethome to configure it.")
        assert result.success is True
        assert result.message_id == "suppressed-home-channel-notice"
        assert fake.posts == []

    asyncio.run(run())
