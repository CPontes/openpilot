"""The comma's own settings, from the phone: what each one is, who may change it and how.

Three levels, owner's request and CTO review (UI-09):
- "cel": the phone changes it, with the car off or in P.
- "so": the phone only shows it. Changed on the comma alone.
- "trav": locked for now, with the reason and what is missing.

Texts are the comma's own (selfdrive/ui/mici/layouts/settings/*.py), in Portuguese, plus a line of ours where the
comma doesn't say everything. Keys are the same Params the comma screen writes, with the same side effects.
"""
from dataclasses import dataclass, field

from openpilot.selfdrive.hexa import edition

LEVELS = ("cel", "so", "trav")
GROUPS: tuple[tuple[str, str], ...] = (
  ("prefs", "Preferências do comma"),
  ("dev", "Desenvolvedor"),
)


@dataclass(frozen=True)
class CommaItem:
  key: str  # Params key
  group: str
  title: str
  description: str
  level: str
  lock: str = ""  # a Param that locks it on the comma too (e.g. RecordFrontLock)
  restart: bool = False  # applies on the next start of the car
  reboot: bool = False  # applies when the comma restarts
  dev_only: bool = False  # hidden on release builds, like the comma screen
  warnings: tuple[str, ...] = field(default_factory=tuple)
  blocked: str = ""  # for "trav": why


# What the logs show between the comma and the Haval's ACC, for the owner to choose (CTO, 03/11, routes 23 to 31).
# Measured on the Haval: the generic edition (edition.py) keeps only the warnings true for any car.
HAVAL_LONG_WARNINGS = (
  "Freio mais forte: o comma vai até −3,5 m/s². O ACC da Haval já chegou a −4,8 m/s² nos seus logs.",
  "Frenagem de emergência da Haval: não provada com o comma no controle. O mais provável é que ela não atue com esta opção ligada.",
  "Distância do carro da frente: o radar da Haval mede. O comma estima pela câmera e pode errar com moto ou carro parado.",
)
AEB_STEP = {"h6gt": "Frenagem de emergência do Haval provada com o openpilot", "generic": "Frenagem de emergência do carro provada com o openpilot"}

ITEMS: tuple[CommaItem, ...] = (
  # *** Preferências: selfdrive/ui/mici/layouts/settings/toggles.py ***
  CommaItem("IsMetric", "prefs", "usar unidades métricas", "Velocidades em km/h na tela do comma.", "cel"),
  CommaItem("AlwaysOnDM", "prefs", "monitor do motorista sempre ligado", "Monitora o motorista mesmo com o openpilot desligado.", "cel",
            lock="AlwaysOnDMLock"),
  CommaItem("OpenpilotEnabledToggle", "prefs", "ligar o openpilot",
            "Ligado: usa a assistência do openpilot. Desligado: usa a assistência original do carro. Só na tela do comma.", "so"),
  # *** Desenvolvedor: selfdrive/ui/mici/layouts/settings/developer.py ***
  CommaItem("ShowDebugInfo", "dev", "depuração da tela", "Mostra toques e quadros por segundo na tela do comma.", "cel", reboot=True),
  CommaItem("NavigationEnabled", "dev", "navegação (alfa)", "Rota com mapa. O destino é escolhido no comma connect. Só na tela do comma.", "so",
            dev_only=True),
  CommaItem("AdbEnabled", "dev", "ADB", "Abre o comma por USB ou pela rede (Android Debug Bridge). Só na tela do comma.", "so"),
  CommaItem("JoystickDebugMode", "dev", "joystick", "Dirigir o carro com um joystick, para depuração. Só na tela do comma.", "so",
            dev_only=True),
  CommaItem("LongitudinalManeuverMode", "dev", "modo de manobra longitudinal", "Manobras automáticas para testar acelerador e freio. Só na tela do comma.",
            "so", dev_only=True),
  CommaItem("AlphaLongitudinalEnabled", "dev", "longitudinal do openpilot (alfa)",
            "Usar o controle de acelerador e freio do openpilot no lugar do ACC original.", "trav", dev_only=True,
            warnings=("AVISO: o longitudinal alfa pode desligar a frenagem automática de emergência (AEB).",) +
                     (() if edition.EDITION == "generic" else HAVAL_LONG_WARNINGS) +
                     ("O aviso de colisão do comma só toca o som: não freia além do plano.",),
            blocked="O celular não muda isso. Só na tela do comma."),
)

# What the Velocidade screen lists as missing before the longitudinal can be trusted (LONG-02 and the CTO's order).
LONG_STEPS: tuple[tuple[str, str], ...] = (
  ("sombra", "Modo sombra nas suas viagens"),
  ("aeb", AEB_STEP[edition.EDITION]),
  ("calibrado", "Acelerador e freio calibrados"),
  ("testes", "Testes de segurança do longitudinal"),
  ("pista", "Seu teste em pista livre"),
)

DEVICE_ACTIONS: tuple[tuple[str, str, str], ...] = (
  # (action, title, what it does) — selfdrive/ui/mici/layouts/settings/device/device_layout.py
  ("reboot", "reiniciar", "Reinicia o comma. O painel volta sozinho em cerca de 1 minuto."),
  ("poweroff", "desligar", "Desliga o comma. Liga de novo ao ligar o carro."),
  ("recalibrate", "recalibrar", "Recalibra a montagem do comma. Apaga também o que o carro aprendeu da direção: força, atrito, " +
                                "relação e atraso. Monte o comma até 4° para os lados e 5° para cima ou 9° para baixo."),
)
# device_layout.py:162-166: what "reset calibration" removes, then a new onroad cycle
RECALIBRATE_REMOVES = ("CalibrationParams", "LiveTorqueParameters", "LiveParametersV2", "LiveDelay")

def get_item(key: str) -> CommaItem | None:
  return next((i for i in ITEMS if i.key == key), None)


def visible(item: CommaItem, release: bool) -> bool:
  return not (release and item.dev_only)


def can_change(item: CommaItem, vs, store) -> tuple[bool, str]:
  """(allowed, reason): the phone's rule on top of the comma's. Car off or in P, never engaged."""
  if item.level in ("so", "trav"):
    return False, item.blocked or "Só na tela do comma."
  if item.lock and store.get_bool(item.lock):
    return False, "Travado no comma."
  if vs.onroad and not (vs.parked and vs.standstill and not vs.engaged):
    return False, "Só com o carro em P ou desligado."
  return True, ""


def effect(item: CommaItem) -> str:
  if item.reboot:
    return "Vale quando o comma reiniciar."
  if item.restart:
    return "Vale na próxima partida do carro."
  return "Vale agora."


def apply(item: CommaItem, value: bool, store) -> None:
  """Write it as the comma screen does."""
  store.put_bool(item.key, value)
