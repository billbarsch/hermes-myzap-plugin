"""MyZap platform adapter for Hermes Agent.

Adapter for connecting any Hermes Agent profile to the MyZap WhatsApp API.
It polls MyZap's incremental messages API, sends Hermes replies through
``POST /mensagens/texto`` and can also upload attachments through
``POST /mensagens/midia`` for document/image/video/voice flows when the
gateway emits media files. Inbound media messages are accepted as first-class
events: the adapter keeps the raw attachment metadata, generates a readable
summary when MyZap does not provide text, and only falls back to text events
when the Hermes runtime does not expose a dedicated media message type.

Directory plugin install shape:

    ~/.hermes/profiles/<profile-name>/plugins/myzap/plugin.yaml
    ~/.hermes/profiles/<profile-name>/plugins/myzap/__init__.py

Pip install shape is also supported through the ``hermes_agent.plugins`` entry
point declared in pyproject.toml.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import mimetypes
import json
import logging
import os
import re
import time
from collections import Counter, OrderedDict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple
from urllib.parse import urlparse

try:  # Hermes already depends on httpx; keep an explicit guard for plugin checks.
    import httpx
    HTTPX_AVAILABLE = True
except Exception:  # pragma: no cover - exercised only when dependency missing
    httpx = None  # type: ignore[assignment]
    HTTPX_AVAILABLE = False

try:
    from gateway.config import Platform, PlatformConfig
    from gateway.platforms.base import BasePlatformAdapter, MessageEvent, MessageType, SendResult, cache_audio_from_url
except (ImportError, ModuleNotFoundError):  # pragma: no cover - lightweight stubs for standalone unit tests
    from dataclasses import dataclass, field
    from enum import Enum

    class Platform(Enum):
        MYZAP = "myzap"

        @classmethod
        def _missing_(cls, value):
            if value == "myzap":
                return cls.MYZAP
            return None

    @dataclass
    class PlatformConfig:
        enabled: bool = False
        extra: Dict[str, Any] = field(default_factory=dict)

    class MessageType(Enum):
        TEXT = "text"
        VOICE = "voice"
        AUDIO = "audio"
        PHOTO = "photo"
        IMAGE = "image"
        VIDEO = "video"
        STICKER = "sticker"
        DOCUMENT = "document"
        FILE = "file"

    @dataclass
    class _SessionSource:
        platform: Platform
        chat_id: str
        chat_name: Optional[str] = None
        chat_type: str = "dm"
        user_id: Optional[str] = None
        user_name: Optional[str] = None
        message_id: Optional[str] = None

    @dataclass
    class MessageEvent:
        text: str
        message_type: MessageType = MessageType.TEXT
        source: Any = None
        raw_message: Any = None
        message_id: Optional[str] = None
        media_urls: List[str] = field(default_factory=list)
        media_types: List[str] = field(default_factory=list)
        timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    @dataclass
    class SendResult:
        success: bool
        message_id: Optional[str] = None
        error: Optional[str] = None
        raw_response: Any = None
        retryable: bool = False

    class BasePlatformAdapter:
        def __init__(self, config: PlatformConfig, platform: Platform):
            self.config = config
            self.platform = platform
            self.name = platform.value
            self._running = True

        def _mark_connected(self) -> None:
            return None

        def _mark_disconnected(self) -> None:
            return None

        def build_source(self, chat_id: str, chat_name: Optional[str] = None, chat_type: str = "dm", user_id: Optional[str] = None, user_name: Optional[str] = None, message_id: Optional[str] = None, **kwargs: Any) -> _SessionSource:
            return _SessionSource(self.platform, str(chat_id), chat_name, chat_type, str(user_id) if user_id else None, user_name, message_id)

        async def handle_message(self, event: MessageEvent) -> None:
            return None

    async def cache_audio_from_url(url: str, ext: str = ".ogg", retries: int = 2) -> str:
        del ext, retries
        return url

logger = logging.getLogger(__name__)

DEFAULT_API_BASE = "https://api.myzap.net/api/v1"
DEFAULT_POLL_INTERVAL_SECONDS = 10.0
DEFAULT_POLL_LOOKBACK_SECONDS = 120.0
DEFAULT_LIMIT = 100
MAX_MESSAGE_LENGTH = 4096
DEDUP_WINDOW_SECONDS = 15 * 60
DEDUP_MAX_SIZE = 5000
RECONNECT_BACKOFF_SECONDS = (2, 5, 10, 30, 60)
DEFAULT_REQUIRED_PROFILE = ""
WIDGET_DESTINATION_RE = re.compile(r"^widget_[a-f0-9]{14}$")
HOME_CHANNEL_NOTICE_PREFIX = "no home channel is set for myzap"
DEFAULT_STATE_FILENAME = "myzap_poll_state.json"


def _truthy(value: str | None) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on", "sim"}


def _env(name: str, default: str = "") -> str:
    try:
        from hermes_cli.config import get_env_value

        value = get_env_value(name)
        if value is not None:
            return str(value).strip()
    except Exception:
        pass
    return os.getenv(name, default).strip()


def _current_profile() -> str:
    return _env("HERMES_PROFILE_NAME") or _env("HERMES_PROFILE")


def _required_profile() -> str:
    return _env("MYZAP_HERMES_PROFILE", DEFAULT_REQUIRED_PROFILE)


def _profile_allowed() -> bool:
    """Allow any profile unless MYZAP_HERMES_PROFILE explicitly restricts it."""
    current = _current_profile()
    required = _required_profile()
    return not current or not required or current == required


def _base_url_from(extra: Dict[str, Any] | None = None) -> str:
    extra = extra or {}
    return str(extra.get("base_url") or _env("MYZAP_BASE_URL", DEFAULT_API_BASE)).rstrip("/")


def _api_key_from(extra: Dict[str, Any] | None = None) -> str:
    extra = extra or {}
    return str(extra.get("api_key") or _env("MYZAP_API_KEY") or "").strip()


def _state_path_from(extra: Dict[str, Any] | None = None) -> Path:
    extra = extra or {}
    explicit = str(extra.get("state_path") or _env("MYZAP_STATE_PATH") or "").strip()
    if explicit:
        return Path(explicit).expanduser()
    hermes_home = _env("HERMES_HOME") or str(Path.home() / ".hermes")
    return Path(hermes_home).expanduser() / "runtime" / DEFAULT_STATE_FILENAME


def _headers(api_key: str) -> Dict[str, str]:
    return {
        "X-API-Key": api_key,
        "Accept": "application/json",
        "User-Agent": "Hermes-MyZap-Plugin/0.2",
    }


def _parse_allowed(raw: str) -> set[str]:
    return {item.strip() for item in raw.split(",") if item.strip()}


def normalize_number(value: Any) -> str:
    """Normalize a phone/JID-ish value to digits only where possible."""
    text = str(value or "").strip()
    if "@" in text:
        text = text.split("@", 1)[0]
    return "".join(ch for ch in text if ch.isdigit())


def is_widget_destination(value: Any) -> bool:
    return bool(WIDGET_DESTINATION_RE.fullmatch(str(value or "").strip()))


def is_public_operational_notice(content: Any) -> bool:
    """Suppress Hermes setup/onboarding notices from public widget transcripts."""
    text = str(content or "").strip().lower()
    return text.startswith(HOME_CHANNEL_NOTICE_PREFIX) and "/sethome" in text


def iso_utc(dt: datetime) -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def parse_datetime(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    text = str(value or "").strip()
    if not text:
        return datetime.now(timezone.utc)
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(text)
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except Exception:
        return datetime.now(timezone.utc)


def extract_messages(payload: Any) -> List[Dict[str, Any]]:
    """Accept common MyZap list shapes and return message dictionaries."""
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if not isinstance(payload, dict):
        return []
    for key in ("mensagens", "messages", "dados", "data", "items", "registros"):
        value = payload.get(key)
        if isinstance(value, list):
            return [item for item in value if isinstance(item, dict)]
        if isinstance(value, dict):
            nested = extract_messages(value)
            if nested:
                return nested
    return []


def message_attachments(message: Dict[str, Any]) -> List[Dict[str, Any]]:
    attachments: List[Dict[str, Any]] = []
    seen: set[str] = set()
    for key in ("arquivos", "arquivosMensagem", "attachments", "media"):
        value = message.get(key)
        if isinstance(value, list):
            for item in value:
                if isinstance(item, dict):
                    signature = "|".join(
                        str(item.get(campo) or "").strip()
                        for campo in ("id", "nome", "fileName", "url", "downloadUrl")
                    )
                    if signature in seen:
                        continue
                    seen.add(signature)
                    attachments.append(item)
    normalized: List[Dict[str, Any]] = []
    for item in attachments:
        nome = str(item.get("nome") or item.get("fileName") or item.get("filename") or "arquivo").strip() or "arquivo"
        mime_type = str(item.get("mimeType") or item.get("mime_type") or item.get("mimetype") or "").strip()
        tipo = str(item.get("tipo") or item.get("type") or "").strip().lower()
        url = str(item.get("url") or item.get("link") or item.get("downloadUrl") or "").strip()
        normalized.append({
            **item,
            "nome": nome,
            "mimeType": mime_type,
            "tipo": tipo,
            "url": url,
        })
    return normalized


def attachment_url(attachment: Dict[str, Any]) -> str:
    return str(attachment.get("url") or attachment.get("link") or attachment.get("downloadUrl") or "").strip()


def attachment_mime_type(attachment: Dict[str, Any]) -> str:
    return str(attachment.get("mimeType") or attachment.get("mime_type") or attachment.get("mimetype") or "").strip()


def attachment_name(attachment: Dict[str, Any]) -> str:
    return str(attachment.get("nome") or attachment.get("fileName") or attachment.get("filename") or "arquivo").strip() or "arquivo"


def safe_download_url(url: str) -> bool:
    parsed = urlparse(str(url or "").strip())
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def attachment_kind(attachment: Dict[str, Any]) -> str:
    tipo = str(attachment.get("tipo") or "").strip().lower()
    mime = str(attachment.get("mimeType") or attachment.get("mime_type") or "").strip().lower()
    nome = str(attachment.get("nome") or "").strip().lower()

    if tipo in {"imagem", "image"} or mime.startswith("image/"):
        if mime == "image/webp" or nome.endswith(".webp"):
            return "sticker"
        return "image"
    if tipo in {"video"} or mime.startswith("video/"):
        return "video"
    if tipo in {"audio", "voice"} or mime.startswith("audio/") or mime == "application/ogg":
        return "audio"
    if tipo in {"figurinha", "sticker"} or nome.endswith(".webp"):
        return "sticker"
    return "document"


def message_media_summary(message: Dict[str, Any]) -> str:
    attachments = message_attachments(message)
    if not attachments:
        return ""

    if len(attachments) == 1:
        attachment = attachments[0]
        kind = attachment_kind(attachment)
        nome = str(attachment.get("nome") or "").strip()
        if kind == "image":
            return f"🖼️ Imagem enviada{f': {nome}' if nome else ''}"
        if kind == "video":
            return f"🎬 Vídeo enviado{f': {nome}' if nome else ''}"
        if kind == "audio":
            return f"🎤 Áudio enviado{f': {nome}' if nome else ''}"
        if kind == "sticker":
            return f"✨ Figurinha enviada{f': {nome}' if nome else ''}"
        return f"📎 Arquivo enviado{f': {nome}' if nome else ''}"

    kinds = Counter(attachment_kind(attachment) for attachment in attachments)
    if len(kinds) == 1:
        kind = next(iter(kinds))
        if kind == "image":
            return f"🖼️ {len(attachments)} imagens anexadas"
        if kind == "video":
            return f"🎬 {len(attachments)} vídeos anexados"
        if kind == "audio":
            return f"🎤 {len(attachments)} áudios anexados"
        if kind == "sticker":
            return f"✨ {len(attachments)} figurinhas anexadas"
    return f"📎 {len(attachments)} arquivos anexados"


def message_primary_kind(message: Dict[str, Any]) -> str:
    attachments = message_attachments(message)
    if not attachments:
        return ""
    return attachment_kind(attachments[0])


def message_identity(message: Dict[str, Any]) -> str:
    for key in ("messageId", "message_id", "id", "idMensagem"):
        value = message.get(key)
        if value is not None and str(value).strip():
            return str(value).strip()
    seed = "|".join(str(message.get(k, "")) for k in ("conversaId", "numero", "criadoEm", "conteudo", "texto"))
    return hashlib.sha256(seed.encode("utf-8", errors="ignore")).hexdigest()[:24]


def message_text(message: Dict[str, Any]) -> str:
    for key in ("conteudo", "texto", "mensagem", "message", "body"):
        value = message.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return message_media_summary(message)


def is_media_summary_text(text: str) -> bool:
    normalized = str(text or "").strip().lower()
    return bool(normalized) and any(
        normalized.startswith(prefix)
        for prefix in (
            "🎤 áudio enviado",
            "áudio enviado",
            "audio enviado",
            "🖼️ imagem enviada",
            "imagem enviada",
            "🎬 vídeo enviado",
            "vídeo enviado",
            "video enviado",
            "📎 arquivo enviado",
            "arquivo enviado",
        )
    )


def _message_type_for_kind(kind: str):
    desired = {
        "audio": ("VOICE", "AUDIO"),
        "image": ("IMAGE", "PHOTO"),
        "video": ("VIDEO",),
        "sticker": ("STICKER",),
        "document": ("DOCUMENT", "FILE"),
    }.get(kind, ())
    for candidate in desired:
        member = getattr(MessageType, candidate, None)
        if member is not None:
            return member
    return MessageType.TEXT


def _audio_extension_for_attachment(attachment: Dict[str, Any]) -> str:
    mime_type = attachment_mime_type(attachment).lower()
    nome = attachment_name(attachment).lower()
    if "ogg" in mime_type or nome.endswith((".ogg", ".opus")):
        return ".ogg"
    if "webm" in mime_type or nome.endswith(".webm"):
        return ".webm"
    if "mpeg" in mime_type or "mp3" in mime_type or nome.endswith(".mp3"):
        return ".mp3"
    if "wav" in mime_type or nome.endswith(".wav"):
        return ".wav"
    if "m4a" in mime_type or "mp4" in mime_type or nome.endswith((".m4a", ".mp4")):
        return ".m4a"
    return ".ogg"


def message_direction(message: Dict[str, Any]) -> str:
    return str(message.get("direcao") or message.get("direction") or message.get("tipo") or "").lower()


def is_inbound(message: Dict[str, Any]) -> bool:
    direction = message_direction(message)
    if not direction:
        return not bool(message.get("fromMe") or message.get("from_me") or message.get("enviadaPorMim"))
    return any(token in direction for token in ("receb", "inbound", "entrada", "cliente"))


def message_number(message: Dict[str, Any]) -> str:
    candidates: Iterable[Any] = (
        message.get("numero"),
        message.get("telefone"),
        message.get("remoteJid"),
        message.get("chatId"),
        (message.get("contato") or {}).get("numero") if isinstance(message.get("contato"), dict) else None,
        (message.get("conversa") or {}).get("numero") if isinstance(message.get("conversa"), dict) else None,
    )
    for candidate in candidates:
        normalized = normalize_number(candidate)
        if normalized:
            return normalized
    return ""


def message_destination(message: Dict[str, Any]) -> str:
    """Return the safest reply destination for MyZap.

    WhatsApp conversations use numeric phone/JID values, while the embedded
    widget stores synthetic visitor identifiers such as ``widget_<hash>`` in
    ``remoteJid``/``numeroWhatsapp``. The Hermes gateway later calls
    ``send(chat_id=source.chat_id, ...)`` without raw message metadata, so the
    adapter must preserve the widget identifier as the chat id. Reducing a
    widget id to digits only makes replies go to an invalid WhatsApp number and
    never appear in the embedded chat.
    """
    for key in ("remoteJid", "numeroWhatsapp", "numero", "telefone", "chatId"):
        value = message.get(key)
        if value is None:
            continue
        raw = str(value).strip()
        if not raw:
            continue
        if is_widget_destination(raw):
            return raw
        if raw.startswith("widget_"):
            return ""
        normalized = normalize_number(raw)
        if normalized:
            return normalized
    for parent in ("contato", "conversa"):
        value = message.get(parent)
        if isinstance(value, dict):
            nested = message_destination(value)
            if nested:
                return nested
    return ""


def message_chat_id(message: Dict[str, Any]) -> str:
    for key in ("conversaId", "conversa_id", "chatId", "remoteJid"):
        value = message.get(key)
        if value is not None and str(value).strip():
            return str(value).strip()
    return message_number(message)


def message_sender_name(message: Dict[str, Any]) -> str:
    for key in ("nome", "pushName", "contatoNome", "name"):
        value = message.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    for parent in ("contato", "conversa"):
        value = message.get(parent)
        if isinstance(value, dict):
            for key in ("nome", "pushName", "name"):
                nested = value.get(key)
                if isinstance(nested, str) and nested.strip():
                    return nested.strip()
    return message_number(message) or "MyZap"


def message_created_at(message: Dict[str, Any]) -> datetime:
    for key in ("criadoEm", "createdAt", "timestamp", "dataHora", "data"):
        value = message.get(key)
        if value:
            return parse_datetime(value)
    return datetime.now(timezone.utc)


def verify_webhook_signature(secret: str, body: bytes, signature: str) -> bool:
    """Validate a sha256 HMAC signature from an external webhook wrapper.

    Accepts plain hex, ``sha256=<hex>`` or ``hmac-sha256=<hex>``.  Hermes does
    not expose a plugin-owned HTTP route here; this helper is provided for the
    small API shim that may forward MyZap webhooks to the Hermes gateway later.
    """
    secret = (secret or "").strip()
    signature = (signature or "").strip()
    if not secret or not signature:
        return False
    provided = signature.split("=", 1)[1] if "=" in signature else signature
    expected = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(provided.lower(), expected.lower())


def _myzap_platform() -> Platform:
    """Return a Platform member for myzap even in isolated unit tests.

    In normal Hermes startup, ``ctx.register_platform('myzap', ...)`` registers
    the platform before the adapter factory is called, so ``Platform('myzap')``
    resolves via the runtime registry.  Unit tests instantiate the adapter
    directly, so we create the same enum pseudo-member defensively.
    """
    try:
        return Platform("myzap")
    except ValueError:
        pseudo = object.__new__(Platform)
        pseudo._value_ = "myzap"
        pseudo._name_ = "MYZAP"
        Platform._value2member_map_["myzap"] = pseudo
        Platform._member_map_["MYZAP"] = pseudo
        return pseudo


def check_requirements() -> bool:
    return HTTPX_AVAILABLE and bool(_api_key_from()) and _profile_allowed()


def validate_config(config: PlatformConfig) -> bool:
    extra = getattr(config, "extra", {}) or {}
    return bool(_api_key_from(extra)) and _profile_allowed()


def is_connected(config: PlatformConfig) -> bool:
    return validate_config(config)


async def _enviar_midia_http(
    http_client,
    *,
    base_url: str,
    api_key: str,
    destination: str,
    file_path: str,
    caption: str = "",
    file_name: Optional[str] = None,
    force_document: bool = False,
) -> Dict[str, Any]:
    arquivo = Path(file_path)
    if not arquivo.exists() or not arquivo.is_file():
        raise FileNotFoundError(f"Arquivo de mídia não encontrado: {file_path}")

    nome_upload = file_name or arquivo.name
    mime_type = mimetypes.guess_type(nome_upload)[0] or mimetypes.guess_type(arquivo.name)[0] or "application/octet-stream"
    payload = {"numero": destination, "legenda": caption or ""}
    if force_document:
        payload["tipo"] = "documento"

    resp = await http_client.post(
        f"{base_url}/mensagens/midia",
        data=payload,
        files={"arquivo": (nome_upload, arquivo.read_bytes(), mime_type)},
        headers=_headers(api_key),
    )
    if resp.status_code >= 300:
        return {
            "success": False,
            "error": f"HTTP {resp.status_code}: {resp.text[:200]}",
            "raw_response": resp.text,
        }

    try:
        data = resp.json()
    except Exception:
        data = {"raw": resp.text}

    msg_id = str(data.get("messageId") or (data.get("mensagem") or {}).get("id") or data.get("id") or "") or None
    return {
        "success": True,
        "message_id": msg_id,
        "raw_response": {"status_code": resp.status_code, "dados": data},
    }


class MyZapAdapter(BasePlatformAdapter):
    """Hermes gateway adapter backed by MyZap's REST API."""

    def __init__(self, config: PlatformConfig):
        super().__init__(config=config, platform=_myzap_platform())
        extra = config.extra or {}
        self._base_url = _base_url_from(extra)
        self._api_key = _api_key_from(extra)
        self._poll_interval = float(extra.get("poll_interval_seconds") or _env("MYZAP_POLL_INTERVAL_SECONDS", str(DEFAULT_POLL_INTERVAL_SECONDS)))
        self._lookback_seconds = float(extra.get("poll_lookback_seconds") or _env("MYZAP_POLL_LOOKBACK_SECONDS", str(DEFAULT_POLL_LOOKBACK_SECONDS)))
        self._limit = int(extra.get("limit") or _env("MYZAP_POLL_LIMIT", str(DEFAULT_LIMIT)))
        self._cursor: Optional[str] = str(extra.get("cursor") or _env("MYZAP_CURSOR") or "").strip() or None
        self._since = datetime.now(timezone.utc) - timedelta(seconds=self._lookback_seconds)
        self._state_path = _state_path_from(extra)
        self._http_client: Optional["httpx.AsyncClient"] = None
        self._poll_task: Optional[asyncio.Task] = None
        self._seen: "OrderedDict[str, float]" = OrderedDict()
        self._allowed_numbers = _parse_allowed(str(extra.get("allowed_numbers") or _env("MYZAP_ALLOWED_NUMBERS")))
        self._allow_all = bool(extra.get("allow_all_numbers")) or _truthy(_env("MYZAP_ALLOW_ALL_NUMBERS"))
        self._load_state()

    def _load_state(self) -> None:
        """Restore poll cursor/seen ids so gateway restarts do not replay recent widget messages."""
        try:
            if not self._state_path.exists():
                return
            data = json.loads(self._state_path.read_text(encoding="utf-8"))
            cursor = str(data.get("cursor") or "").strip()
            if cursor and not self._cursor:
                self._cursor = cursor
            since = data.get("since")
            if since:
                self._since = parse_datetime(since)
            seen = data.get("seen") or []
            now = time.time()
            for item in seen[-DEDUP_MAX_SIZE:]:
                if isinstance(item, str) and item.strip():
                    self._seen[item.strip()] = now
                elif isinstance(item, dict) and item.get("id"):
                    self._seen[str(item["id"])] = float(item.get("seen_at") or now)
            logger.info("[myzap] restored poll state: cursor=%s seen=%d", bool(self._cursor), len(self._seen))
        except Exception as exc:
            logger.warning("[myzap] ignoring unreadable poll state %s: %s", self._state_path, exc)

    def _persist_state(self) -> None:
        try:
            now = time.time()
            compact_seen = [
                {"id": key, "seen_at": seen_at}
                for key, seen_at in self._seen.items()
                if now - seen_at <= DEDUP_WINDOW_SECONDS
            ][-DEDUP_MAX_SIZE:]
            self._state_path.parent.mkdir(parents=True, exist_ok=True)
            tmp_path = self._state_path.with_suffix(self._state_path.suffix + ".tmp")
            tmp_path.write_text(json.dumps({
                "cursor": self._cursor,
                "since": iso_utc(self._since),
                "seen": compact_seen,
            }, ensure_ascii=False), encoding="utf-8")
            tmp_path.replace(self._state_path)
        except Exception as exc:
            logger.warning("[myzap] failed to persist poll state %s: %s", self._state_path, exc)

    async def connect(self) -> bool:
        if not HTTPX_AVAILABLE:
            logger.warning("[myzap] httpx not installed. Run: pip install httpx")
            return False
        if not _profile_allowed():
            logger.warning("[myzap] disabled outside configured profile %s", _required_profile())
            return False
        if not self._api_key:
            logger.warning("[myzap] MYZAP_API_KEY not configured")
            return False
        self._http_client = httpx.AsyncClient(timeout=httpx.Timeout(30.0))
        self._poll_task = asyncio.create_task(self._poll_loop())
        self._mark_connected()
        logger.info(
            "[myzap] connected in polling mode: base=%s interval=%ss source=%s",
            self._base_url,
            self._poll_interval,
            __file__,
        )
        return True

    async def disconnect(self) -> None:
        self._running = False
        self._mark_disconnected()
        if self._poll_task:
            self._poll_task.cancel()
            try:
                await self._poll_task
            except asyncio.CancelledError:
                pass
            self._poll_task = None
        if self._http_client:
            await self._http_client.aclose()
            self._http_client = None
        self._persist_state()
        self._seen.clear()
        logger.info("[myzap] disconnected")

    async def _poll_loop(self) -> None:
        backoff_idx = 0
        while self._running:
            try:
                count = await self.poll_once()
                if count:
                    logger.debug("[myzap] dispatched %d inbound message(s)", count)
                backoff_idx = 0
                await asyncio.sleep(max(self._poll_interval, 1.0))
            except asyncio.CancelledError:
                return
            except Exception as exc:
                delay = RECONNECT_BACKOFF_SECONDS[min(backoff_idx, len(RECONNECT_BACKOFF_SECONDS) - 1)]
                backoff_idx += 1
                logger.warning("[myzap] polling failed: %s; retrying in %ss", exc, delay)
                await asyncio.sleep(delay)

    async def poll_once(self) -> int:
        """Fetch one incremental page and dispatch inbound text messages."""
        if self._http_client is None:
            raise RuntimeError("HTTP client not initialized")
        params: Dict[str, Any] = {
            "desde": iso_utc(self._since),
            "limite": self._limit,
            "ordem": "asc",
        }
        if self._cursor:
            params["cursor"] = self._cursor
        resp = await self._http_client.get(f"{self._base_url}/mensagens", params=params, headers=_headers(self._api_key))
        # MyZap has accepted milliseconds in ISO filters in previous validations;
        # if a deployment rejects the format, retry once without cursor using offset seconds.
        if resp.status_code == 400 and "DATA_INVALIDA" in resp.text.upper():
            params["desde"] = self._since.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")
            resp = await self._http_client.get(f"{self._base_url}/mensagens", params=params, headers=_headers(self._api_key))
        if resp.status_code == 400 and self._cursor and "CURSOR" in resp.text.upper():
            logger.warning("[myzap] cursor rejected by API; clearing cursor and retrying with persisted since")
            self._cursor = None
            params.pop("cursor", None)
            resp = await self._http_client.get(f"{self._base_url}/mensagens", params=params, headers=_headers(self._api_key))
        resp.raise_for_status()
        payload = resp.json()
        messages = extract_messages(payload)
        dispatched = 0
        max_dt = self._since
        for msg in sorted(messages, key=lambda m: (message_created_at(m), str(m.get("id") or m.get("messageId") or ""))):
            created = message_created_at(msg)
            if created > max_dt:
                max_dt = created
            if await self._dispatch_if_relevant(msg):
                dispatched += 1

        # API v1 returns an opaque cursor in meta.nextCursor using the
        # required ``criadoEm,id`` format. Do not synthesize it from id only,
        # otherwise production rejects the next poll with CURSOR_INVALIDO.
        meta = payload.get("meta") if isinstance(payload, dict) else None
        next_cursor = str((meta or {}).get("nextCursor") or "").strip() or None
        if next_cursor:
            self._cursor = next_cursor
        if max_dt > self._since:
            self._since = max_dt
        self._persist_state()
        return dispatched

    async def _prepare_inbound_message(self, msg: Dict[str, Any]) -> Dict[str, Any]:
        text = message_text(msg)
        attachments = message_attachments(msg)
        media_urls: List[str] = []
        media_types: List[str] = []

        for attachment in attachments:
            url = attachment_url(attachment)
            mime_type = attachment_mime_type(attachment)
            kind = attachment_kind(attachment)
            if kind == "audio" and url:
                cached_url = await self._cache_audio_attachment(attachment)
                media_urls.append(cached_url or url)
                media_types.append(mime_type or "audio/ogg")
                continue
            if url:
                media_urls.append(url)
                media_types.append(mime_type or kind)

        if any(attachment_kind(attachment) == "audio" for attachment in attachments) and is_media_summary_text(text):
            text = "(The user sent a message with no text content)"

        if attachments or is_media_summary_text(text):
            logger.info(
                "[myzap] prepared inbound message: message=%s attachments=%d media=%d text=%r",
                message_identity(msg),
                len(attachments),
                len(media_urls),
                text[:120],
            )

        return {
            "text": text,
            "media_urls": media_urls,
            "media_types": media_types,
        }

    async def _cache_audio_attachment(self, attachment: Dict[str, Any]) -> str:
        url = attachment_url(attachment)
        if not url or not safe_download_url(url):
            logger.warning("[myzap] skipping audio cache for unsafe or empty URL")
            return ""

        try:
            cached_path = await cache_audio_from_url(url, ext=_audio_extension_for_attachment(attachment))
            logger.info("[myzap] cached user voice at %s", cached_path)
            return cached_path
        except Exception as exc:
            logger.warning("[myzap] failed to cache voice: %s", exc)
            return ""

    async def _dispatch_if_relevant(self, msg: Dict[str, Any]) -> bool:
        msg_id = message_identity(msg)
        if self._is_duplicate(msg_id):
            return False
        if not is_inbound(msg):
            logger.debug("[myzap] skipping outbound/echo message %s", msg_id)
            return False
        prepared_message = await self._prepare_inbound_message(msg)
        text = prepared_message["text"]
        if not text:
            logger.debug("[myzap] skipping empty message %s", msg_id)
            return False
        destination = message_destination(msg)
        if not self._number_allowed(destination):
            logger.info("[myzap] skipping destination outside adapter allowlist: %s", destination[-4:] if destination else "unknown")
            return False
        chat_id = destination if is_widget_destination(destination) else (message_chat_id(msg) or destination)
        source = self.build_source(
            chat_id=chat_id,
            chat_name=message_sender_name(msg),
            chat_type="dm",
            user_id=destination or chat_id,
            user_name=message_sender_name(msg),
            message_id=msg_id,
        )
        event = MessageEvent(
            text=text,
            message_type=_message_type_for_kind(message_primary_kind(msg)),
            source=source,
            raw_message=msg,
            message_id=msg_id,
            media_urls=prepared_message["media_urls"],
            media_types=prepared_message["media_types"],
            timestamp=message_created_at(msg),
        )
        if event.media_urls:
            logger.info(
                "[myzap] dispatching inbound media: message=%s type=%s media=%d first_media_type=%s",
                msg_id,
                getattr(event.message_type, "value", str(event.message_type)),
                len(event.media_urls),
                event.media_types[0] if event.media_types else "",
            )
        await self.handle_message(event)
        return True

    def _number_allowed(self, number: str) -> bool:
        if not str(number or "").strip():
            return False
        if self._allow_all:
            return True
        if is_widget_destination(number):
            return True
        if str(number or "").strip().startswith("widget_"):
            return False
        if not self._allowed_numbers:
            # Leave trust boundary to Hermes gateway auth (MYZAP_ALLOWED_USERS),
            # but default to accepting the adapter message so pairing/allowlist
            # can decide centrally.
            return True
        if number in self._allowed_numbers:
            return True
        normalized_number = normalize_number(number)
        normalized_allowed = {normalize_number(item) or item for item in self._allowed_numbers}
        return bool(normalized_number and normalized_number in normalized_allowed)

    def _is_duplicate(self, msg_id: str) -> bool:
        now = time.time()
        while self._seen and (len(self._seen) > DEDUP_MAX_SIZE or now - next(iter(self._seen.values())) > DEDUP_WINDOW_SECONDS):
            self._seen.popitem(last=False)
        if msg_id in self._seen:
            return True
        self._seen[msg_id] = now
        return False

    async def send(self, chat_id: str, content: str, reply_to: Optional[str] = None, metadata: Optional[Dict[str, Any]] = None) -> SendResult:
        if self._http_client is None:
            return SendResult(success=False, error="HTTP client not initialized")
        destination_raw = str((metadata or {}).get("numero") or chat_id or "").strip()
        if is_widget_destination(destination_raw):
            destination = destination_raw
        elif destination_raw.startswith("widget_"):
            return SendResult(success=False, error="invalid MyZap widget destination")
        else:
            destination = normalize_number(destination_raw)
        if not destination:
            return SendResult(success=False, error="missing MyZap destination")
        if is_widget_destination(destination) and is_public_operational_notice(content):
            logger.info("[myzap] suppressed operational home-channel notice for public widget destination")
            return SendResult(success=True, message_id="suppressed-home-channel-notice", raw_response={"suppressed": True})
        body = {"numero": destination, "texto": content[:MAX_MESSAGE_LENGTH]}
        try:
            resp = await self._http_client.post(f"{self._base_url}/mensagens/texto", json=body, headers=_headers(self._api_key))
            if resp.status_code >= 300:
                logger.warning("[myzap] send failed HTTP %s: %s", resp.status_code, resp.text[:200])
                return SendResult(success=False, error=f"HTTP {resp.status_code}: {resp.text[:200]}", raw_response=resp.text)
            data = resp.json()
            msg_id = str(data.get("messageId") or (data.get("mensagem") or {}).get("id") or data.get("id") or "") or None
            return SendResult(success=True, message_id=msg_id, raw_response={"status_code": resp.status_code})

        except Exception as exc:
            logger.error("[myzap] send error: %s", exc)
            return SendResult(success=False, error=str(exc), retryable=True)

    async def send_document(
        self,
        chat_id: str,
        file_path: str,
        caption: Optional[str] = None,
        file_name: Optional[str] = None,
        reply_to: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
        **kwargs,
    ) -> SendResult:
        if self._http_client is None:
            return SendResult(success=False, error="HTTP client not initialized")
        destination_raw = str((metadata or {}).get("numero") or chat_id or "").strip()
        if is_widget_destination(destination_raw):
            destination = destination_raw
        elif destination_raw.startswith("widget_"):
            return SendResult(success=False, error="invalid MyZap widget destination")
        else:
            destination = normalize_number(destination_raw)
        if not destination:
            return SendResult(success=False, error="missing MyZap destination")

        try:
            resultado = await _enviar_midia_http(
                self._http_client,
                base_url=self._base_url,
                api_key=self._api_key,
                destination=destination,
                file_path=file_path,
                caption=caption or "",
                file_name=file_name,
                force_document=bool(kwargs.get("force_document")),
            )
            if not resultado.get("success"):
                return SendResult(success=False, error=resultado.get("error", "Erro ao enviar mídia"), raw_response=resultado.get("raw_response"))
            return SendResult(success=True, message_id=resultado.get("message_id"), raw_response=resultado.get("raw_response"))
        except Exception as exc:
            logger.error("[myzap] send_document error: %s", exc)
            return SendResult(success=False, error=str(exc), retryable=True)

    async def send_image_file(
        self,
        chat_id: str,
        image_path: str,
        caption: Optional[str] = None,
        reply_to: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
        **kwargs,
    ) -> SendResult:
        return await self.send_document(chat_id, image_path, caption=caption, reply_to=reply_to, metadata=metadata, **kwargs)

    async def send_voice(
        self,
        chat_id: str,
        audio_path: str,
        caption: Optional[str] = None,
        reply_to: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
        **kwargs,
    ) -> SendResult:
        return await self.send_document(chat_id, audio_path, caption=caption, reply_to=reply_to, metadata=metadata, **kwargs)

    async def send_video(
        self,
        chat_id: str,
        video_path: str,
        caption: Optional[str] = None,
        reply_to: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
        **kwargs,
    ) -> SendResult:
        return await self.send_document(chat_id, video_path, caption=caption, reply_to=reply_to, metadata=metadata, **kwargs)

    async def send_typing(self, chat_id: str, metadata=None) -> None:
        return None

    async def get_chat_info(self, chat_id: str) -> Dict[str, Any]:
        return {"name": chat_id, "type": "dm"}


async def _standalone_send(
    pconfig: PlatformConfig,
    chat_id: str,
    message: str,
    *,
    thread_id: Optional[str] = None,
    media_files: Optional[List[str]] = None,
    force_document: bool = False,
) -> Dict[str, Any]:
    if not HTTPX_AVAILABLE:
        return {"error": "myzap standalone send: httpx not installed"}
    extra = getattr(pconfig, "extra", {}) or {}
    api_key = _api_key_from(extra)
    if not api_key:
        return {"error": "myzap standalone send: MYZAP_API_KEY not configured"}
    number = normalize_number(chat_id or _env("MYZAP_HOME_NUMBER"))
    if not number:
        return {"error": "myzap standalone send: destination number missing"}
    base_url = _base_url_from(extra)
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            if media_files:
                resultado_final: Dict[str, Any] = {"success": True, "platform": "myzap", "chat_id": number, "media_sent": []}
                for index, item in enumerate(media_files):
                    media_path, _is_voice = item if isinstance(item, (list, tuple)) and len(item) >= 2 else (item, False)
                    postagem = await _enviar_midia_http(
                        client,
                        base_url=base_url,
                        api_key=api_key,
                        destination=number,
                        file_path=str(media_path),
                        caption=message[:MAX_MESSAGE_LENGTH] if index == 0 else "",
                        force_document=bool(force_document),
                    )
                    if not postagem.get("success"):
                        return {"error": postagem.get("error", "myzap media send failed"), "raw_response": postagem.get("raw_response")}
                    resultado_final["media_sent"].append(postagem)
                    if not resultado_final.get("message_id"):
                        resultado_final["message_id"] = postagem.get("message_id")
                return resultado_final

            resp = await client.post(
                f"{base_url}/mensagens/texto",
                json={"numero": number, "texto": message[:MAX_MESSAGE_LENGTH]},
                headers=_headers(api_key),
            )
            if resp.status_code >= 300:
                return {"error": f"myzap HTTP {resp.status_code}: {resp.text[:200]}"}
            data = resp.json()
            return {
                "success": True,
                "platform": "myzap",
                "chat_id": number,
                "message_id": str(data.get("messageId") or (data.get("mensagem") or {}).get("id") or data.get("id") or ""),
            }
    except Exception as exc:
        return {"error": f"myzap standalone send failed: {exc}"}


def _env_enablement() -> dict | None:
    if not _api_key_from() or not _profile_allowed():
        return None
    home = normalize_number(_env("MYZAP_HOME_NUMBER"))
    seed: Dict[str, Any] = {"base_url": _base_url_from(), "api_key": _api_key_from()}
    if _env("MYZAP_ALLOWED_NUMBERS"):
        seed["allowed_numbers"] = _env("MYZAP_ALLOWED_NUMBERS")
    if _env("MYZAP_POLL_INTERVAL_SECONDS"):
        seed["poll_interval_seconds"] = float(_env("MYZAP_POLL_INTERVAL_SECONDS"))
    if home:
        seed["home_channel"] = {"chat_id": home, "name": _env("MYZAP_HOME_CHANNEL_NAME", "MyZap Home")}
    return seed


def register(ctx) -> None:
    ctx.register_platform(
        name="myzap",
        label="MyZap",
        adapter_factory=lambda cfg: MyZapAdapter(cfg),
        check_fn=check_requirements,
        validate_config=validate_config,
        is_connected=is_connected,
        required_env=["MYZAP_BASE_URL", "MYZAP_API_KEY"],
        install_hint="Install in the Hermes profile that should use MyZap and set MYZAP_API_KEY in that profile .env",
        env_enablement_fn=_env_enablement,
        cron_deliver_env_var="MYZAP_HOME_NUMBER",
        standalone_sender_fn=_standalone_send,
        allowed_users_env="MYZAP_ALLOWED_USERS",
        allow_all_env="MYZAP_ALLOW_ALL_USERS",
        max_message_length=MAX_MESSAGE_LENGTH,
        emoji="💬",
        pii_safe=False,
        allow_update_command=False,
        platform_hint=(
            "You are replying through MyZap/WhatsApp. Use concise plain-text "
            "answers, do not expose credentials, and escalate uncertain billing, "
            "support, or system-change requests."
        ),
    )
