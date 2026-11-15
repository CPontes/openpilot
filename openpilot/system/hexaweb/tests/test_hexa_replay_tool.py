import json
import threading
import urllib.request

from openpilot.selfdrive.hexa.tests.test_timeline import write_rlog
from openpilot.tools.gwm import hexa_replay


def test_segment_ids():
  assert hexa_replay.segment_id("/data/media/0/realdata/00000007--7ad--9/rlog.zst") == "00000007--7ad--9"
  assert hexa_replay.segment_id("logs/abcd_00000007--7ad--9--rlog.zst") == "abcd_00000007--7ad--9--rlog"


def test_serves_the_same_page_and_data(tmp_path):
  f = write_rlog(tmp_path / "x_00000001--a--0--rlog.zst", seconds=2.0, bookmark_at=1.0)
  replays = hexa_replay.Replays([str(f)])
  server = hexa_replay.ThreadingHTTPServer(("127.0.0.1", 0), hexa_replay.make_handler(replays))
  threading.Thread(target=server.serve_forever, daemon=True).start()
  url = f"http://127.0.0.1:{server.server_address[1]}"
  try:
    assert b"Replay" in urllib.request.urlopen(url + "/").read()
    segs = json.loads(urllib.request.urlopen(url + "/api/replay/list").read())["segments"]
    data = json.loads(urllib.request.urlopen(f"{url}/api/replay/data?id={segs[0]['id']}").read())
    assert len(data["frames"]) >= 19 and data["events"][-1]["kind"] == "mark"
  finally:
    server.shutdown()
    server.server_close()


def test_export(tmp_path):
  f = write_rlog(tmp_path / "x_00000001--a--0--rlog.zst", seconds=1.0)
  hexa_replay.main([str(f), "--export", str(tmp_path / "out")])
  out = json.loads((tmp_path / "out" / "x_00000001--a--0--rlog.json").read_text())
  assert out["fps"] == 10
