# hermes-myzap-plugin

Plugin de plataforma para conectar agentes do Hermes Agent ao MyZap, permitindo que um agente receba e responda mensagens do WhatsApp usando a API do MyZap.

Ele funciona como um conector de plataforma, no mesmo papel de conectores como Telegram, WhatsApp, Discord ou outros canais de atendimento: você instala no perfil Hermes desejado, configura a chave da API do MyZap e habilita a plataforma `myzap`.

Status: v0.2, instalável/testável localmente, sem credenciais no repositório.

## O que faz

- Registra a plataforma `myzap` via `ctx.register_platform(...)`.
- Recebe mensagens por polling incremental em `GET {MYZAP_BASE_URL}/mensagens`.
- Envia respostas por `POST {MYZAP_BASE_URL}/mensagens/texto`.
- Recebe anexos do MyZap e repassa URLs/tipos de mídia ao Hermes quando disponíveis.
- Baixa áudios recebidos para o cache do Hermes e entrega como `MessageType.VOICE`, usando o mesmo fluxo central de transcrição dos conectores Telegram/WhatsApp.
- Envia documentos, imagens, vídeos e áudios por `POST {MYZAP_BASE_URL}/mensagens/midia`.
- Deduplica mensagens por `messageId`/`id` e persiste cursor/estado para evitar replay após restart.
- Preserva destinos de widget público no formato `widget_<hash>`.
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

Para transcrição de áudio, use a configuração `stt` padrão do Hermes. O plugin MyZap apenas cacheia o áudio recebido e entrega o evento como voz para o gateway.

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

## Agrupamento de mensagens de texto

O plugin aguarda mensagens curtas antes de encaminhá-las ao agente. Se a mesma
pessoa enviar várias mensagens em sequência, o temporizador é reiniciado a cada
mensagem e o conteúdo é entregue em um único evento, separado por quebras de
linha. Isso evita respostas fragmentadas para sequências como “oi”, “bom dia” e
“estou com uma dúvida”.

Os padrões são:

- `10` segundos após a última mensagem para textos com até `1.024` caracteres;
- `15` segundos após a última mensagem para textos maiores;
- mídias, como áudio, imagem, vídeo e documentos, continuam sendo encaminhadas
  imediatamente para o pipeline do Hermes.

O comportamento segue o padrão de agrupamento dos adaptadores oficiais do
Hermes. Caso seja necessário ajustar uma instalação específica, use no `extra`
do MyZap as chaves `text_batch_delay_seconds`,
`text_batch_split_delay_seconds` e `text_batch_long_threshold`. Os mesmos dois
atrasos também podem ser definidos pelas variáveis
`MYZAP_TEXT_BATCH_DELAY_SECONDS` e `MYZAP_TEXT_BATCH_SPLIT_DELAY_SECONDS`.
Definir o atraso aplicável como `0` desativa a espera para aquela faixa.

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
- `POST /mensagens/midia` deve aceitar multipart/form-data com campo único `arquivo`.
- Anexos recebidos podem vir em `arquivos`, `arquivosMensagem`, `attachments` ou `media`.
- A autenticação usa o header `X-API-Key`.

## Mídia e Áudio

- Envio de mídia disponível pelo fluxo do agente via `send_document`/`send_image_file`/`send_voice`/`send_video`, usando `POST /mensagens/midia`.
- Recebimento de mídia preenche `MessageEvent.media_urls` e `MessageEvent.media_types` quando o MyZap fornece URL do anexo.
- Respostas citadas preservam `MessageEvent.reply_to_message_id` e
  `MessageEvent.reply_to_text`; o envio de texto e mídia aceita `reply_to`.
- Áudios recebidos são baixados para o cache local do Hermes e enviados ao gateway como `MessageType.VOICE`.
- A transcrição é feita pelo pipeline central do Hermes, conforme a configuração `stt` do perfil.

## Escalonamento e pausa por operador

- Mensagens recebidas de widgets carregam no `channel_context` o link direto da conversa quando a API fornece `widgetPublicId` e `visitanteId`.
- O agente deve copiar esse link no resumo enviado ao superior pelo fluxo de escalonamento; o link abre a conversa correta no MyZap.
- Uma mensagem manual enviada pelo painel do MyZap para um widget pausa o atendimento automático daquela conversa por 30 minutos. O intervalo pode ser alterado com `MYZAP_OPERATOR_PAUSE_SECONDS` ou `operator_pause_seconds`.
- As mensagens enviadas pelo próprio Hermes são identificadas pelo `messageId` retornado pela API e não ativam a pausa. Mensagens recebidas durante a pausa continuam no histórico, mas não são encaminhadas ao agente.
- A pausa é mantida em memória do adaptador e expira sozinha; o cursor incremental continua persistido para evitar reprocessamento após reinício.

## Limites atuais

- Sem rota HTTP própria de webhook. O arquivo `adapter.py` inclui `verify_webhook_signature(...)` para um shim futuro validar HMAC antes de repassar eventos.
- Polling usa a rota incremental do MyZap; se a API mudar o contrato de payload, ajuste os helpers `extract_messages(...)` e os campos de mensagem.

## Segurança operacional

- Não versionar `.env`, tokens, prints de payloads reais ou conversas reais.
- Use allowlist em produção (`MYZAP_ALLOWED_USERS` e/ou `MYZAP_ALLOWED_NUMBERS`).
- Não use `MYZAP_ALLOW_ALL_USERS=true` em produção sem regra explícita de atendimento.
- Mantenha `MYZAP_API_KEY` apenas no `.env` do perfil Hermes ou em um secret manager.
