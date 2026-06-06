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

## Envio de texto

`POST /mensagens/texto`

JSON:

```json
{
  "numero": "5562999999999",
  "texto": "Mensagem em texto puro"
}
```

Também é aceito multipart/form-data com campo repetido `arquivos` para envio de
documentos, imagens, vídeos, áudios e figurinhas. O texto pode ser omitido
quando houver anexos.

Header:

- `X-API-Key: <MYZAP_API_KEY>`

## Webhook futuro

Hermes plugin de plataforma não expõe rota HTTP própria nesta v0.1. Para webhook, usar um shim externo que:

1. recebe MyZap webhook;
2. valida HMAC com `verify_webhook_signature(secret, body, signature)`;
3. normaliza payload para o mesmo contrato acima;
4. entrega ao gateway Hermes quando houver endpoint aprovado.
