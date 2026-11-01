"""Hexa options: the single list of the fork's own settings.

The comma screen (selfdrive/ui/mici/layouts/settings/hexa.py) and the phone panel (system/hexaweb) are both
built from this list, so a new option only has to be added here. New options are off by default, except
fixes the car needs, which stay switchable so they can be turned off without an update.
"""
from dataclasses import dataclass, field

# (key, title) in display order
GROUPS: tuple[tuple[str, str], ...] = (
  ("direcao", "Direção"),
  ("velocidade", "Velocidade"),
  ("avisos", "Avisos"),
  ("celular", "Celular"),
)
GROUP_KEYS = tuple(k for k, _ in GROUPS)

# The comma's own longitudinal (AlphaLongitudinalEnabled) was never tested nor calibrated in this car. The phone
# says so when a speed bump asks for less than the Haval ACC goes. True only after the calibration (LONG-02).
LONG_CALIBRATED = False


@dataclass(frozen=True)
class HexaOption:
  key: str  # Params key
  group: str
  title: str  # short, lowercase like the rest of the comma 4 UI
  description: str
  kind: str = "bool"  # "bool" or "enum" (enum is stored as the choice index, an INT param)
  choices: tuple[str, ...] = field(default_factory=tuple)
  default: bool | int = False
  # Changes how the car drives: the phone may only change it with the car parked or off, and it is
  # applied on the next engagement (or the next start, if restart_needed).
  driving: bool = True
  restart_needed: bool = False
  # Some options can only be changed on the comma itself (e.g. turning the phone panel on).
  phone_writable: bool = True
  # Why the option can't be offered yet. It stays in the list so it isn't forgotten, hidden everywhere.
  blocked: str = ""
  # Still being proven in the car: the comma and the phone show an "em teste" tag next to it.
  testing: bool = False

  def coerce(self, value):
    """Validate a value coming from outside (the phone). Returns the value to store or raises ValueError."""
    if self.kind == "bool":
      if isinstance(value, bool):
        return value
      raise ValueError(f"{self.key}: expected true/false")
    if self.kind == "enum":
      if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value < len(self.choices):
        raise ValueError(f"{self.key}: expected 0..{len(self.choices) - 1}")
      return value
    raise ValueError(f"{self.key}: unknown kind {self.kind}")


OPTIONS: tuple[HexaOption, ...] = (
  HexaOption(
    key="LowSpeedFilter",
    group="direcao",
    title="volante calmo em baixa velocidade",
    description="Abaixo de 30 km/h, o volante ignora as tremidas pequenas do modelo e fica reto.\n" +
                "Curvas de verdade continuam passando, com um pequeno atraso.",
    default=True,
    restart_needed=True,
  ),
  HexaOption(
    key="AlwaysOnLateral",
    group="direcao",
    title="direção sempre ativa",
    description="O freio pausa só o acelerador e o freio do ACC; a direção continua.\n" +
                "Em teste: depois de frear, o volante segue ativo. Puxe a alavanca para voltar o ACC. Só o cancelar desliga a direção.",
    restart_needed=True,
    testing=True,
  ),
  HexaOption(
    key="LaneOffsetEnabled",
    group="direcao",
    title="posição na faixa pelo volante",
    description="Segure o volante 1,5 s para deslocar o carro na faixa; solte e ele mantém.\n" +
                "Segure de volta ao centro para voltar. Zera ao desengatar.",
  ),
  HexaOption(
    key="AutoLaneChange",
    group="direcao",
    title="troca de faixa pela seta",
    description="A partir de 60 km/h, a seta ligada troca de faixa sem empurrar o volante.\n" +
                "Uma troca por seta, e só com o ponto cego livre. Não use para virar.",
    restart_needed=True,
  ),
  HexaOption(
    key="AutoLaneChangeDelay",
    group="direcao",
    title="tempo da seta até trocar",
    description="Quanto tempo a seta fica ligada antes de a troca começar.",
    kind="enum",
    choices=("1 s", "2 s", "3 s"),
    default=1,
    restart_needed=True,
  ),
  HexaOption(
    key="IsLdwEnabled",
    group="avisos",
    title="aviso de saída de faixa",
    description="Avisa quando o carro cruza uma linha sem a seta, a partir de 50 km/h.\n" +
                "Só alerta: não mexe no volante.",
    driving=False,
  ),
  HexaOption(
    key="RadarAlert",
    group="avisos",
    title="aviso de radar e lombada",
    description="Avisa com som antes dos radares, blitz e lombadas da lista, com a distância e o limite. Só avisa: não reduz sozinho.\n" +
                "Precisa de GPS. Pode faltar ponto. Corrija e adicione em Pontos.",
    driving=False,
    restart_needed=True,
  ),
  HexaOption(
    key="AutoResume",
    group="velocidade",
    title="sair sozinho da parada",
    description="Parado atrás de um carro, o ACC sai quando ele anda. Só com openpilot ligado, você olhando para a frente, sem pedal e até 3 min parado.\n" +
                "Em teste: fique pronto para frear. Não lê o sinal: se o carro da frente sair no vermelho, o seu sai junto. Sem carro à frente, não sai.",
    restart_needed=True,
    testing=True,
  ),
  HexaOption(
    key="BumpSlowdown",
    group="velocidade",
    title="reduzir na lombada",
    description="Antes de uma lombada marcada, o comma toca o − do ACC até a velocidade que você escolheu, de 30 km/h para cima. Abaixo disso, só avisa.\n" +
                "Em teste: fique pronto para frear. Precisa do aviso de radar e lombada ligado. Depois da lombada, suba com o + da alavanca.",
    restart_needed=True,
    testing=True,
  ),
  HexaOption(
    key="LeadDepartAlert",
    group="avisos",
    title="aviso de carro da frente saiu",
    description="Parado, um som avisa quando o carro da frente anda.\n" +
                "Só avisa: o carro não sai sozinho.",
    driving=False,
    restart_needed=True,
  ),
  HexaOption(
    key="AccLostAlert",
    group="avisos",
    title="aviso de acc saiu sozinho",
    description="Com o openpilot na direção, um som avisa quando o ACC da Haval sai sem freio, acelerador ou alavanca.\n" +
                "Só avisa: a partir daí, a velocidade é sua.",
    driving=False,
    restart_needed=True,
    testing=True,
  ),
  HexaOption(
    key="HexaWebEnabled",
    group="celular",
    title="painel no celular",
    description="Abre uma página no celular, na mesma rede Wi-Fi ou no hotspot do comma,\n" +
                "para ver o estado e mudar estas opções. Só com celular pareado.",
    driving=False,
    phone_writable=False,
  ),
)

_BY_KEY = {o.key: o for o in OPTIONS}


def get_option(key: str) -> HexaOption | None:
  return _BY_KEY.get(key)


def is_on(params, key: str) -> bool:
  """A blocked option stays off even if it was turned on before it was blocked: the screen can't turn it off anymore."""
  opt = get_option(key)
  return opt is not None and not opt.blocked and bool(params.get_bool(key))


def options_in_group(group: str) -> list[HexaOption]:
  return [o for o in OPTIONS if o.group == group]


def supported(opt: HexaOption, params) -> bool:
  """Shown only when this build has the option's param: a base without the feature has no key, and
  reading an unknown key raises. Stores without check_key (tests) know every key."""
  if opt.blocked:
    return False
  check = getattr(params, "check_key", None)
  if check is None:
    return True
  try:
    check(opt.key)
  except Exception:
    return False
  return True
