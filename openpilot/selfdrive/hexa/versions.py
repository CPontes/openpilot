"""Hexa versions: what each channel version brought, for the phone's "Versões" screen (owner, 03/11).

One entry per version that went to the channel (docs/STATUS.md, "Histórico do canal"), newest first. The CTO adds the
new one when he promotes the channel: the commit, the date, what changed in the owner's words, and whether the panda
changed. Nothing here runs with the car on: the phone reads it once, with the rest of the panel.
"""
from dataclasses import dataclass


@dataclass(frozen=True)
class Release:
  commit: str  # openpilot, 7 characters, as the drive summaries keep it
  date: str  # dd/mm
  opendbc: str
  title: str
  changes: tuple[str, ...]
  panda: bool = False  # the safety code in the panda changed
  driving: bool = True  # steering, speed or brakes changed (False: the phone only)


RELEASES: tuple[Release, ...] = (
  Release("516d695", "03/11", "2060c4e", "Um roteiro só de missões",
          ("Um roteiro só para a próxima viagem: os testes novos primeiro, depois os que faltam provar",
           "Você escolhe quantas missões cabem numa viagem, até 12", "«Ainda não feitas» lista todas as abertas"), driving=False),
  Release("00e5ba7", "03/11", "2060c4e", "Devolução suave do volante e modo sombra",
          ("Devolução suave do volante (SoftHandoff), desligada", "Modo sombra «só a Haval», só no log",
           "Frear antes lendo a distância da Haval em metros")),
  Release("0d09b50", "03/11", "bf02b93", "Missões em Viagens e copiloto do passageiro",
          ("Missões dentro de Viagens", "Copiloto do passageiro com o carro andando", "Cópia dos dados no celular e fila de missões",
           "Mapas fora do celular por padrão"), driving=False),
  Release("3d6b08c", "03/11", "bf02b93", "Parada mais perto do carro da frente",
          ("Parada mais perto do carro da frente (StopCloser), desligada", "Comma × Haval no celular")),
  Release("f203426", "03/11", "bf02b93", "Caminho para as Missões",
          ("Pílula das Missões no cartão do comma", "Missões logo abaixo do comma no Início"), driving=False),
  Release("8c09e17", "03/11", "bf02b93", "Missões e números do celular",
          ("Missões com trilhas, roteiro da versão e Fiz agora", "Números das 4 semanas: openpilot no volante, balanço em reta, alertas"),
          driving=False),
  Release("b3ac6fe", "02/11", "bf02b93", "Terminar a parada sem carro à frente",
          ("Sem carro à frente, termina a parada e espera o gás (StopFinish), desligada",)),
  Release("a635314", "02/11", "bf02b93", "Frear antes com a distância da Haval",
          ("Frear antes com a distância da Haval (LeadClosing), desligada",)),
  Release("3b759f6", "02/11", "70a970a", "Posição na faixa e freio em ladeira",
          ("Posição na faixa não segue linhas piscando", "Ladeira fora do valor do freio", "Freio de ladeira passa para o pedal"), panda=True),
  Release("00a40b8", "02/11", "dce68d2", "Freio passa para o pedal",
          ("Freio passa para o pedal como o da Haval", "Bônus na arrancada atrás de carro que sai", "Posição na faixa no log"), panda=True),
  Release("d522577", "02/11", "2f30d45", "Mão na curva e histórico no celular",
          ("O volante segue a sua mão ao aliviar na curva", "Histórico, mapa e ocultar viagens no celular")),
  Release("de23241", "02/11", "2f30d45", "Retomada mais suave",
          ("Retomada espera o ACC aceitar", "Gás sobe aos poucos depois do freio")),
  Release("bde7f0f", "02/11", "39b478c", "Telas e comandos do celular",
          ("Telas e comandos do celular (entrega 03), sem Rede",), driving=False),
  Release("f680297", "02/11", "39b478c", "Freio e parada",
          ("Freio fica no modo freio depois de aliviar", "Fim da rolagem na parada", "Freio para o ponto de parada do modelo")),
  Release("544108f", "01/11", "6d9d56b", "Volante, acelerador e freio separados na viagem",
          ("A viagem separa volante, acelerador e freio", "Ré com freio sem TAKE CONTROL", "Longitudinal do comma com regeneração"), panda=True),
  Release("be24496", "01/11", "73f9a26", "Marcar oval e aviso de ACC",
          ("Botão Marcar oval", "Aviso de ACC que saiu sozinho (AccLostAlert), desligado"), driving=False),
  Release("5be82e8", "01/11", "73f9a26", "LizPilot",
          ("O painel passa a se chamar LizPilot", "Sinal de ACC ativo", "Limite real do botão − do ACC"), panda=True),
  Release("dea997c", "01/11", "4658448", "Lombada com velocidade",
          ("Lombada com velocidade de passagem", "Reduzir na lombada (BumpSlowdown), desligada", "Botão − do ACC")),
  Release("48ecad6", "01/11", "5c3643b", "Radares por toques e direção sempre ativa",
          ("Radares e lombadas por toques", "Direção sempre ativa liberada, desligada", "Cancelar do comma em toques")),
  Release("3070d0e", "01/11", "3a8bc90", "Celular ampliado",
          ("Celular ampliado: conexão, toques, instalar, aba Agora", "Painel fora dos processos da direção"), driving=False),
  Release("dbb532e", "01/11", "3a8bc90", "Sair sozinho",
          ("Sair sozinho quando o da frente anda (AutoResume), desligada",), panda=True),
  Release("d9727c0", "01/11", "72eec02", "Aviso de radar",
          ("Aviso de radar, desligado", "Aba Radares no celular"), driving=False),
  Release("83e2fba", "01/11", "72eec02", "Marcas só depois de Salvar",
          ("A marca só existe depois de Salvar", "Comentário opcional em todos os motivos"), driving=False),
  Release("17554ba", "01/11", "72eec02", "Pacote 2",
          ("Mensagens do carro conferidas", "Retomada pela alavanca", "Posição na faixa", "Aviso de saída da faixa", "Troca de faixa",
           "Painel em abas")),
  Release("5bbf194", "01/11", "e0749f7", "Painel: editar marcas",
          ("Caixa de texto em «outro»", "Editar marcas", "Marcas no topo"), driving=False),
  Release("e1241a1", "01/11", "e0749f7", "Filtro em baixa velocidade e painel do celular",
          ("Filtro do pedido até 30 km/h no lugar do corte de torque", "Primeiro painel do celular")),
  Release("2fd5622", "01/11", "578199d", "Fim da falha falsa",
          ("Forçar contra o openpilot não conta mais como falha da direção",)),
  Release("df2d9d5", "01/11", "5e48a4e", "Metade do torque em baixa velocidade",
          ("Metade do torque abaixo de 15 km/h",)),
  Release("1bd6c53", "31/10", "f5b5864", "Correção do volante",
          ("O volante não solta quando você ajuda no mesmo sentido",)),
  Release("7d2bedb", "30/10", "9d7b315", "Primeira versão",
          ("O código do H6 GT sobre o openpilot da comma",)),
)
BY_COMMIT = {r.commit: r for r in RELEASES}


def public() -> dict:
  return {"releases": [{"commit": r.commit, "date": r.date, "opendbc": r.opendbc, "title": r.title, "changes": list(r.changes),
                        "panda": r.panda, "driving": r.driving} for r in RELEASES]}
