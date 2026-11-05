#!/usr/bin/env python3
"""The developers' side of LizPilot sharing: list what owners sent, download it, open it as an rlog.

  export LIZPILOT_SHARE_URL=https://<project>.supabase.co/functions/v1/lizpilot-share
  export LIZPILOT_READER_KEY=<the reader word>   # LIZPILOT_READER_SECRET of the function: reads, never writes nor deletes
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
import shutil
import sys
import urllib.parse
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # the repository root: openpilot.selfdrive.hexa.share

from openpilot.selfdrive.hexa import share

DEFAULT_DIR = Path.home() / "lizpilot-envios"
MAX_PACKAGE = 9 * 1024 * 1024  # share.MAX_BYTES and a margin: the receiver refuses more
MAX_RLOG = 256 * 1024 * 1024  # 45 s of rlog is a few MB: a package that unpacks into more is not one of ours


def _env() -> tuple[str, str]:
  url, key = os.environ.get("LIZPILOT_SHARE_URL", "").rstrip("/"), os.environ.get("LIZPILOT_READER_KEY", "")
  if not url or not key:
    sys.exit("Defina LIZPILOT_SHARE_URL e LIZPILOT_READER_KEY (a palavra de leitura do receptor).")
  if not share.receiver_ok(url):
    sys.exit("LIZPILOT_SHARE_URL precisa ser https.")
  return url, key


def _get(url: str, key: str, opener=urllib.request.urlopen, limit: int = MAX_PACKAGE) -> bytes:
  req = urllib.request.Request(url, headers={"X-LizPilot-Reader": key, "User-Agent": "lizpilot-envios/1"})
  with opener(req, timeout=60) as r:
    data = r.read(limit + 1)
  if len(data) > limit:
    raise SystemExit(f"{url}: resposta grande demais")
  return data


def rows(base: str, key: str, brand: str | None = None, n: int = 30, opener=urllib.request.urlopen) -> list[dict]:
  q = {"limit": str(n), **({"brand": brand} if brand else {})}
  return json.loads(_get(f"{base}/v1/shares?{urllib.parse.urlencode(q)}", key, opener))["shares"]


def open_package(path: Path, out_dir: Path) -> Path:
  """<out_dir>/<mark id or file name>/rlog.zst and manifest.json, checked against the manifest's hash."""
  import hashlib
  import zstandard
  if path.stat().st_size > MAX_PACKAGE:
    raise SystemExit(f"{path}: grande demais para um envio")
  manifest, payload = share.unpack(path.read_bytes())
  if hashlib.sha256(payload).hexdigest() != manifest.get("sha256"):
    raise SystemExit(f"{path}: o conteúdo não bate com o manifesto")
  try:  # unpacked with a ceiling: a small file can't fill the disk
    with zstandard.ZstdDecompressor().stream_reader(payload) as r:
      if len(r.read(MAX_RLOG + 1)) > MAX_RLOG:
        raise SystemExit(f"{path}: descompactado passa de {MAX_RLOG // 2**20} MB")
  except zstandard.ZstdError as e:
    raise SystemExit(f"{path}: não é um zstd válido") from e
  dest = out_dir / path.stem
  dest.mkdir(parents=True, exist_ok=True)
  (dest / "rlog.zst").write_bytes(payload)  # the cut is a zstd of the rlog's events: it is an rlog.zst
  (dest / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
  return dest


def download(base: str, key: str, row: dict, out_dir: Path, opener=urllib.request.urlopen) -> Path:
  """Kept under its id only once it opens and matches its manifest: a broken download is fetched again next time."""
  out_dir.mkdir(parents=True, exist_ok=True)
  f = out_dir / f"{row['id']}.lzs"
  if not f.is_file():
    part = f.with_suffix(".part")
    part.write_bytes(_get(f"{base}/v1/shares/{urllib.parse.quote(row['id'])}", key, opener))
    try:
      open_package(part, out_dir / ".check")
    finally:
      shutil.rmtree(out_dir / ".check", ignore_errors=True)
    os.replace(part, f)
  return open_package(f, out_dir)


def _plain(v) -> str:
  """What an owner typed, shown without control characters (a terminal escape can't act from it)."""
  return re.sub(r"\s+", " ", "".join(c if c.isprintable() else " " for c in str(v or ""))).strip()


def describe(r: dict) -> str:
  note = f" — {_plain(r['note'])}" if r.get("note") else ""
  car = f"{r.get('brand') or '?'}/{r.get('model') or '?'}"
  sw = f"{r.get('edition') or '?'} {r.get('branch') or '?'}@{r.get('git_commit') or '?'}"
  label = _plain(r.get("label") or r.get("reason") or "?")
  return f"{r['created_at'][:10]}  {r['id']}  {_plain(car)}  {label}{note}  [{_plain(sw)}, {int(r.get('bytes') or 0) // 1024} KB]"


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
  todo = rows(base, key, n=200) if a.id == "novos" else [r for r in rows(base, key, n=200) if r["id"] == a.id] or [{"id": a.id, "created_at": "?"}]
  todo = [r for r in todo if a.id != "novos" or not (a.pasta / f"{r['id']}.lzs").is_file()]
  if not todo:
    print("Nada novo.")
  for r in todo:
    print(describe(r), "->", download(base, key, r, a.pasta))


if __name__ == "__main__":
  main()
