"""Hexa missions: the tests the car still needs, as missions the owner takes along on a drive (owner, 02/11).

Two layers (owner, 05/11: "a sensação de avanço automático; se precisar, a gente devolve"):
1. the owner: what he does counts at once on the phone ("Fiz agora", "Deu certo", the parked answers), as "feita · a conferir";
2. the CTO and the team: check it in the log and accept it (a Try "ok"/"partial", and `done` raised) or send it back (a Try
   "back", or "bad", with the reason in `measured`): it counts no more and the phone says "devolvida: refazer" and why,
   until the owner does it again.

Each mission is one real test. What counts as done, and who says so:
- "auto": the phone sees it in the drive summaries the comma already keeps (lp-logic.js missionProgress), with the car off;
- "marca": it happens while driving and the comma can't tell by itself yet. The owner taps "Fiz agora": a mark with the hour,
  the place and the mission, so the CTO finds the minute in the log. The mark counts at once; the CTO accepts or returns it;
- "parado": the owner does it parked and answers on the phone (HexaMissions keeps the answer);
- "cto": the CTO measures it in a log. Only proofs checked by the CTO go in `done` (never the designer's own reports);
- "decisao": not a test, a choice of the owner's.

The CTO completes a "marca" or "cto" mission by raising `done` here, with the route that proved it in `proof`.
A test stays until it's proved, whatever version is in the car (owner, 03/11: "tem testes que servem para todas as versões").
A proved one comes back only when the CTO changes the code it tests: lower `done` and say why in `note`.
Keys never change once in use: the phone's answers point to them.

Nothing here runs with the car on: the catalog is static, the state is one small param, and the phone does the counting.
"""
from dataclasses import dataclass, replace

from openpilot.selfdrive.hexa import edition

MISSIONS_PARAM = "HexaMissions"
# as many as the owner picks for a drive (owner, 03/11), the route too: the copilot still brings one at a time, the one that fits
MAX_ACTIVE = 12
MAX_PACK = 12
# the owner's mission marks, kept for the history: a mark checked in the log stays, as "conferida" (owner, 03/11)
MAX_PENDING = 300
HOW = ("auto", "marca", "parado", "cto", "decisao")
DETECT = ("slow10", "night30", "highway", "km200", "marks10", "installed")
# the passenger's copilot (owner, 03/11): what the live data says fits now
WHEN = ("stop_free", "stop_queue", "stop_long", "follow_mid", "slow_follow", "moving", "city")
# what the passenger answered (owner, 03/11: D1), as the mark's label: only "done" and "ok" wait as a mission advanced,
# "bad" and "stop" wait as "não deu", never as progress. None of them completes a mission: the CTO checks it in the log
RESULTS = {"done": "missão feita", "ok": "missão: deu certo", "bad": "missão: não deu", "stop": "missão: parei o teste"}
ADVANCED = ("done", "ok")
# How a try went, checked in the log; "base" is the number before the change, to compare with
TRY_RESULTS = {"ok": "deu certo", "partial": "em parte", "bad": "não deu", "base": "referência", "back": "devolvida"}
# a try that sends the owner's marks back: they count no more, and the phone asks to do it again (lp-logic missionReturned)
RETURNED = ("back", "bad")

TRAILS: tuple[tuple[str, str], ...] = (
  ("direcao", "Direção"),
  ("acc", "Velocidade e ACC"),
  ("long", "Longitudinal do comma"),
  ("avisos", "Avisos e atenção"),
  ("celular", "Celular e painel"),
  ("coleta", "Coleta para a comma"),
  ("cobertura", "Cobertura"),
  ("versao", "Versão e envio à comma"),
)

# The system's level, by missions done out of all: the LizPilot's, never the driver's. Numbers only: a name like "Rodovia"
# read as a release, and could be reached with doors and turn signals, no highway at all (CTO review of 06)
LEVELS: tuple[tuple[float, str], ...] = ((0., ""), (.15, ""), (.29, ""), (.44, ""), (.62, ""), (.81, ""), (1., ""))


@dataclass(frozen=True)
class Mission:
  key: str
  trail: str
  title: str
  total: int
  how: str
  note: str = ""  # what is still missing, or where it was proved
  why: str = ""
  steps: tuple[str, ...] = ()
  option: str = ""  # an option to turn on parked before the drive (options.py key)
  lock: str = ""  # why it can't be taken yet
  drive: bool = False  # a "parado" answer about something that happens driving: asked after the drive
  detect: str = ""  # "auto": what the phone looks for in the summaries
  arg: str = ""  # the detector's argument (a commit)
  done: int = 0  # proved so far, by the CTO (raise it with the proof)
  proof: str = ""
  version: str = ""  # new in this version: the phone says "nova" while it's the one in the route
  route: int = 0  # its place in the next drive's route (the CTO's order); 0: only when the owner picks it
  when: str = ""  # copilot: the moment that fits it, seen live on the phone (WHEN)
  goal: str = ""  # what counts as performing well, to read each try's number against


@dataclass(frozen=True)
class Try:
  """One time the mission was checked in the log (owner, 03/11: "saber o quanto performou"). Only numbers and the route's
  short name: never a place."""
  # when the drive checked ended, "2026-11-03T18:30": the owner's marks up to it were checked with this try. A day alone
  # would take a later drive's marks too, so only a "base" (it checks none) may have no time
  date: str
  route: str
  version: str
  result: str  # TRY_RESULTS
  measured: str = ""


CATALOG: tuple[Mission, ...] = (
  # *** Direção ***
  Mission("base", "direcao", "Primeira volta com a versão base", 1, "cto", done=1, proof="30/10 · melhor que a do Sato"),
  Mission("ajuda-mesmo-sentido", "direcao", "Ajuda no mesmo sentido não solta o volante", 1, "cto", done=1, proof="rota 7 · torque mantido em 88–93%"),
  Mission("estrada", "direcao", "Estrada de 60 a 90 km/h sem cair", 1, "cto", done=1, proof="rota 16 · 98,8% engajado"),
  Mission("filtro-baixa", "direcao", "Filtro de baixa velocidade agindo", 1, "cto", done=1, proof="rota 14 · tremida em 58–65% da de antes"),
  Mission("balanco-normal", "direcao", "Balanço normal de novo", 1, "cto", done=1, proof="rota 2c · nota 9"),
  Mission("mao-na-curva", "direcao", "O volante segue a sua mão na curva", 1, "cto", done=1, proof="rota 29 · 0 de 55 contra a mão"),
  Mission("sinuosa", "direcao", "Rua sinuosa da rota diária medida", 1, "cto", done=1, proof="rota 0f · falta força em 9 de 19"),
  Mission("curva-fechada", "direcao", "Curva fechada medida", 1, "cto", done=1, proof="rota 16 · não é o torque"),
  Mission("asfalto-ruim", "direcao", "Asfalto ruim medido", 1, "cto", note="aguarda a conferência da medição"),
  Mission("transito-lento", "direcao", "Trânsito lento atrás de outro carro", 1, "auto", when="slow_follow", detect="slow10",
          note="10 min abaixo de 30 km/h com o openpilot no volante",
          why="É a dúvida mais antiga da direção: o balanço abaixo de 30 km/h.",
          steps=("Siga um carro no trânsito lento por 10 minutos.", "Não precisa fazer nada diferente: o comma conta sozinho.")),
  Mission("posicao-faixa", "direcao", "Posição na faixa ligada, sem balanço", 1, "marca", option="LaneOffsetEnabled",
          note="1 viagem com ela ligada", why="Ela foi corrigida depois da rota 2b. Falta uma viagem com ela ligada.",
          steps=("Ligue a posição na faixa, parado.", "Dirija normalmente. Se balançar, marque.")),
  Mission("sempre-ativa", "direcao", "Direção sempre ativa", 4, "parado", option="AlwaysOnLateral", drive=True,
          note="frear, volante ativo, alavanca, cancelar", why="Com ela, frear não desliga a direção. Só o cancelar desliga.",
          steps=("Parado, ligue e reinicie o comma.", "Andando, freie: o volante continua ativo?", "Puxe a alavanca: o ACC volta?",
                 "Empurre a alavanca: tudo desliga?")),
  Mission("ponto-cego", "direcao", "Troca de faixa: o ponto cego bate", 1, "parado", drive=True,
          note="a luz do retrovisor bate com o aviso", why="Sem isso, a seta em via de mão dupla pode levar à contramão.",
          steps=("Com um carro no ponto cego, veja se a luz do retrovisor e o aviso no comma acendem juntos.",)),
  Mission("assistente-haval", "direcao", "Curva com o assistente da Haval agindo", 1, "marca",
          note="openpilot desligado, com marca", why="Diz se a direção aceita mais força que hoje.",
          steps=("Com o openpilot desligado e o assistente de faixa da Haval ligado, faça uma curva.", "Toque em «Fiz agora».")),
  Mission("falha-falsa", "direcao", "Sem falha falsa ao forçar contra", 1, "marca", note="1 viagem sem falha com a sua mão"),
  Mission("perto-de-zero", "direcao", "Direção perto de 0 km/h", 1, "marca", note="falta a ida e volta perto do 0"),
  Mission("devolver-volante", "direcao", "Devolver o volante aos poucos", 3, "marca", when="city", option="SoftHandoff",
          version="00e5ba7", route=1, done=3, proof="rota 32 · 0 falhas em 862 vezes com a mão no volante",
          goal="nenhuma falha da direção e nenhum tranco ao forçar",
          why="Hoje, uma força leve contra a curva corta a força do comma de uma vez. Com a opção, ela sai aos poucos.",
          steps=("Numa curva de cidade, até 60 km/h, com o openpilot no volante, faça uma força leve contra.",
                 "Certo: o volante cede aos poucos, sem tranco, e volta quando você solta.",
                 "Se aparecer falha da direção, marque «não deu». Parado, desligue a opção.")),
  Mission("modo-sombra", "direcao", "Sinuosa com o modo sombra", 3, "marca", note="3 passagens", lock="Depende do modo sombra (LEARN-01)"),
  # *** Velocidade e ACC ***
  Mission("alavanca-parada", "acc", "A alavanca tira o carro da parada", 4, "cto", done=4, proof="rota 16 · sem acelerador"),
  Mission("cancelar-alavanca", "acc", "Cancelar pela alavanca", 5, "cto", done=5, proof="rotas 16 e 1c · com o ACC ativo"),
  Mission("retomar", "acc", "Retomar sem falha de ACC", 6, "cto", done=6, proof="rota 29"),
  Mission("descida", "acc", "Sem freada falsa em descida", 1, "cto", done=1, proof="rota 2c"),
  Mission("sair-sozinho", "acc", "Sair sozinho quando o da frente anda", 5, "marca", when="stop_queue", option="AutoResume", done=1, proof="rota 1c",
          why="Já deu certo 1 vez. Com 5, sai o «sem prova».",
          steps=("Pare atrás de um carro com o ACC ativo.", "Espere ele andar. Pé pronto no freio.", "Se não sair, acelere: vale como tentativa.")),
  Mission("cancelar-comma", "acc", "Cancelar pelo comma", 5, "marca", lock="Ainda não liberado para teste"),
  # 00e5ba7 (03/11): two options, off until the owner turns them on
  Mission("parar-perto", "acc", "Parar mais perto do carro da frente", 5, "marca", when="slow_follow", option="StopCloser",
          version="00e5ba7", route=2, done=5, proof="rota 32 · mediana de 3,7 m em 36 paradas", goal="parar a até 4,0 m do carro da frente",
          why="O comma parava a 4,7 m do carro da frente, e a Haval para a uns 3 m. Com a opção, o esperado é uns 4 m.",
          steps=("No trânsito lento, deixe o comma parar sozinho atrás de um carro.", "Certo: para um pouco mais perto que antes, sem tranco.",
                 "Se chegar perto demais ou frear forte, freie e marque «não deu».")),
  # the CTO's route for b3ac6fe (02/11), in its order: what isn't proved yet stays in the route
  Mission("parar-de-vez", "acc", "Sinal vermelho sem carro à frente", 3, "marca", when="stop_free", option="StopFinish", version="b3ac6fe", route=3,
          why="O principal desta versão: sem carro à frente, o comma termina a parada e espera o gás.",
          steps=("Deixe o modelo frear sozinho, sem carro atrás colado.",
                 "Certo: ele para de vez e só sai quando você tocar no acelerador.",
                 "O aviso «Parado: toque no acelerador para sair» só aparece se o modelo quiser sair. Sem aviso também é normal.",
                 "A regra só atua abaixo de 3 km/h: ele ainda pode passar da faixa. Se for passar, freie.")),
  Mission("freio-alavanca", "acc", "Freio e alavanca na parada", 1, "marca", when="stop_free", option="StopFinish", version="b3ac6fe", route=4,
          why="A parada de vez precisa aguentar o freio e a alavanca.",
          steps=("Só no vermelho, depois de o teste do sinal ter parado o carro sozinho, sem você pisar antes.", "Toque no freio e puxe a alavanca.",
                 "Certo: o carro continua parado até você tocar no acelerador.")),
  Mission("sinal-abrindo", "acc", "Sinal abrindo", 1, "marca", when="stop_free", option="StopFinish", version="b3ac6fe", route=5,
          steps=("Ainda sem carro à frente, quando abrir, toque de leve no acelerador.", "Certo: ele sai normalmente.")),
  Mission("fila", "acc", "Fila atrás de carro", 1, "marca", when="stop_queue", version="b3ac6fe", route=6, done=1,
          proof="rota 32 · saiu junto, sem pedal",
          steps=("Pare atrás de um carro e deixe ele sair.", "Certo: o seu sai junto, sem você tocar em nada. Se ficar parado, marque.")),
  Mission("lombada-faixa", "acc", "Lombada e faixa de pedestre", 1, "marca", when="moving", version="b3ac6fe", route=7,
          steps=("Certo: ele reduz e segue.", "Se ficar parado sem motivo, marque.")),
  Mission("frear-antes", "acc", "Carro da frente freando", 3, "marca", when="follow_mid", option="LeadClosing", version="b3ac6fe", route=8,
          done=1, proof="rota 32 · parou a 3 m, sem pedal",
          why="Usa a distância que a Haval mede para começar a frear mais cedo.", note="desde 00e5ba7, lê a distância da Haval em metros",
          steps=("Siga um carro a 40–60 km/h em avenida.", "Marque «freada estranha» se frear à toa ou muito em cima.")),
  Mission("parada-longa", "acc", "Parada longa e subida", 1, "marca", when="stop_long", version="b3ac6fe", route=9, note="se acontecer naturalmente",
          steps=("Parada de 1 a 2 minutos, ou parada em subida.", "Certo: fica firme, sem soltar nem descer. Se for descer ou soltar, freie e marque.")),
  Mission("ruas-casa", "direcao", "Ruas perto de casa", 1, "marca", when="moving", version="b3ac6fe", route=10,
          why="As marcas de balanço servem para comparar uma versão com a outra.",
          steps=("Só marque «balançou» quando acontecer.",)),
  Mission("lombada", "acc", "Reduzir na lombada", 3, "marca", when="moving", option="BumpSlowdown"),
  Mission("aviso-acc", "acc", "Aviso de ACC que saiu sozinho", 1, "parado", option="AccLostAlert", drive=True,
          note="o som tocou numa queda real", why="O ACC da Haval às vezes sai sozinho. O aviso precisa tocar quando isso acontece."),
  Mission("arrancada", "acc", "Arrancada com ritmo", 3, "marca", note="rota 29: falta ritmo"),
  Mission("rota-diaria", "acc", "Gravar a rota de todo dia", 1, "marca", note="base para reduzir antes da curva"),
  # *** Longitudinal do comma ***
  Mission("long-primeira", "long", "Primeira volta com ele", 1, "cto", done=1, proof="rota 23"),
  Mission("long-re", "long", "Ré com freio", 1, "cto", done=1, proof="rota 26"),
  # no count on the red light: the stops with no car ahead still end on the owner's pedal, and on route 2e the model
  # pulled away on a red (CTO review of 06)
  Mission("long-sinal", "long", "Sinal vermelho visto", 1, "cto", note="as paradas sem carro à frente ainda acabam no seu pedal"),
  Mission("long-sem-freada", "long", "Uma viagem sem freada forte sua", 1, "cto"),
  Mission("long-aeb", "long", "O AEB da Haval segue ativo", 1, "cto", lock="Só no log. Nunca provoque o AEB"),
  # *** Avisos e atenção ***
  Mission("aviso-radar", "avisos", "Aviso de radar antes do radar", 3, "marca", option="RadarAlert"),
  Mission("aviso-saiu", "avisos", "Aviso de que o carro da frente saiu", 3, "marca", option="LeadDepartAlert"),
  Mission("atencao", "avisos", "Atenção: sem óculos, escuros, celular", 3, "parado", note="em P, com a câmera interna gravando",
          steps=("Olhe à frente, para o rádio e para o comma.", "Repita de óculos escuros.", "Repita com o celular na mão.")),
  # *** Celular e painel ***
  Mission("marcar-10", "celular", "Marcar 10 momentos numa viagem", 1, "auto", detect="marks10", done=1, proof="rota 26 · 27 marcas"),
  Mission("parear-casa", "celular", "Parear no Wi-Fi de casa", 1, "parado"),
  Mission("parear-hotspot", "celular", "Parear pelo hotspot do comma", 1, "parado"),
  Mission("ponto-radar", "celular", "Salvar um ponto de radar", 1, "parado", note="parado, com GPS"),
  Mission("ajuste-sincroniza", "celular", "Ajuste no celular muda no comma", 1, "parado"),
  Mission("replay", "celular", "Replay de uma viagem", 1, "parado"),
  Mission("sem-conexao", "celular", "Sem conexão e volta sozinho", 1, "parado", note="Wi-Fi fora por 10 s"),
  Mission("lizpilot-local", "celular", "lizpilot.local abre o painel", 1, "parado"),
  Mission("perfil", "celular", "Perfil e cópia dos ajustes", 2, "parado"),
  Mission("video-marca", "celular", "Vídeo da marca", 1, "parado", note="com o carro desligado"),
  Mission("historico", "celular", "Histórico, mapa e ocultar", 1, "parado"),
  Mission("android", "celular", "Android", 1, "parado", note="se tiver um"),
  # *** Coleta para a comma (VALID-01, UP-01): the checklist's car signals, read once in a log. All of it in one stop of
  # about 15 minutes, car on and the comma recording: a mark before each one is how the CTO finds the second in the log ***
  Mission("cinto", "coleta", "Cinto: tirar e pôr", 1, "parado", note="o comma grava o sinal",
          why="A comma confere se o aparelho lê o cinto do motorista.",
          steps=("Carro ligado, em P, com o comma gravando.", "Toque em Marcar.", "Solte o cinto, espere 5 s e prenda de novo.")),
  Mission("portas", "coleta", "Portas: abrir e fechar cada uma", 4, "parado",
          why="A comma confere se o aparelho vê cada porta aberta.",
          steps=("Carro ligado, em P. Toque em Marcar.", "Abra a porta do motorista, espere 3 s e feche.",
                 "Depois a do passageiro, a traseira esquerda e a traseira direita, uma de cada vez.", "Por último, o porta-malas.")),
  Mission("freio-mao", "coleta", "Freio de mão e auto hold", 2, "parado",
          why="Com o freio de mão ou o auto hold segurando, o openpilot não pode tentar andar.",
          steps=("Em P, toque em Marcar. Puxe o freio de mão, espere 5 s e solte.",
                 "Ligue o auto hold. Num sinal, pare até a luz do auto hold acender, marque e saia normalmente.")),
  Mission("cambio", "coleta", "Câmbio: P, R, N, D", 1, "parado", note="pé no freio",
          why="Uma posição que o comma não conhece nunca pode virar D.",
          steps=("Pé no freio, toque em Marcar.", "Passe P, R, N e D, parando 3 s em cada.",
                 "Se o câmbio tiver outra posição (S, B ou M), passe por ela também.", "Volte a P.")),
  Mission("pedais", "coleta", "Pedais: freio e acelerador", 2, "parado", note="em P",
          why="A comma confere se o aparelho lê o começo e o fim de cada pedal.",
          steps=("Em P, toque em Marcar.", "Pise o freio até o fim, devagar, e solte devagar.",
                 "Pise o acelerador até a metade, devagar, e solte.")),
  Mission("piscas", "coleta", "Piscas: esquerda, direita, alerta", 3, "parado",
          steps=("Toque em Marcar.", "Seta para a esquerda 5 s, depois para a direita 5 s.", "Pisca-alerta 5 s.")),
  Mission("cego-coleta", "coleta", "Ponto cego: um carro de cada lado", 2, "marca", note="andando, só observar",
          why="A luz do retrovisor e o que o comma lê precisam bater.",
          steps=("Numa avenida, quando a luz do ponto cego acender, toque em Fiz agora.", "Uma vez de cada lado.")),
  Mission("modos-conducao", "coleta", "Modos de condução do PHEV", 1, "parado", note="EV, HEV, Sport e os outros",
          why="A comma quer saber se algum modo muda o que o aparelho lê ou o que o ACC faz.",
          steps=("Em P, toque em Marcar.", "Passe por cada modo do botão de modos, parando 3 s em cada.",
                 "Na resposta, escreva quais modos o carro tem.")),
  Mission("ligar-desligar", "coleta", "Ligar e desligar o carro 3 vezes", 3, "parado",
          why="A comma confere se o aparelho reconhece o carro e engata depois de cada partida, sem aviso no painel.",
          steps=("Desligue o carro e espere 1 minuto.", "Ligue e espere o comma mostrar a tela de dirigir.",
                 "Veja se o painel do carro mostra algum aviso.", "Repita 3 vezes. Na última, saia e engate o openpilot.")),
  Mission("carro-dormindo", "coleta", "Carro dormindo: o comma desliga sozinho", 1, "parado", note="carro trancado 30 min",
          why="O comma não pode acordar o carro nem gastar a bateria de 12 V parado.",
          steps=("Desligue e tranque o carro por 30 minutos.", "Ao voltar, veja se a tela do comma está apagada.",
                 "Ligue o carro: o comma reconhece o carro de primeira?")),
  Mission("botoes-acc", "coleta", "Botões do ACC: +, −, distância e cancelar", 4, "cto",  # CTO, 05/11: no phone while driving
          why="A comma confere se cada botão chega certo ao aparelho.",
          steps=("Numa reta, com o ACC ligado e o trânsito permitindo.", "Aperte + uma vez, depois − uma vez, depois a distância uma vez, com uns 5 s entre cada um.",
                 "Por último, empurre a alavanca para cancelar.", "Não precisa tocar no celular andando: o comma grava cada botão e o CTO confere no log.")),
  Mission("sem-fingerprint", "coleta", "O comma reconhece o carro sozinho", 3, "parado",
          lock="Precisa de uma versão de teste que não força o reconhecimento (CTO)",
          why="Hoje o aparelho é avisado de que é um Haval. A comma exige que ele descubra sozinho, toda vez.",
          steps=("Instale a versão de teste que o CTO indicar.", "Com o carro parado há pelo menos 1 hora, ligue.",
                 "Veja se o comma engata normalmente.", "Repita em 3 dias diferentes.")),
  # *** Cobertura ***
  Mission("rodovia", "cobertura", "Rodovia", 1, "auto", detect="highway", done=1, proof="rota 16"),
  Mission("cidade", "cobertura", "Cidade", 1, "cto", done=1, proof="rota 29"),
  Mission("tunel", "cobertura", "Túnel", 1, "cto", done=1, proof="rota 0f"),
  Mission("noite", "cobertura", "Noite", 1, "auto", detect="night30", note="30 min depois do pôr do sol"),
  Mission("chuva", "cobertura", "Chuva", 1, "parado", drive=True, note="você diz que choveu"),
  Mission("km200", "cobertura", "200 km numa mesma semana", 1, "auto", detect="km200", note="para a comparação de versões"),
  # *** Versão e envio à comma ***
  # arg: filled by public() with the newest version in versions.py, so it never waits for an old one (it did for b3ac6fe)
  Mission("instalar", "versao", "Instalar a versão nova", 1, "auto", detect="installed", note="Software, Check for update"),
  Mission("subir-logs", "versao", "Subir as viagens dos testes", 1, "parado", note="para medir no log"),
  Mission("branches", "versao", "Apagar as branches arquivadas", 1, "decisao"),
  Mission("rota-publica", "versao", "Rota pública para a comma", 1, "decisao", note="privacidade: você escolhe"),
  Mission("checklist-comma", "versao", "Checklist da comma", 52, "cto", done=2, proof="UP-01"),
)
# The generic edition (edition.py) offers only what any car can test, from zero: the rest is the Haval's own test plan,
# and its proofs and tries are the owner's drives.
GENERIC_TRAILS = ("avisos", "celular")
if edition.EDITION == "generic":
  CATALOG = tuple(replace(m, done=0, proof="", note="") for m in CATALOG if m.trail in GENERIC_TRAILS and not m.route)

BY_KEY = {m.key: m for m in CATALOG}

# What was checked in the log, per mission, oldest first. Checking a mission is adding a Try here (and raising `done`)
TRIES: dict[str, tuple[Try, ...]] = {
  "base": (Try("2026-10-30T23:59", "rota 1", "7d2bedb", "ok", "melhor que a do Sato"),),
  "ajuda-mesmo-sentido": (Try("2026-10-31T11:00", "rota 7", "1bd6c53", "ok", "torque mantido em 88–93%"),),
  "estrada": (Try("2026-11-01T12:56", "rota 16", "17554ba", "ok", "98,8% engajado"),),
  "filtro-baixa": (Try("2026-11-01T11:56", "rota 14", "e1241a1", "ok", "tremida abaixo de 22 km/h em 58–65% da de antes"),),
  "balanco-normal": (Try("2026-11-02T13:10", "rota 2c", "3b759f6", "ok", "nota 9"),),
  "mao-na-curva": (Try("2026-11-02T09:50", "rota 29", "d522577", "ok", "0 de 55 contra a mão"),),
  "sinuosa": (Try("2026-11-01T10:28", "rota 0f", "1bd6c53", "partial", "falta força em 9 de 19"),),
  "curva-fechada": (Try("2026-11-01T12:56", "rota 16", "17554ba", "ok", "não é o torque"),),
  "alavanca-parada": (Try("2026-11-01T12:56", "rota 16", "17554ba", "ok", "4 de 4 sem acelerador"),),
  "cancelar-alavanca": (Try("2026-11-01T12:56", "rota 16", "17554ba", "ok", "1 de 1 com o ACC ativo"),
                        Try("2026-11-01T16:58", "rota 1c", "3070d0e", "ok", "4 de 4 com o ACC ativo")),
  "retomar": (Try("2026-11-02T09:50", "rota 29", "d522577", "ok", "6 de 6 sem falha de ACC"),),
  "descida": (Try("2026-11-02T13:10", "rota 2c", "3b759f6", "ok", "sem freada falsa"),),
  "sair-sozinho": (Try("2026-11-01T16:58", "rota 1c", "3070d0e", "ok", "1 saída sozinho"),),
  "long-primeira": (Try("2026-11-01T21:02", "rota 23", "5be82e8", "partial", "funciona, mas não está pronto para o trânsito"),),
  "long-re": (Try("2026-11-01T23:17", "rota 26", "544108f", "ok", "ré com freio"),),
  "marcar-10": (Try("2026-11-01T23:17", "rota 26", "544108f", "ok", "27 marcas"),),
  "rodovia": (Try("2026-11-01T12:56", "rota 16", "17554ba", "ok"),),
  "cidade": (Try("2026-11-02T09:50", "rota 29", "d522577", "ok"),),
  "tunel": (Try("2026-11-01T10:28", "rota 0f", "1bd6c53", "ok"),),
  # before the option: the number to compare with
  "parar-perto": (Try("2026-11-03", "rota 31", "f203426", "base", "parou a 4,7 m, sem a opção"),
                  Try("2026-11-03T20:05", "rota 32", "5c47e8f", "ok", "mediana de 3,7 m em 36 paradas")),
  "devolver-volante": (Try("2026-11-03T20:05", "rota 32", "5c47e8f", "ok", "0 falhas em 862 vezes com a mão no volante"),),
  "fila": (Try("2026-11-03T20:05", "rota 32", "5c47e8f", "ok", "saiu junto, sem pedal"),),
  "frear-antes": (Try("2026-11-03T20:05", "rota 32", "5c47e8f", "ok", "parou a 3 m, sem pedal; a parte da Haval não medida"),),
}

# The next drive's route (owner, 03/11): one route, not one per version. The new tests come first, then what an older version
# asked and isn't proved yet. It goes along with what the owner picked, never in its place.
CURRENT = "00e5ba7"
ROUTE_KEY = "proxima"
OLD_ROUTES = ("b3ac6fe",)  # a route chosen away from the comma before this version still means this route
ROUTE: dict = {
  "version": CURRENT,
  "minutes": 30,
  "intro": "Pé pronto no freio o tempo todo. As opções novas estão em teste e não leem o sinal: quem garante a parada é você.",
  "before": ("Em Software, Check for update: se houver versão nova, instale antes.", "Ligue abaixo só as opções que quiser testar nesta volta.",
             "Desligue e ligue o carro para as opções valerem."),
  "during": "Só faça cada teste quando o trânsito permitir, e marque pelo celular logo depois.",
  "stop_if": ("O carro sair sem você tocar no acelerador num teste de parada.", "O carro soltar o freio ou descer parado.",
              "O carro frear forte sem motivo mais de uma vez.", "O volante dar tranco ou aparecer falha da direção quando você força."),
  "old": OLD_ROUTES,
  "back": "Para voltar, desligue a opção com o carro parado e reinicie. Se ainda ficar ruim, a versão anterior é 0d09b50 + bf02b93.",
  "after": "Quando terminar, envie os logs das viagens: é neles que tudo é conferido.",
}


def newest_version(running: str = "") -> str:
  """What "Instalar a versão nova" waits for: the newest version in versions.py. Its top entry has commit "" (a commit can't
  hold its own hash): that one is the version this comma runs, so the running commit stands for it (CTO, 05/11)."""
  from openpilot.selfdrive.hexa import versions
  top = versions.RELEASES[0].commit if versions.RELEASES else ""
  if not top and running:
    return str(running)[:7]
  return next((r.commit for r in versions.RELEASES if r.commit), "")


def public(running: str = "") -> dict:
  """What the phone needs, texts in Portuguese: the phone translates them. running: the comma's GitCommit."""
  trails = [(k, t) for k, t in TRAILS if any(m.trail == k for m in CATALOG)]
  tries = {} if edition.EDITION == "generic" else TRIES  # the owner's drives
  return {"trails": [{"key": k, "title": t} for k, t in trails], "levels": [list(lv) for lv in LEVELS], "max_active": MAX_ACTIVE,
          "route": dict({k: list(x) if isinstance(x, tuple) else x for k, x in ROUTE.items()}, key=ROUTE_KEY),
          "missions": [{"key": m.key, "trail": m.trail, "title": m.title, "total": m.total, "how": m.how, "note": m.note, "why": m.why,
                        "steps": list(m.steps), "option": m.option, "lock": m.lock, "drive": m.drive, "detect": m.detect,
                        "arg": newest_version(running) if m.detect == "installed" else m.arg,
                        "done": m.done, "proof": m.proof, "version": m.version, "route": m.route, "when": m.when, "goal": m.goal,
                        "tries": [vars(t) for t in tries.get(m.key, ())]} for m in CATALOG],
          "try_results": TRY_RESULTS}


def clean(raw) -> dict:
  """The phone's part of the state: what it takes along, what the owner answered, and the marks waiting for the CTO.
  A mission proved since it was taken leaves the trip, so it never holds one of the places (CTO review of the route)."""
  raw = raw if isinstance(raw, dict) else {}
  answers = raw.get("answers") if isinstance(raw.get("answers"), dict) else {}
  answers = {k: max(0, min(int(v), BY_KEY[k].total)) for k, v in answers.items()
             if k in BY_KEY and isinstance(v, int) and not isinstance(v, bool)}
  active = [k for k in raw.get("active", []) if isinstance(k, str) and k in BY_KEY and can_take(BY_KEY[k])
            and max(BY_KEY[k].done, answers.get(k, 0)) < BY_KEY[k].total]
  pending = [p for p in raw.get("pending", []) if isinstance(p, dict) and p.get("key") in BY_KEY][-MAX_PENDING:]
  return {"active": list(dict.fromkeys(active))[:MAX_PACK], "answers": answers, "pending": pending}


def can_take(m: Mission) -> bool:
  return not m.lock and (m.how in ("auto", "marca") or (m.how == "parado" and m.drive))


def take(state: dict, key: str) -> dict:
  """Raises ValueError with the reason the owner reads."""
  m = BY_KEY.get(key)
  if m is None:
    raise ValueError("Missão não encontrada.")
  if not can_take(m):
    raise ValueError("Esta missão não vai na viagem.")
  if key in state["active"]:
    return state
  if len(state["active"]) >= MAX_ACTIVE:
    raise ValueError("No máximo 12 missões por viagem.")
  return dict(state, active=state["active"] + [key])


def route_keys(answers: dict) -> list[str]:
  """The route's missions still open, in the CTO's order. The phone counts what the comma can't (lp-logic.js routeKeys)."""
  ms = [m for m in CATALOG if m.route and can_take(m) and max(m.done, answers.get(m.key, 0)) < m.total]
  return [m.key for m in sorted(ms, key=lambda m: m.route)]


def take_pack(state: dict, key: str) -> dict:
  """The route goes on the next drive after what the owner already picked, up to MAX_PACK."""
  if key != ROUTE_KEY and key not in OLD_ROUTES:
    raise ValueError("Roteiro não encontrado.")
  return dict(state, active=list(dict.fromkeys(state["active"] + route_keys(state["answers"])))[:MAX_PACK])


def drop(state: dict, key: str) -> dict:
  return dict(state, active=[k for k in state["active"] if k != key])


def answer(state: dict, key: str, ok: bool) -> dict:
  """The owner's answer, parked: one more done when it went well. A drive question leaves the trip list once answered."""
  m = BY_KEY.get(key)
  if m is None or m.how != "parado" or m.lock:
    raise ValueError("Esta missão não se responde no celular.")
  answers = dict(state["answers"])
  if ok:
    answers[key] = min(m.total, max(answers.get(key, 0), m.done) + 1)
  active = [k for k in state["active"] if k != key] if m.drive else state["active"]
  return dict(state, answers=answers, active=active)


def can_mark(m: Mission) -> bool:
  """What happens driving: "Fiz agora", or the copilot's answer. The same missions that go on a drive; parked ones are
  answered parked."""
  return can_take(m)


def add_pending(state: dict, key: str, mark_id: str, t: int, result: str = "done") -> dict:
  item = {"key": key, "mark": mark_id, "t": int(t), "result": result if result in RESULTS else "done"}
  return dict(state, pending=(state["pending"] + [item])[-MAX_PENDING:])
