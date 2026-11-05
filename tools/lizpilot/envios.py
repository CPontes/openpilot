#!/usr/bin/env python3
"""The developers' side of LizPilot sharing: list what owners sent, download it, open it as an rlog.

  export LIZPILOT_SUPABASE_URL=https://<project>.supabase.co
  export LIZPILOT_SUPABASE_KEY=<service role key>      # never in a file in a repository
  python tools/lizpilot/envios.py listar [--carro gwm] [--n 30]
  python tools/lizpilot/envios.py baixar <id>|novos [--pasta ~/lizpilot-envios]
  python tools/lizpilot/envios.py abrir <arquivo.lzs> [--pasta ...]

"abrir" writes <pasta>/<id>/rlog.zst and manifest.json: the rlog opens in the usual tools (LogReader, plotjuggler,
openpilot/tools/hexa_replay.py, openpilot.selfdrive.hexa.timeline.read_rlog). What was sent stays out of the repositories:
the default folder is in your home, and the .gitignore already blocks .zst.
"""
import argparse
import json
import os
import re
import sys
import urllib.parse
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # the repository root: openpilot.selfdrive.hexa.share

from openpilot.selfdrive.hexa import share

BUCKET = "lizpilot-shares"
FIELDS = "id,created_at,brand,model,reason,label,note,edition,branch,git_commit,bytes,object"
DEFAULT_DIR = Path.home() / "lizpilot-envios"


def _env() -> tuple[str, str]:
  url, key = os.environ.get("LIZPILOT_SUPABASE_URL", "").rstrip("/"), os.environ.get("LIZPILOT_SUPABASE_KEY", "")
  if not url or not key:
    sys.exit("Defina LIZPILOT_SUPABASE_URL e LIZPILOT_SUPABASE_KEY (a chave service role do projeto).")
  return url, key


def _get(url: str, key: str, opener=urllib.request.urlopen) -> bytes:
  req = urllib.request.Request(url, headers={"apikey": key, "Authorization": f"Bearer {key}", "User-Agent": "lizpilot-envios/1"})
  with opener(req, timeout=60) as r:
    return r.read()


def rows(base: str, key: str, brand: str | None = None, n: int = 30, ids: list[str] | None = None, opener=urllib.request.urlopen) -> list[dict]:
  q = {"select": FIELDS, "order": "created_at.desc", "limit": str(n)}
  if brand:
    q["brand"] = f"eq.{brand}"
  if ids:
    q["id"] = "in.(" + ",".join(ids) + ")"
  return json.loads(_get(f"{base}/rest/v1/lizpilot_shares?{urllib.parse.urlencode(q)}", key, opener))


def open_package(path: Path, out_dir: Path) -> Path:
  """<out_dir>/<mark id or file name>/rlog.zst and manifest.json, checked against the manifest's hash."""
  import hashlib
  manifest, payload = share.unpack(path.read_bytes())
  if hashlib.sha256(payload).hexdigest() != manifest.get("sha256"):
    raise SystemExit(f"{path}: o conteúdo não bate com o manifesto")
  dest = out_dir / path.stem
  dest.mkdir(parents=True, exist_ok=True)
  (dest / "rlog.zst").write_bytes(payload)  # the cut is a zstd of the rlog's events: it is an rlog.zst
  (dest / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
  return dest


def download(base: str, key: str, row: dict, out_dir: Path, opener=urllib.request.urlopen) -> Path:
  out_dir.mkdir(parents=True, exist_ok=True)
  f = out_dir / f"{row['id']}.lzs"
  if not f.is_file():
    f.write_bytes(_get(f"{base}/storage/v1/object/{BUCKET}/{urllib.parse.quote(row['object'])}", key, opener))
  return open_package(f, out_dir)


def describe(r: dict) -> str:
  note = f" — {r['note']}" if r.get("note") else ""
  car = f"{r.get('brand') or '?'}/{r.get('model') or '?'}"
  sw = f"{r.get('edition') or '?'} {r.get('branch') or '?'}@{r.get('git_commit') or '?'}"
  return f"{r['created_at'][:16]}  {r['id']}  {car}  {r.get('label') or r.get('reason') or '?'}{note}  [{sw}, {int(r.get('bytes') or 0) // 1024} KB]"


def main(argv=None) -> None:
  ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
  sub = ap.add_subparsers(dest="cmd", required=True)
  ls = sub.add_parser("listar")
  ls.add_argument("--carro")
  ls.add_argument("--n", type=int, default=30)
  dl = sub.add_parser("baixar")
  dl.add_argument("id", help="o id do envio, ou 'novos' para os que ainda não estão na pasta")
  dl.add_argument("--pasta", type=Path, default=DEFAULT_DIR)
  op = sub.add_parser("abrir")
  op.add_argument("arquivo", type=Path)
  op.add_argument("--pasta", type=Path, default=DEFAULT_DIR)
  a = ap.parse_args(argv)

  if a.cmd == "abrir":
    print(open_package(a.arquivo, a.pasta))
    return
  base, key = _env()
  if a.cmd == "listar":
    for r in rows(base, key, a.carro, a.n):
      print(describe(r))
    return
  if a.id != "novos" and not re.fullmatch(r"[A-Za-z0-9-]{1,64}", a.id):
    sys.exit("Id inválido.")
  todo = rows(base, key, n=200) if a.id == "novos" else rows(base, key, ids=[a.id])
  todo = [r for r in todo if a.id != "novos" or not (a.pasta / f"{r['id']}.lzs").is_file()]
  if not todo:
    print("Nada novo.")
  for r in todo:
    print(describe(r), "->", download(base, key, r, a.pasta))


if __name__ == "__main__":
  main()
