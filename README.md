# hermes-myzap-plugin

Plugin de plataforma para conectar agentes do Hermes Agent ao MyZap, permitindo que um agente receba e responda mensagens do WhatsApp usando a API do MyZap.

Ele funciona como um conector de plataforma, no mesmo papel de conectores como Telegram, WhatsApp, Discord ou outros canais de atendimento: você instala no perfil Hermes desejado, configura a chave da API do MyZap e habilita a plataforma `myzap`.

Status: v0.1 text-only, instalável/testável localmente, sem credenciais no repositório.

## O que faz

- Registra a plataforma `myzap` via `ctx.register_platform(...)`.
- Recebe mensagens por polling incremental em `GET {MYZAP_BASE_URL}/mensagens`.
- Envia respostas por `POST {MYZAP_BASE_URL}/mensagens/texto`.
- Deduplica mensagens por `messageId`/`id` e persiste cursor/estado para evitar replay após restart.
- Preserva destinos de widget público no formato `widget_<hash>`.
- Ignora mídia no v0.1; mensagens sem texto não são baixadas nem anexadas automaticamente.
- Usa allowlist do Hermes (`MYZAP_ALLOWED_USERS`/`MYZAP_ALLOW_ALL_USERS`) e allowlist opcional do adapter (`MYZAP_ALLOWED_NUMBERS`).
- Pode ser restringido a um perfil Hermes específico com `MYZAP_HERMES_PROFILE`, mas por padrão aceita qualquer perfil em que for instalado/configurado.

## Requisitos

- Hermes Agent com suporte a plugins de plataforma.
- Python 3.10 ou superior.
- Acesso a uma API MyZap compatível com o contrato em `docs/API_CONTRACT.md`.
- Uma chave de API MyZap (`MYZAP_API_KEY`).

## Variáveis de ambiente

Defina as variáveis no `.env` do perfil Hermes que vai usar o MyZap. Não coloque credenciais no repositório.

Obrigatórias:

```env
MYZAP_BASE_URL=https://api.myzap.net/api/v1
MYZAP_API_KEY=coloque_sua_chave_aqui
```

Recomendadas:

```env
MYZAP_ALLOWED_USERS=5562999999999
MYZAP_HOME_NUMBER=5562999999999
MYZAP_POLL_INTERVAL_SECONDS=10
MYZAP_POLL_LOOKBACK_SECONDS=120
```

O adapter também aceita:

- `MYZAP_HERMES_PROFILE`: restringe o plugin a um perfil Hermes específico. Se não for definido, qualquer perfil configurado pode usar o plugin.
- `MYZAP_ALLOWED_NUMBERS`: filtro local por números, separado por vírgula.
- `MYZAP_ALLOW_ALL_USERS=true`: libera autorização no gateway Hermes. Use somente em ambiente controlado.
- `MYZAP_ALLOW_ALL_NUMBERS=true`: desliga o filtro local do adapter.
- `MYZAP_CURSOR`: cursor inicial opcional.
- `MYZAP_STATE_PATH`: caminho opcional para o arquivo de estado do polling.

## Instalação para desenvolvimento

```bash
git clone https://github.com/billbarsch/hermes-myzap-plugin.git
cd hermes-myzap-plugin
python3 -m venv .venv
. .venv/bin/activate
pip install -e '.[test]'
pytest -q
```

No Windows PowerShell:

```powershell
git clone https://github.com/billbarsch/hermes-myzap-plugin.git
cd hermes-myzap-plugin
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[test]"
.\.venv\Scripts\python.exe -m pytest -q
```

## Instalação em um perfil Hermes

Substitua `<perfil>` pelo perfil Hermes que deve operar o canal MyZap.

Opção A, pacote editável/local:

```bash
PATH=/root/.local/bin:$PATH <perfil> plugins install /caminho/para/hermes-myzap-plugin
```

Opção B, diretório de plugin do perfil:

```bash
mkdir -p /root/.hermes/profiles/<perfil>/plugins/myzap
cp plugin.yaml /root/.hermes/profiles/<perfil>/plugins/myzap/
cp -R src/hermes_myzap_plugin/* /root/.hermes/profiles/<perfil>/plugins/myzap/
```

Depois, habilite o plugin e a plataforma no perfil:

```bash
PATH=/root/.local/bin:$PATH <perfil> config set plugins.enabled '["myzap"]'
PATH=/root/.local/bin:$PATH <perfil> config set platforms.myzap.enabled true
```

Coloque `MYZAP_API_KEY` e demais variáveis no `.env` desse perfil. Para limitar o plugin a esse perfil, defina também:

```env
MYZAP_HERMES_PROFILE=<perfil>
```

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

Esse teste confirma apenas se o plugin consegue carregar e se as variáveis mínimas foram encontradas.

```bash
MYZAP_API_KEY=teste MYZAP_BASE_URL=https://api.myzap.net/api/v1 \
python - <<'PY'
from hermes_myzap_plugin.adapter import check_requirements
print(check_requirements())
PY
```

Para testar a restrição opcional por perfil:

```bash
HERMES_PROFILE=atendimento MYZAP_HERMES_PROFILE=atendimento MYZAP_API_KEY=teste \
python - <<'PY'
from hermes_myzap_plugin.adapter import check_requirements
print(check_requirements())
PY
```

## Contrato da API MyZap

O contrato esperado está documentado em `docs/API_CONTRACT.md`.

Resumo:

- `GET /mensagens` deve retornar mensagens em ordem incremental.
- `POST /mensagens/texto` deve aceitar `{ "numero": "...", "texto": "..." }`.
- A autenticação usa o header `X-API-Key`.

## Limites v0.1

- Envio de mídia disponível pelo fluxo do agente via `send_document`/`send_image_file`/`send_voice`/`send_video`, usando `POST /mensagens/midia`.
- Sem rota HTTP própria de webhook. O arquivo `adapter.py` inclui `verify_webhook_signature(...)` para um shim futuro validar HMAC antes de repassar eventos.
- Polling usa a rota incremental do MyZap; se a API mudar o contrato de payload, ajuste os helpers `extract_messages(...)` e os campos de mensagem.

## Segurança operacional

- Não versionar `.env`, tokens, prints de payloads reais ou conversas reais.
- Use allowlist em produção (`MYZAP_ALLOWED_USERS` e/ou `MYZAP_ALLOWED_NUMBERS`).
- Não use `MYZAP_ALLOW_ALL_USERS=true` em produção sem regra explícita de atendimento.
- Mantenha `MYZAP_API_KEY` apenas no `.env` do perfil Hermes ou em um secret manager.
