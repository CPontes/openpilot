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
| Da CAN, só os endereços que o DBC **deste carro** descreve (o que o openpilot lê dele) | Endereços fora do DBC; mensagens do DBC com sinal de posição, hora, data, odômetro, VIN, nome, telefone ou Bluetooth (`share.private_signal`); a CAN de diagnóstico (0x700–0x7FF e 0x18DA/DBxxxx); todo endereço que carrega um pedaço do VIN; todo endereço cujos quadros são na maior parte texto. **Carro sem DBC conhecido: nenhuma CAN** |
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

**Limites conhecidos:**
- Um DBC pode ter mensagem com dado pessoal e nome inocente. A lista de palavras (`PRIVATE_TOKENS`) cobre os nomes comuns; um carro novo merece uma olhada no DBC antes.
- A Supabase registra o IP de quem chama a função, nos logs da plataforma. O receptor não guarda IP.

## O comma

- Opção **enviar trechos aos desenvolvedores** (`HexaShareEnabled`, desligada por padrão), em Ajustes → Celular. Ela é o consentimento.
- No celular, cada marca ganha **enviar**. A confirmação diz o que vai. Os estados são: na fila → esperando Wi-Fi → enviado.
- Corta só com o carro desligado, num processo à parte com prioridade mínima (`nice 19`). Se o carro ligar no meio, o corte para e é refeito depois.
- Envia só em rede que não é medida (`deviceState.networkMetered`).
- Um log que não abre (minuto cortado quando a energia caiu) é tentado 3 vezes e não segura os outros da fila.
- A fila tem no máximo 20 trechos e 8 tentativas por trecho, com espera crescente. Cada trecho tem no máximo 8 MB (um Haval dá algumas centenas de KB).
- Código: `openpilot/selfdrive/hexa/share.py`, `system/hexaweb/hexaweb.py` (`/api/share*` e o `share_worker`).
- **`share.RECEIVER_URL` está vazio** até o receptor ir ao ar. Até lá, os trechos esperam no comma e o celular diz isso.

## O receptor (Supabase)

Os arquivos ficam nesta pasta:
- `handler.ts`: toda a lógica, sem plataforma;
- `index.ts`: a Edge Function;
- `schema.sql`: as tabelas e o bucket privado;
- `dev_server.ts`: o receptor em memória, num computador.

Para pôr no ar, o dono ou o CTO:
1. Cria um projeto no Supabase (de preferência, região São Paulo).
2. Roda `schema.sql` no SQL Editor. Ele cria duas tabelas com RLS ligado e sem política, e o bucket privado `lizpilot-shares`.
3. Faz o deploy da função com `supabase functions deploy lizpilot-share --no-verify-jwt`. O `--no-verify-jwt` é porque a autenticação é a assinatura do comma, não o JWT do Supabase.
4. Opcional: para receber uma issue a cada envio, cria um repositório **privado** (ex.: `CPontes/lizpilot-envios`) e configura os segredos da função:
   - `LIZPILOT_GITHUB_TOKEN`: um token fine-grained só com Issues: write nesse repositório;
   - `LIZPILOT_GITHUB_REPO`: `CPontes/lizpilot-envios`.
5. Põe a URL da função em `share.RECEIVER_URL` (`https://<projeto>.supabase.co/functions/v1/lizpilot-share`) num commit no canal.

Limites (`handler.ts` `DEFAULTS`, por dia UTC):
- 8 MB por envio;
- 30 envios por comma, 500 no total e 1 GB no total;
- 100 commas novos;
- relógio com até 300 s de diferença. Se o do comma estiver errado, o receptor responde a hora dele e o comma assina de novo.

O cadastro é aberto (qualquer comma pode ajudar). Por isso:
- os limites existem;
- motivo e anotação ficam como texto puro: no aviso, nenhuma menção, link ou imagem age, e no terminal nenhum caractere de controle age.

Ver "Pontos para o CTO" em `codeweb-06`: travar o cadastro com um segredo da build, e anti-replay dentro dos 300 s.

## Os desenvolvedores

```bash
export LIZPILOT_SUPABASE_URL=https://<projeto>.supabase.co
export LIZPILOT_SUPABASE_KEY=<service role>     # só no ambiente, nunca em arquivo de repositório
python tools/lizpilot/envios.py listar --carro toyota
python tools/lizpilot/envios.py baixar novos    # em ~/lizpilot-envios/<id>/rlog.zst + manifest.json
```

O `rlog.zst` abre nas ferramentas de sempre: `LogReader`, plotjuggler, `openpilot/tools/hexa_replay.py` e `timeline.read_rlog`.

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
