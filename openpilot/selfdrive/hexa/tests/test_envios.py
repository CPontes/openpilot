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
  base = "https://p.supabase.co/functions/v1/lizpilot-share"
  op = Opener({f"{base}/v1/shares?": json.dumps({"shares": [row]}).encode(), f"{base}/v1/shares/s1": data})
  rows = envios.rows(base, "svc", brand="gwm", opener=op)
  assert rows == [row] and "brand=gwm" in op.asked[0][0]
  assert op.asked[0][1]["X-lizpilot-reader"] == "svc" and "Authorization" not in op.asked[0][1]  # the reader word, never the service role
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


def test_a_broken_download_is_not_kept_and_typed_text_is_shown_plain(tmp_path):
  envios = tool()
  row = {"id": "s2", "object": "x/s2.lzs", "created_at": "2026-11-05T00:00:00Z", "note": "a\x1b[31mb\x07c", "label": "x\ny"}
  op = Opener({"https://p.supabase.co/v1/shares/": b"LZS1 not really"})
  try:
    envios.download("https://p.supabase.co", "svc", row, tmp_path / "out", opener=op)
  except (ValueError, SystemExit):
    pass
  assert not (tmp_path / "out" / "s2.lzs").exists()
  line = envios.describe(row)
  assert "\x1b" not in line and "\x07" not in line and "\n" not in line and line.startswith("2026-11-05  s2")


def test_a_package_that_unpacks_into_too_much_is_refused(tmp_path, monkeypatch):
  import hashlib
  import zstandard
  envios = tool()
  monkeypatch.setattr(envios, "MAX_RLOG", 1024)
  payload = zstandard.ZstdCompressor().compress(bytes(4096))  # a few bytes that unpack into 4 KB
  f = tmp_path / "bomb.lzs"
  f.write_bytes(share.pack({"format": 1, "sha256": hashlib.sha256(payload).hexdigest()}, payload))
  try:
    envios.open_package(f, tmp_path / "out")
  except SystemExit as e:
    assert "descompactado" in str(e)
  else:
    raise AssertionError("opened a bomb")
  assert not (tmp_path / "out").exists()
