"""mapd and openpilot open the same msgq queues: the size and the reader slots of each must match on both sides (MAP-01, MAP-03).

mapd truncates every queue it opens to its own size plus its own header (gomsgq msgq.go: Truncate(size + header)), and the header
grows with the reader slots. A queue whose size or reader slots differ from openpilot's (cereal/services.py, msgq/msgq.h) leaves
the header off (MAP-01: 15 slots against 25, 240 bytes, and loggerd never read a mapdOut) or resizes a queue the car reads.

mapd's side is copied here from its source at MAPD_VERSION: settings/const.go of pfeiferj/mapd 8e5eee6 (ServiceQueueSize; on a
prefixed msgq, the comma's, any other queue gets QUEUE_SIZE_SMALL), the queues main.go and cereal/gps.go open, and the reader
slots of tools/lizpilot/mapd/0001-msgq-25-reader-slots.patch. A new mapd pin must bring its table here.
"""
import importlib.util
import re
from pathlib import Path

import pytest

from openpilot.cereal.services import SERVICE_LIST
from openpilot.selfdrive.hexa import speed_limit as sl

KiB, MiB = 1024, 1024 * 1024
MAPD_VERSION = "8e5eee6-msgq25"
# every queue mapd opens that an openpilot process opens too, with the size mapd gives it
MAPD_QUEUES = {
  "mapdOut": 2 * MiB, "mapdExtendedOut": 2 * MiB, "mapdIn": 2 * MiB,  # published or read by speed_limit.py and the panel
  "carState": 250 * KiB, "modelV2": 10 * MiB, "selfdriveState": 250 * KiB,  # read by mapd, written by the car's processes
  "gpsLocation": 250 * KiB, "gpsLocationExternal": 250 * KiB,
}
MAPD_ONLY = ("mapdCli",)  # mapd's own command line (./mapd interactive): no openpilot process opens it
MAPD_MAX_READERS = 25
REPO = Path(__file__).resolve().parents[4]
PATCH = REPO / "tools" / "lizpilot" / "mapd" / "0001-msgq-25-reader-slots.patch"


def msgq_header() -> Path | None:
  """msgq/msgq.h of this checkout: next to the msgq package, or in the submodule. None in a copy without submodules."""
  places = []
  spec = importlib.util.find_spec("msgq")
  if spec is not None and spec.submodule_search_locations:
    places += [Path(p) / "msgq.h" for p in spec.submodule_search_locations]
  places.append(REPO / "msgq_repo" / "msgq" / "msgq.h")
  return next((p for p in places if p.is_file()), None)


def test_the_table_is_for_the_pinned_mapd():
  assert sl.MAPD_VERSION == MAPD_VERSION, "a new mapd pin: copy its queue sizes (settings/const.go) and reader slots here"


@pytest.mark.parametrize("name", sorted(MAPD_QUEUES))
def test_each_queue_has_the_same_size_on_both_sides(name):
  assert name in SERVICE_LIST, f"mapd opens {name}, and services.py doesn't list it"
  assert int(SERVICE_LIST[name].queue_size) == MAPD_QUEUES[name], \
    f"{name}: services.py {int(SERVICE_LIST[name].queue_size)} bytes, mapd {MAPD_QUEUES[name]}: mapd would resize the car's queue"


def test_the_queues_only_mapd_opens_are_not_openpilot_services():
  for name in MAPD_ONLY:
    assert name not in SERVICE_LIST, f"{name} is now an openpilot service: add it to MAPD_QUEUES with mapd's size"


def test_the_shipped_patch_gives_mapd_the_reader_slots_of_the_table():
  m = re.search(r"^\+const MSGQ_MAX_READERS = (\d+)$", PATCH.read_text(), re.M)
  assert m is not None and int(m.group(1)) == MAPD_MAX_READERS


def test_mapd_and_the_msgq_of_this_checkout_have_the_same_reader_slots():
  header = msgq_header()
  if header is None:
    pytest.skip("no msgq/msgq.h here (a git archive has no submodules): the INT-01 command runs it")
  m = re.search(r"^#define NUM_READERS (\d+)$", header.read_text(), re.M)
  assert m is not None, f"NUM_READERS not found in {header}"
  assert int(m.group(1)) == MAPD_MAX_READERS, \
    f"msgq has {m.group(1)} reader slots and mapd {MAPD_MAX_READERS}: the queue header would be off, as in MAP-01"
