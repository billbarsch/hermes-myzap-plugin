# Contrato MyZap esperado pelo plugin

Base padrão: `https://api.myzap.net/api/v1`.

## Polling de mensagens

`GET /mensagens`

Parâmetros usados:

- `desde`: ISO UTC com milissegundos, ex. `2026-05-31T12:00:00.000Z`.
- `limite`: padrão `100`.
- `ordem`: `asc`.
- `cursor`: opcional; preserva `(criadoEm,id)` quando a API suportar.

Campos aceitos na resposta:

- lista direta, ou objeto com `mensagens`, `messages`, `dados`, `data`, `items` ou `registros`.

Campos aceitos por mensagem:

- texto: `conteudo`, `texto`, `mensagem`, `message` ou `body`.
- direção: `direcao`, `direction` ou `tipo`; inbound se contém `receb`, `inbound`, `entrada` ou `cliente`.
- id: `messageId`, `message_id`, `id` ou `idMensagem`.
- número: `numero`, `telefone`, `remoteJid`, `chatId`, `contato.numero` ou `conversa.numero`.
- conversa: `conversaId`, `conversa_id`, `chatId` ou `remoteJid`.
- data: `criadoEm`, `createdAt`, `timestamp`, `dataHora` ou `data`.
- anexos: `arquivos`, `arquivosMensagem`, `attachments` ou `media`.
- anexo individual: `nome`, `fileName`, `filename`, `tipo`, `type`, `mimeType`, `mime_type`, `url`, `link` ou `downloadUrl`.

Quando a mensagem vier sem texto, o adapter monta um resumo legível a partir
dos anexos para que o evento continue sendo processado pelo Hermes.

Quando a mensagem vier com anexos que tenham URL, o adapter repassa esses
endereços em `MessageEvent.media_urls` e os tipos em `MessageEvent.media_types`.
Anexos de áudio são transcritos quando houver chave de STT configurada no
perfil Hermes.

## Envio de texto

`POST /mensagens/texto`

JSON:

```json
{
  "numero": "5562999999999",
  "texto": "Mensagem em texto puro"
}
```

Header:

- `X-API-Key: <MYZAP_API_KEY>`

## Envio de mídia

`POST /mensagens/midia`

Multipart/form-data:

- `numero`: destino em formato internacional, ex. `5562999999999`.
- `legenda`: texto opcional enviado junto com a mídia.
- `arquivo`: arquivo binário único. Este campo é singular porque a API pública
  do MyZap recebe uma mídia por chamada nesta rota.
- `tipo`: opcional; use `documento` para forçar envio como documento quando
  aplicável.

Exemplo:

```bash
curl -X POST "$MYZAP_BASE_URL/mensagens/midia" \
  -H "X-API-Key: $MYZAP_API_KEY" \
  -F "numero=5562999999999" \
  -F "legenda=Arquivo enviado pelo Hermes" \
  -F "arquivo=@./contrato.pdf"
```

## Webhook futuro

Hermes plugin de plataforma não expõe rota HTTP própria nesta v0.1. Para webhook, usar um shim externo que:

1. recebe MyZap webhook;
2. valida HMAC com `verify_webhook_signature(secret, body, signature)`;
3. normaliza payload para o mesmo contrato acima;
4. entrega ao gateway Hermes quando houver endpoint aprovado.
