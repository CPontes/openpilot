#!/usr/bin/env python3
"""Watch recorded drives in the Hexa replay page on a computer, with the marked timeline.

  python -m openpilot.tools.gwm.hexa_replay rlog1.zst rlog2.zst ...      # http://127.0.0.1:8090
  python -m openpilot.tools.gwm.hexa_replay --export pasta/ rlog.zst ...  # writes the timelines as JSON

Same page and same data as the phone panel on the comma (system/hexaweb). Only listens on this
computer. Logs are personal data: keep them out of public places.
"""
import argparse
import json
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs

from openpilot.selfdrive.hexa import timeline
from openpilot.system.hexaweb.hexaweb import STATIC_DIR


def segment_id(path: str) -> str:
  p = Path(path)
  # comma layout: <route>--<n>/rlog.zst; downloads: <dongle>_<route>--<n>--rlog.zst
  return p.parent.name if p.name.startswith("rlog") else p.name.removesuffix(".zst").removesuffix(".bz2")


class Replays:
  def __init__(self, files: list[str]):
    self.files = {segment_id(f): f for f in files}
    self.cache: dict[str, dict] = {}

  def data(self, seg_id: str) -> dict | None:
    if seg_id not in self.files:
      return None
    if seg_id not in self.cache:
      self.cache[seg_id] = timeline.build(timeline.read_rlog(self.files[seg_id]))
    return self.cache[seg_id]


def make_handler(replays: Replays):
  class Handler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):  # noqa: A002
      pass

    def _send(self, status, body: bytes, ctype: str):
      self.send_response(status)
      self.send_header("Content-Type", ctype)
      self.send_header("Content-Length", str(len(body)))
      self.send_header("Cache-Control", "no-store")
      self.end_headers()
      self.wfile.write(body)

    def _json(self, status, obj):
      self._send(status, json.dumps(obj, ensure_ascii=False, separators=(",", ":")).encode(), "application/json; charset=utf-8")

    def do_GET(self):
      path, _, query = self.path.partition("?")
      if path in ("/", "/replay"):
        self._send(200, (STATIC_DIR / "replay.html").read_bytes(), "text/html; charset=utf-8")
      elif path == "/api/replay/list":
        self._json(200, {"segments": [{"id": k, "label": k} for k in replays.files]})
      elif path == "/api/replay/data":
        d = replays.data(parse_qs(query).get("id", [""])[0])
        self._json(200, d) if d is not None else self._json(404, {"error": "Trecho não encontrado."})
      else:
        self._json(404, {"error": "Não encontrado."})
  return Handler


def main(argv=None):
  ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
  ap.add_argument("rlogs", nargs="+")
  ap.add_argument("--port", type=int, default=8090)
  ap.add_argument("--export", help="write <segment>.json files to this folder and exit")
  args = ap.parse_args(argv)
  replays = Replays(args.rlogs)
  if args.export:
    out = Path(args.export)
    out.mkdir(parents=True, exist_ok=True)
    for seg_id in replays.files:
      (out / f"{seg_id}.json").write_text(json.dumps(replays.data(seg_id), ensure_ascii=False, separators=(",", ":")))
      print(f"{seg_id}: {len(replays.data(seg_id)['events'])} eventos")
    return
  server = ThreadingHTTPServer(("127.0.0.1", args.port), make_handler(replays))
  print(f"replay em http://127.0.0.1:{args.port}  ({len(replays.files)} trecho(s))", file=sys.stderr)
  server.serve_forever()


if __name__ == "__main__":
  main()
