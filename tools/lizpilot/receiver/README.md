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
| A rede CAN do carro como ele a transmite | A CAN de diagnóstico (0x700–0x7FF e 0x18DA/DBxxxx, onde o VIN é pedido) e todo endereço que carrega um pedaço do VIN |
| Marca e modelo do carro, edição, branch, commit e versão do LizPilot | VIN, dongle id, serial do comma, nome do celular, dia e hora (só vai o mês) |
| Motivo e anotação da marca | |

**Garantias** (todas testadas em `openpilot/selfdrive/hexa/tests/test_share.py`):
- Lista branca de serviços (`share.SERVICES`): um serviço novo do openpilot fica de fora até alguém incluí-lo.
- Antes de enviar, o comma procura no corte inteiro o VIN, o dongle id e o serial. Se achar qualquer um, o trecho **não sai**.
- A chave de envio é aleatória, criada no comma; não é o dongle id. O receptor não sabe de quem é o comma.
- "apagar meus envios" apaga a fila no comma e pede ao receptor que apague tudo o que aquela chave mandou; depois o comma cria uma chave nova.

**Limite conhecido** [hipótese]: alguns carros transmitem na própria CAN a posição da central multimídia ou o odômetro. A CAN vai como o carro a manda (é o que se precisa para portar e ajustar um carro). A opção diz isso ao dono. Se um carro conhecido fizer isso, inclua o endereço numa lista de exclusão por marca em `share.py`.

## O comma

- Opção **enviar trechos aos desenvolvedores** (`HexaShareEnabled`, desligada por padrão), em Ajustes → Celular. Ela é o consentimento.
- No celular, cada marca ganha **enviar**. A confirmação diz o que vai. Os estados são: na fila → esperando Wi-Fi → enviado.
- Corta só com o carro desligado. Envia só em rede que não é medida (`deviceState.networkMetered`).
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

Limites (`handler.ts` `DEFAULTS`):
- 8 MB por envio;
- 50 envios por comma por dia, e 2000 por dia no total;
- 200 commas novos por dia;
- relógio com até 300 s de diferença. Se o do comma estiver errado, o receptor responde a hora dele e o comma assina de novo.

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
