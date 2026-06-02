# hermes-myzap-plugin

Plugin de plataforma Hermes Agent para integrar o MyZap (WhatsApp) ao perfil exclusivo `pontoatendente` do Geranet Ponto.

Status: v0.1 text-only, instalável/testável localmente, sem credenciais no repositório.

## O que faz

- Registra a plataforma `myzap` via `ctx.register_platform(...)`.
- Recebe mensagens por polling incremental em `GET {MYZAP_BASE_URL}/mensagens`.
- Envia respostas por `POST {MYZAP_BASE_URL}/mensagens/texto`.
- Deduplica mensagens por `messageId`/`id`.
- Ignora mídia no v0.1 (não baixa/anexa arquivos automaticamente).
- Usa allowlist do próprio Hermes (`MYZAP_ALLOWED_USERS`/`MYZAP_ALLOW_ALL_USERS`) e allowlist opcional do adapter (`MYZAP_ALLOWED_NUMBERS`).
- Falha fechado fora do perfil `pontoatendente` quando `HERMES_PROFILE`/`HERMES_PROFILE_NAME` estiver definido.

## Variáveis de ambiente

Defina somente no `.env` do perfil `pontoatendente`, nunca no repositório:

```env
MYZAP_BASE_URL=https://api.myzap.net/api/v1
MYZAP_API_KEY=[REDACTED]
MYZAP_HERMES_PROFILE=pontoatendente
MYZAP_ALLOWED_USERS=5562999999999
MYZAP_HOME_NUMBER=5562999999999
MYZAP_POLL_INTERVAL_SECONDS=10
MYZAP_POLL_LOOKBACK_SECONDS=120
```

O adapter também aceita:

- `MYZAP_ALLOWED_NUMBERS`: filtro local por números, separado por vírgula.
- `MYZAP_ALLOW_ALL_USERS=true`: libera autorização no gateway Hermes (use só em ambiente controlado).
- `MYZAP_ALLOW_ALL_NUMBERS=true`: desliga o filtro local do adapter.
- `MYZAP_CURSOR`: cursor inicial opcional.

## Instalação local para desenvolvimento

```bash
cd /workspace/hermes-myzap-plugin
python3 -m venv .venv
. .venv/bin/activate
pip install -e '.[test]'
pytest -q
```

## Instalação no perfil `pontoatendente`

Opção A — pacote editável/local:

```bash
# Sem push/publicação; apontar para o checkout revisado.
PATH=/root/.local/bin:$PATH pontoatendente plugins install /workspace/hermes-myzap-plugin
```

Opção B — diretório de plugin do perfil:

```bash
mkdir -p /root/.hermes/profiles/pontoatendente/plugins/myzap
cp -R plugin.yaml src/hermes_myzap_plugin/* /root/.hermes/profiles/pontoatendente/plugins/myzap/
```

Depois, habilite somente no profile `pontoatendente`:

```bash
PATH=/root/.local/bin:$PATH pontoatendente config set plugins.enabled '["myzap"]'
PATH=/root/.local/bin:$PATH pontoatendente config set platforms.myzap.enabled true
```

E coloque as variáveis no `.env` do perfil `pontoatendente`. Não coloque no profile Diretor, Programador, Desenvolvimento ou outros.

## Config YAML equivalente

```yaml
plugins:
  enabled:
    - myzap
platforms:
  myzap:
    enabled: true
    extra:
      base_url: "https://api.myzap.net/api/v1"
      poll_interval_seconds: 10
      poll_lookback_seconds: 120
```

`MYZAP_API_KEY` deve ficar no `.env`, não no `config.yaml`.

## Teste rápido sem credenciais reais

```bash
HERMES_PROFILE=pontoatendente MYZAP_API_KEY=[REDACTED] MYZAP_BASE_URL=https://api.myzap.net/api/v1 \
python - <<'PY'
from hermes_myzap_plugin.adapter import check_requirements
print(check_requirements())
PY
```

## Limites v0.1

- Sem download/envio de mídia pelo fluxo do agente.
- Sem rota HTTP própria de webhook. O arquivo `adapter.py` inclui `verify_webhook_signature(...)` para um shim futuro validar HMAC antes de repassar eventos.
- Polling usa a rota incremental do MyZap; se a API mudar contrato de payload, ajuste os helpers `extract_messages(...)` e campos de mensagem.

## Segurança operacional

- Não versionar `.env`, tokens, prints de payloads reais ou conversas reais.
- Não habilitar o plugin fora de `pontoatendente`.
- Não usar `MYZAP_ALLOW_ALL_USERS=true` em produção sem regra explícita de atendimento.
- Não alterar financeiro, ERP, deploy ou configurações MyZap a partir deste plugin.
