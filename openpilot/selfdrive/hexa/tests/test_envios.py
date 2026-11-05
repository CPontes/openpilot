"""tools/lizpilot/envios.py: the developers download what was sent and open it as an rlog."""
import importlib.util
import io
import json
from pathlib import Path

from openpilot.selfdrive.hexa import share
from openpilot.selfdrive.hexa.tests.test_share import MARK, NS, T0, World, decode, write_segments

TOOL = Path(__file__).resolve().parents[4] / "tools/lizpilot/envios.py"


def tool():
  spec = importlib.util.spec_from_file_location("envios", TOOL)
  mod = importlib.util.module_from_spec(spec)
  spec.loader.exec_module(mod)
  return mod


class Opener:
  def __init__(self, files):
    self.files, self.asked = files, []

  def __call__(self, req, timeout=None):
    self.asked.append((req.full_url, dict(req.header_items())))
    for prefix, data in self.files.items():
      if req.full_url.startswith(prefix):
        return io.BytesIO(data)
    raise AssertionError(req.full_url)


def a_package(tmp_path) -> bytes:
  write_segments(tmp_path / "realdata", minutes=2)
  w = World(tmp_path)
  w.sharer.cutter = share.cut_mark
  mark = {**MARK, "t": 10_000 + 60 + 30, "mono_ns": T0 + 90 * NS, "delay_s": 0.}
  w.sharer.enqueue(mark)
  w.sharer.step(True, False, [mark])
  return next((tmp_path / "share").iterdir()).read_bytes()


def test_download_and_open_as_an_rlog(tmp_path):
  envios = tool()
  data = a_package(tmp_path)
  row = {"id": "s1", "object": "ab" * 16 + "/s1.lzs", "created_at": "2026-11-05T10:00:00Z", "brand": "gwm", "model": "GWM_HAVAL_H6",
         "label": "curva difícil", "note": "na serra", "bytes": len(data)}
  base = "https://p.supabase.co"
  op = Opener({f"{base}/rest/v1/lizpilot_shares?": json.dumps([row]).encode(), f"{base}/storage/v1/object/lizpilot-shares/": data})
  rows = envios.rows(base, "svc", ids=["s1"], opener=op)
  assert rows == [row] and "id=in.%28s1%29" in op.asked[0][0]
  assert op.asked[0][1]["Authorization"] == "Bearer svc"
  dest = envios.download(base, "svc", row, tmp_path / "out", opener=op)
  manifest = json.loads((dest / "manifest.json").read_text())
  assert manifest["mark"]["id"] == MARK["id"]
  from openpilot.selfdrive.hexa.timeline import read_rlog
  evs = read_rlog(dest / "rlog.zst")  # the usual tools read it
  assert evs and {e.which() for e in evs} <= share.SERVICES
  assert decode((dest / "rlog.zst").read_bytes())[1][0].logMonoTime == evs[0].logMonoTime
  assert "gwm/GWM_HAVAL_H6" in envios.describe(row)


def test_a_package_that_doesnt_match_its_manifest_is_refused(tmp_path):
  envios = tool()
  data = bytearray(a_package(tmp_path))
  data[-1] ^= 0xFF
  f = tmp_path / "bad.lzs"
  f.write_bytes(bytes(data))
  try:
    envios.open_package(f, tmp_path / "out")
  except SystemExit as e:
    assert "não bate" in str(e)
  else:
    raise AssertionError("opened a broken package")
