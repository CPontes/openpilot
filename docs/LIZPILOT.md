# LizPilot

LizPilot é o openpilot com o app LizPilot no celular e os recursos criados no projeto do GWM Haval H6 GT. Esta é a **edição genérica**, para qualquer carro que o openpilot suporta. Ela espelha o canal do Haval (`CPontes/openpilot`, branch `h6gt`) sem mexer nele.

> openpilot é um sistema de assistência ao motorista (nível 2). Mãos no volante e atenção o tempo todo. Nenhum recurso daqui troca o motorista.

## As duas edições

O código é o mesmo. O que muda fica em `openpilot/selfdrive/hexa/edition.py` e na marca do carro:

| | Haval (`h6gt`, canal) | Genérica (`lizpilot`, este repositório) |
|---|---|---|
| Carro | forçado como GWM Haval H6 | o openpilot reconhece sozinho (`launch_env.sh`) |
| Opções testadas só no Haval | aparecem | só num carro GWM |
| Posição na faixa e troca de faixa pela seta | valem | só num carro GWM, também no código |
| Missões | o plano de testes do Haval, com o histórico do dono | só as de avisos e de celular, do zero |
| Atualizar pelo celular | branch `h6gt` | branch `lizpilot` |

A marca vem do `CarParamsPersistent`, que o próprio openpilot grava quando reconhece o carro. Antes da primeira viagem ela é desconhecida, e a edição genérica trata o carro como **não GWM**.

**Para qualquer carro:**
- o app no celular: pareamento por QR, marcas com motivo, replay com linha do tempo, viagens, missões e indicadores;
- as opções do comma pelo celular e a atualização pelo celular;
- o aviso de radar e lombada (lista do Brasil, OpenStreetMap);
- o aviso de carro da frente saiu;
- o aviso de saída de faixa;
- a página LizPilot na tela do comma 4 e do comma 3X.

**Só GWM**, porque foi medido e ajustado no Haval:
- volante calmo em baixa velocidade e devolver o volante aos poucos;
- direção sempre ativa;
- posição na faixa pelo volante;
- troca de faixa pela seta;
- sair sozinho da parada, frear antes, parar de vez e parar mais perto;
- reduzir na lombada;
- aviso de ACC que saiu.

## Um GWM que o openpilot não reconhece

O Haval H6 GT ainda não tem as versões de firmware no opendbc. Para usar esta edição nele, crie no comma o arquivo `/data/lizpilot/fingerprint` com o nome do carro (ex.: `GWM_HAVAL_H6`). O `launch_env.sh` só aproveita letras maiúsculas, números e `_`.

## Atualizar a partir do canal

A branch `canal-h6gt` daqui é uma sequência de retratos do canal. Ela não carrega o histórico, porque o do openpilot é grande demais. A branch `lizpilot` mescla esses retratos.

```bash
tools/lizpilot/sync_canal.sh          # novo retrato do canal em canal-h6gt
git checkout -b sync-AAAAMMDD origin/lizpilot
git merge origin/canal-h6gt           # conflitos: só em edition.py e nos pontos marcados "generic"
LIZPILOT_EDITION=generic pytest openpilot/selfdrive/hexa/tests/test_edition.py
pytest openpilot/selfdrive/hexa/tests openpilot/system/hexaweb/tests   # roda como h6gt (conftest.py)
# PR para lizpilot. Revisão do CTO antes de mesclar.
```

## Revisão antes do ar

- **Branch `lizpilot`:** a que um comma instala. Só recebe mudança revisada e aprovada.
- **Branches `review/*` e `lizpilot-review-*`:** trabalho em revisão.
- **Branch `main`:** só este texto. Não instala.
