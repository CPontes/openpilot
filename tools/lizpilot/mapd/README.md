# mapd no LizPilot: como o binário chega ao comma

O mapd (github.com/pfeiferj/mapd, licença MIT) lê o OpenStreetMap sem internet e publica o limite da via (`mapdOut`). O LizPilot **não guarda o binário no repositório**. Quando o dono toca em "Instalar o mapd" no celular, o comma baixa a versão fixada em `openpilot/selfdrive/hexa/speed_limit.py` (`MAPD_URL`, `MAPD_SHA256`, `MAPD_VERSION`).

As estruturas em `openpilot/cereal/custom.capnp` foram copiadas do mapd no commit `8e5eee6` (21/09/2026). O binário precisa ser **desse mesmo schema**.

## O binário: o CTO compila o `8e5eee6`

Não há release do autor nesse commit (a última release é a v2.3.1, de outro schema). Então o CTO compila, num computador confiável:

```bash
git clone https://github.com/pfeiferj/mapd && cd mapd && git checkout 8e5eee6
# linux/arm64, estático; o alvo exato do Makefile não foi conferido nesta sessão [hipótese]
file <binário>        # ELF 64-bit LSB executable, ARM aarch64, statically linked
shasum -a 256 <binário>
```

Publique como anexo de uma Release do `CPontes/openpilot` (ex.: `mapd-8e5eee6`) e preencha, num commit no canal:

```python
MAPD_URL = "https://github.com/CPontes/openpilot/releases/download/mapd-8e5eee6/mapd"
MAPD_SHA256 = "<64 hex>"
```

Enquanto os dois estiverem vazios, o comma não baixa nada e o mapd não roda. `test_the_shipped_pins_are_empty_or_complete` impede deixar só um preenchido.

## O que o comma confere

- **Na instalação:** só `https://`, até 64 MB, sha256 igual ao fixado; grava com `fsync` antes de virar executável.
- **Sempre que o manager decide se o mapd roda:** o arquivo em `/data/lizpilot/mapd/mapd` precisa ter o sha256 fixado (`binary_ok`, cache por tamanho e mtime). Um binário trocado depois da instalação não roda.
- **As mensagens do mapd** são lidas dos bytes crus (`speed_limit.decode`): outra união ou bytes inválidos viram "sem limite", nunca uma exceção. Nem o `selfdrived` nem o painel põem `mapdOut` no SubMaster.

## Como roda

- `nice -n 10`, `GOMAXPROCS=2`; não é processo de direção (`NON_DRIVING_PROCESSES`): se cair, o carro não desengata.
- O aviso de limite tem prioridade `LOWEST`: abaixo do aviso de radar.

## Logs

- `mapdOut` (nome da rua e limite): só no rlog, nunca no qlog.
- `mapdExtendedOut` (posição e caminho à frente) e `mapdIn`: não gravados.

## Mapas

O comma lê `/data/openpilot/mapd_download_menu.json`, que está na raiz do repositório. Ele oferece:
- `br_state.RJ`: o estado do Rio, em 6 arquivos da grade de 2°;
- `nation.BR`: o Brasil inteiro.

O download parte do celular e só começa com o **carro desligado**, num **Wi-Fi** (rede não medida) e com **espaço livre** (2 GB para o Rio, 12 GB para o Brasil: valores de folga, os tamanhos reais não foram medidos [hipótese]). Se o carro ligar no meio, o painel manda `cancelDownload` uma vez.
