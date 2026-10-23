# Entrega de relatorios no widget

O envio avulso (`hermes send`) preserva identificadores `widget_` com exatamente 14 caracteres hexadecimais minusculos. Nao os normaliza como telefone. Enderecos de widget invalidos sao rejeitados antes de chamar a API. Telefones continuam sendo normalizados como antes.

Isso vale para texto e anexos, inclusive XLSX com legenda. O envio usa a mesma API v1 e a chave do proprio perfil, que verifica a titularidade da conversa. Nao exige iniciar sessao WhatsApp para uma conversa de widget.

```sh
hermes --profile atendenteagilcontabil send --to myzap:widget_DESTINO_VALIDO \
  "Relatorio solicitado
MEDIA:/caminho/absoluto/relatorio.xlsx" --json
```

Substituir o exemplo pelo destino real da propria conversa. `-f` le um arquivo de texto; nao e uma opcao de upload binario.

O identificador da mensagem tambem e lido do formato publico `dados.mensagem.id` da API v1, preservando os formatos antigos. Confirmar o anexo registrado no MyZap e sua disponibilidade antes de declarar entrega; sucesso HTTP nao comprova leitura pelo cliente.

Depois que todos os uploads forem aceitos, o envio avulso retorna `media_delivered=true`, sinal previsto pelo Hermes para nao acrescentar o aviso incorreto de que a midia foi omitida. Uma falha de upload continua retornando erro, sem esse sinal. Para o comando avulso, mantenha uma legenda junto da linha `MEDIA:`; algumas versoes do Hermes rejeitam mensagens sem texto antes de chamar plugins.
