# Envios do LizPilot: o momento de uma marca, para os desenvolvedores

O dono marca um momento no celular e toca em **enviar**. O comma corta o log daquele momento, tira o que identifica e manda para este receptor. Os desenvolvedores baixam e abrem como um rlog. Funciona no Haval e em qualquer carro do openpilot.

```text
celular (marca → enviar) → comma: corta, limpa, assina, espera Wi-Fi → receptor (Supabase) → aviso (issue privada) → envios.py → rlog
```

## O que vai e o que não vai

| Vai | Não vai |
|---|---|
| 30 s antes e 15 s depois da marca | O resto da viagem |
| `carState`, `carControl`, `carOutput`, `carParams` (sem VIN), `controlsState`, `selfdriveState`, `onroadEvents`, `longitudinalPlan`, `radarState`, `drivingModelData`, parâmetros aprendidos, `driverMonitoringState` (só o estado), `accelerometer`, `pandaStates` | GPS do comma (`gpsLocation*`, `ubloxGnss`, `qcomGnss`), câmeras, áudio, `initData` (dongle id, parâmetros), `logMessage`, `deviceState` e todo serviço que não está na lista |
| Da CAN, só os endereços que o DBC **deste carro** descreve (o que o openpilot lê dele) | Endereços fora do DBC; mensagens cujo nome é de posição, hora, odômetro ou VIN (`LOCAL_TIME`, `VIN_PART_1`, `APIMGPS_…`); a CAN de diagnóstico (0x700–0x7FF e 0x18DA/DBxxxx); todo endereço que carrega um pedaço do VIN; todo endereço cujos quadros são na maior parte texto. **Carro sem DBC conhecido: nenhuma CAN** |
| Mensagens com um sinal pessoal no meio (o `CRUISE` do Honda tem o odômetro) | Esse sinal, **zerado** no quadro (`share.masked`, com o `set_value` do opendbc). O checksum desse quadro deixa de bater, de propósito |
| Marca e modelo do carro, edição, branch, commit e versão do LizPilot | VIN, dongle id, serial do comma, nome do celular, dia e hora (só vai o mês). O receptor guarda só o dia da chegada |
| Motivo e anotação da marca | |

**Garantias** (todas testadas em `openpilot/selfdrive/hexa/tests/test_share.py`):
- Lista branca de serviços (`share.SERVICES`): um serviço novo do openpilot fica de fora até alguém incluí-lo.
- Antes de enviar, o comma procura no corte inteiro o VIN, o dongle id e o serial. Se achar qualquer um, o trecho **não sai**.
- A chave de envio é aleatória, criada no comma; não é o dongle id. O receptor não sabe de quem é o comma.
- "apagar meus envios" apaga a fila no comma e pede ao receptor que apague tudo o que aquela chave mandou; depois o comma cria uma chave nova.

**Medido no Haval** [log], em 15 minutos reais (`logs_data`, rotas 0, 1, 2, 7, 9, 11 e 12):
- cada trecho de 45 s deu de 0,7 a 1,7 MB (mediana 1,4 MB);
- foram de 59 a 87 pares barramento/endereço, todos do DBC do Haval;
- nenhum VIN apareceu no trecho;
- o filtro de texto só pegou 0x7AE/0x7AF, que são de diagnóstico e já saem.

Um CRC às vezes parece texto: `WHEEL_SPEEDS` chega a 11% dos quadros. Por isso um endereço só sai como texto com metade dos quadros ou mais.

**Em todos os carros do opendbc** [teste], `test_no_car_lets_a_clock_a_position_or_a_name_through`: nenhuma mensagem que sai tem, aberto, um sinal com nome de hora, posição, distância ou pessoa, **em inglês, em alemão** (os DBCs da VW: `UH_Jahr`, `KBI_Kilometerstand`, `IDT_Geheimnis`) **e nas abreviações da Hyundai/Kia** (`SMKFobID`, `CountryCfg`). Hoje só o Haval envia; isso é a base para as próximas marcas. Exemplos:
- Haval: as 29 mensagens saem; `REQ_REVIEW_ODOMETER` sai zerado;
- RAV4: saem 90 de 94 (ficam de fora `TIME` e `VIN_PART_1..3`);
- Civic 2022: saem 52 de 53 (fica de fora `ODOMETER`);
- Kona EV: saem 86 de 88 (ficam de fora `LOCAL_TIME`);
- Bronco Sport: saem 395 de 405 (ficam de fora GPS, relógio e local de recarga), 26 delas com sinais zerados.

**Limites conhecidos:**
- Um DBC pode ter mensagem com dado pessoal e nome inocente. A lista de palavras (`PRIVATE_TOKENS`) cobre os nomes comuns; um carro novo merece uma olhada no DBC antes.
- A lista é por endereço, não por barramento: um endereço do DBC de um barramento também passa nos outros (com os mesmos sinais zerados).
- A Supabase registra o IP de quem chama a função, nos logs da plataforma. O receptor não guarda IP.
- A hora em que o pacote chega fica nos metadados do bucket e na issue. Por isso o comma espera um tempo aleatório de até 2 h depois do corte (`SEND_JITTER_S`): a chegada não diz quando o carro foi estacionado.
- Uma só pessoa pode ocupar os limites do dia (100 commas novos, 500 envios). Isso só fecha com limite por IP na borda ou com um segredo da build no cadastro: está nos pontos para o CTO.

## Decisões do dono (05/11, `DECISIONS.md`)

1. **Só no Haval** (`brands=edition.GWM_ONLY` e `share.BRANDS`). Outra marca, só com as mensagens do DBC dela revisadas à mão.
2. **Só sobe com o carro desligado**: o corte e o envio.
3. **A nota vai só se o dono escolher**, num diálogo que mostra o texto. Um motivo que ele mesmo escreveu conta como nota. Nunca vai na issue do GitHub.
4. **Cadastro fechado por um segredo da build** (`share.BUILD_SECRET`): só um comma com LizPilot se cadastra. O segredo está num repositório público: barra o que não é LizPilot, não quem lê o código. Por isso os limites diários ficam.
5. **Receptor num projeto Supabase novo e exclusivo, em São Paulo** (a região da Supabase no Brasil).
6. **Retenção de 12 meses**, com exclusão automática.
7. **Desligar a opção apaga a fila** que ainda não saiu, com os arquivos `.lzs`.

## O comma

- Opção **enviar trechos aos desenvolvedores** (`HexaShareEnabled`, desligada, só no Haval), em Ajustes → Celular. Ela é o consentimento.
- No celular, cada marca ganha **enviar**:
  - a confirmação diz o que vai;
  - se a marca tem nota, um segundo diálogo mostra o texto e pergunta se inclui;
  - estados: na fila → pronto para enviar → ✓ enviado. Tocar no enviado mostra o **código do envio**.
- Corta e envia só com o carro desligado. O corte roda num processo à parte com prioridade mínima (`nice 19`) e para se o carro ligar. Um corte que passa de 180 s conta como tentativa.
- Envia só em rede que não é medida (`deviceState.networkMetered`), num momento aleatório dentro de 2 h.
- Um log que não abre é tentado 3 vezes e não segura os outros. A fila tem no máximo 20 trechos, com 8 tentativas por trecho.
- `share.RECEIVER_URL` aceita só `https`. Fica vazio até o receptor ir ao ar; até lá os trechos esperam e o celular diz isso.

## O receptor (Supabase), passo a passo

Quem cria o projeto é o dono:

1. Em supabase.com, crie um projeto **novo, só para isto**, na região **South America (São Paulo)**.
2. No SQL Editor, rode `schema.sql`. Ele cria duas tabelas com RLS ligado e sem política, e o bucket privado `lizpilot-shares` (8 MB por arquivo).
3. Em Edge Functions → Secrets, crie:
   - `LIZPILOT_BUILD_SECRETS`: o valor de `share.BUILD_SECRET`. Separe por vírgula para aceitar uma versão antiga e uma nova;
   - `LIZPILOT_PURGE_SECRET`: uma palavra longa e aleatória, para a exclusão automática;
   - `LIZPILOT_READER_SECRET`: outra palavra longa e aleatória, só para os desenvolvedores lerem;
   - opcional: `LIZPILOT_GITHUB_TOKEN`, um token fine-grained só com Issues: write num repositório **privado** (ex.: `CPontes/lizpilot-envios`), e `LIZPILOT_GITHUB_REPO`.
4. Faça o deploy: `supabase functions deploy lizpilot-share --no-verify-jwt`. A autenticação é a assinatura do comma, não o JWT da Supabase.
5. **Retenção:** em Database → Extensions, ligue `pg_cron` e `pg_net`. Rode o bloco comentado no fim de `schema.sql`: ele guarda a palavra de exclusão no Vault e agenda a chamada diária a `/v1/purge`, que apaga pacotes e linhas com mais de 365 dias.
6. Ponha a URL da função (`https://<projeto>.supabase.co/functions/v1/lizpilot-share`) em `share.RECEIVER_URL`, num commit no canal.

**Limites** (`handler.ts` `DEFAULTS`, por dia UTC):
- 8 MB por envio;
- 30 envios por comma, 500 no total e 1 GB no total;
- 100 commas novos;
- relógio com até 300 s de diferença. Se o do comma estiver errado, o receptor responde a hora dele e o comma assina de novo.

**O receptor guarda:** o dia da chegada (não a hora), o carro, a versão, o motivo e o pacote. A nota só se o dono escolheu. Nunca o dongle id nem o IP. A Supabase registra o IP nos logs da plataforma, e a hora da chegada fica nos metadados do bucket; a espera aleatória de até 2 h serve para isso.

**Pedido de exclusão sem o comma** (o comma foi resetado e a chave se perdeu): o dono mostra o **código do envio** que o celular exibiu. Quem administra apaga a linha e o pacote pelo painel da Supabase. Os dados são pseudonimizados (chave aleatória), não anônimos. Por isso há retenção e exclusão.

## Os desenvolvedores

```bash
export LIZPILOT_SHARE_URL=https://<projeto>.supabase.co/functions/v1/lizpilot-share
export LIZPILOT_READER_KEY=<LIZPILOT_READER_SECRET>   # só lê: listar e baixar. Nunca a chave service role
python tools/lizpilot/envios.py listar --carro gwm
python tools/lizpilot/envios.py baixar novos    # em ~/lizpilot-envios/<id>/rlog.zst + manifest.json
```

- O `envios.py` recusa pacote maior que 9 MB e descompactado maior que 256 MB.
- O `rlog.zst` abre nas ferramentas de sempre: `LogReader`, plotjuggler, `openpilot/tools/hexa_replay.py` e `timeline.read_rlog`.

**Os envios são dados de quem dirigiu.** Nunca vão para repositório público nem para commit. Ficam no Supabase, na pasta local e, se for o caso, como anexo de Release no repositório privado de coordenação.

## Testes

```bash
node --test --experimental-strip-types tools/lizpilot/receiver/handler.test.ts        # o receptor
python -m pytest openpilot/selfdrive/hexa/tests/test_share.py \
  openpilot/selfdrive/hexa/tests/test_share_receiver.py \
  openpilot/selfdrive/hexa/tests/test_envios.py \
  openpilot/system/hexaweb/tests/test_hexaweb.py -k "share or envios"
```

`test_share_receiver.py` sobe o `dev_server.ts` e manda um corte de verdade, pelo HTTP, do `Sharer` do comma para o `handler.ts`. Assim os dois lados ficam testados juntos: a assinatura, o pacote, a correção do relógio e o apagar.
