// LizPilot receiver: takes the moments of a drive that owners chose to send (openpilot/selfdrive/hexa/share.py).
//
// Pure logic, no platform: index.ts runs it as a Supabase Edge Function, dev_server.ts on a computer with a memory store,
// and handler.test.ts / openpilot/selfdrive/hexa/tests/test_share_receiver.py test it.
//
// Every request is signed with the comma's share key (HMAC-SHA256 of "time\nMETHOD\npath\nsha256(body)"). The key is random,
// made on the comma, and is not the dongle id: nothing here says whose comma it is. A comma registers its key once
// (/v1/register); sends a package (/v1/share); and can ask for everything it sent to be deleted (/v1/forget).

export const FORMAT = "LZS1";
export const MAX_SKEW_S = 300;
export const ZSTD_MAGIC = [0x28, 0xb5, 0x2f, 0xfd];

export interface Device { key: string }

export interface ShareRow {
  id: string;
  device: string;
  mark_id: string;
  created_at: number; // s, the day only (00:00 UTC): when it arrived would say when the owner parked
  brand: string | null;
  model: string | null;
  reason: string | null;
  label: string | null;
  note: string;
  edition: string | null;
  branch: string | null;
  git_commit: string | null;
  bytes: number;
  object: string; // where the package is kept: "<device>/<id>.lzs"
  manifest: Record<string, unknown>;
}

export interface Store {
  getDevice(id: string): Promise<Device | null>;
  addDevice(id: string, key: string, now: number): Promise<void>;
  devicesSince(since: number): Promise<number>;
  sharesSince(device: string | null, since: number): Promise<number>; // null: every device
  bytesSince(since: number): Promise<number>; // every device
  findShare(device: string, markId: string): Promise<string | null>;
  putShare(row: ShareRow, data: Uint8Array): Promise<void>;
  forgetDevice(id: string): Promise<number>; // its packages, its rows and itself; returns how many packages
  purgeBefore(before: number): Promise<number>; // packages and rows that arrived before this day; returns how many
  listShares(brand: string | null, limit: number): Promise<ShareRow[]>; // newest first
  getPackage(id: string): Promise<Uint8Array | null>;
}

export interface Config {
  now: () => number; // s
  newId: () => string;
  maxBytes: number;
  perDevicePerDay: number;
  perDayAll: number;
  bytesPerDayAll: number;
  newDevicesPerDay: number;
  retentionDays: number;
  buildSecrets: string[]; // the words a LizPilot build registers with (share.BUILD_SECRET); empty: nobody registers
  purgeSecret?: string; // the daily retention job (schema.sql) calls /v1/purge with it
  readerSecret?: string; // the developers read (envios.py) with it: list and download, nothing else
  notify?: (row: ShareRow) => Promise<void>;
}

// Only a LizPilot build registers (its BUILD_SECRET, owner 05/11). The secret is in a public repository, so the limits stay: per
// comma, and for everyone per day (UTC days). Packages are kept 12 months, then deleted (owner, 05/11).
export const DEFAULTS = { maxBytes: 8 * 1024 * 1024, perDevicePerDay: 30, perDayAll: 500, bytesPerDayAll: 1024 * 1024 * 1024, newDevicesPerDay: 100,
                          retentionDays: 365 };

function secretOk(given: string | null, expected: string | undefined): boolean {
  if (!expected || !given || given.length !== expected.length) return false;
  let diff = 0;
  for (let i = 0; i < given.length; i++) diff |= given.charCodeAt(i) ^ expected.charCodeAt(i);
  return diff === 0;
}

// What the developers see of a row: never the device.
function publicRow(r: ShareRow) {
  const { device: _device, object: _object, ...rest } = r;
  return { ...rest, created_at: new Date(r.created_at * 1000).toISOString().slice(0, 10) };
}
export const day = (s: number) => Math.floor(s / 86400) * 86400;

// What a person typed (the reason's label, the note): one line of plain text, no control characters, at most max characters.
export function plain(v: unknown, max: number): string {
  return typeof v === "string" ? v.replace(/[\p{C}\u2028\u2029]+/gu, " ").replace(/\s+/g, " ").trim().slice(0, max) : "";
}

const enc = new TextEncoder();
const HEX32 = /^[0-9a-f]{32}$/;
const HEX64 = /^[0-9a-f]{64}$/;
const MARK_ID = /^[0-9a-f]{8,16}$/;
const CODE = /^[A-Za-z0-9_.-]{1,40}$/;

function json(status: number, body: Record<string, unknown>): Response {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json", "Cache-Control": "no-store" } });
}

export function hex(buf: ArrayBuffer | Uint8Array): string {
  return Array.from(buf instanceof Uint8Array ? buf : new Uint8Array(buf), (b) => b.toString(16).padStart(2, "0")).join("");
}

function fromHex(s: string): Uint8Array {
  const out = new Uint8Array(s.length / 2);
  for (let i = 0; i < out.length; i++) out[i] = parseInt(s.slice(2 * i, 2 * i + 2), 16);
  return out;
}

export async function sha256hex(data: Uint8Array): Promise<string> {
  return hex(await crypto.subtle.digest("SHA-256", data));
}

export async function sign(keyHex: string, ts: number, method: string, path: string, body: Uint8Array): Promise<string> {
  const key = await crypto.subtle.importKey("raw", fromHex(keyHex), { name: "HMAC", hash: "SHA-256" }, false, ["sign"]);
  const msg = `${ts}\n${method.toUpperCase()}\n${path}\n${await sha256hex(body)}`;
  return hex(await crypto.subtle.sign("HMAC", key, enc.encode(msg)));
}

function sameHex(a: string, b: string): boolean { // constant time
  if (a.length !== b.length) return false;
  let diff = 0;
  for (let i = 0; i < a.length; i++) diff |= a.charCodeAt(i) ^ b.charCodeAt(i);
  return diff === 0;
}

export function unpack(data: Uint8Array): { manifest: Record<string, unknown>; payload: Uint8Array } {
  if (data.length < 8 || new TextDecoder().decode(data.slice(0, 4)) !== FORMAT) throw new Error("format");
  const n = new DataView(data.buffer, data.byteOffset + 4, 4).getUint32(0);
  if (n > data.length - 8 || n > 64 * 1024) throw new Error("manifest length");
  const manifest = JSON.parse(new TextDecoder().decode(data.slice(8, 8 + n)));
  if (!manifest || typeof manifest !== "object" || Array.isArray(manifest)) throw new Error("manifest");
  return { manifest, payload: data.slice(8 + n) };
}

function str(v: unknown, max: number, re?: RegExp): string | null {
  if (typeof v !== "string" || !v || v.length > max) return null;
  return re && !re.test(v) ? null : v;
}

function num(v: unknown): number | null {
  return typeof v === "number" && Number.isFinite(v) ? v : null;
}

// Only what share.py sends, checked field by field: anything else in the manifest is dropped, never kept.
export async function cleanManifest(m: Record<string, unknown>, payload: Uint8Array): Promise<Record<string, unknown>> {
  const obj = (v: unknown) => (v && typeof v === "object" && !Array.isArray(v) ? v as Record<string, unknown> : {});
  if (m.format !== 1) throw new Error("format version");
  const mark = obj(m.mark), car = obj(m.car), sw = obj(m.software), cut = obj(m.cut), win = obj(m.window);
  const id = str(mark.id, 16, MARK_ID);
  if (!id) throw new Error("mark id");
  if (m.sha256 !== await sha256hex(payload) || m.bytes !== payload.length) throw new Error("payload hash");
  if (!ZSTD_MAGIC.every((b, i) => payload[i] === b)) throw new Error("payload format");
  const services = obj(cut.services);
  return {
    format: 1,
    mark: { id, reason: str(mark.reason, 40, CODE), label: plain(mark.label, 40) || null, note: plain(mark.note, 500),
            delay_s: num(mark.delay_s), source: str(mark.source, 20, CODE) },
    month: str(m.month, 7, /^\d{4}-\d{2}$/),
    car: { brand: str(car.brand, 40, CODE), model: str(car.model, 60, CODE) },
    software: { edition: str(sw.edition, 20, CODE), branch: str(sw.branch, 40, CODE), commit: str(sw.commit, 12, /^[0-9a-f]{1,12}$/),
                version: str(sw.version, 20, CODE) },
    window: { before_s: num(win.before_s), after_s: num(win.after_s) },
    with_note: m.with_note === true,
    cut: {
      services: Object.fromEntries(Object.entries(services).filter(([k, v]) => CODE.test(k) && typeof v === "number").slice(0, 64)),
      left_out: Array.isArray(cut.left_out) ? cut.left_out.filter((x) => typeof x === "string" && CODE.test(x)).slice(0, 200) : [],
      can: cut.can === "dbc" ? "dbc" : "none",
      can_addresses: num(cut.can_addresses),
      can_frames_dropped: num(cut.can_frames_dropped),
      can_addresses_with_vin: Array.isArray(cut.can_addresses_with_vin)
        ? cut.can_addresses_with_vin.filter((x) => typeof x === "string" && /^\d+:0x[0-9a-f]+$/.test(x)).slice(0, 64) : [],
      can_addresses_with_text: Array.isArray(cut.can_addresses_with_text)
        ? cut.can_addresses_with_text.filter((x) => typeof x === "string" && /^\d+:0x[0-9a-f]+$/.test(x)).slice(0, 64) : [],
      vin_checked: cut.vin_checked === true,
      seconds: num(cut.seconds),
    },
    sha256: m.sha256,
    bytes: payload.length,
  };
}

// Reads at most max bytes, and stops there: a body without Content-Length (chunked) is never read whole before the check.
async function readBody(req: Request, max: number): Promise<Uint8Array | null> {
  const declared = Number(req.headers.get("Content-Length") ?? "0");
  if (declared > max) return null;
  if (!req.body) return new Uint8Array();
  const reader = req.body.getReader();
  const parts: Uint8Array[] = [];
  let n = 0;
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    n += value.length;
    if (n > max) {
      await reader.cancel();
      return null;
    }
    parts.push(value);
  }
  const out = new Uint8Array(n);
  let at = 0;
  for (const p of parts) { out.set(p, at); at += p.length; }
  return out;
}

export async function handle(req: Request, store: Store, cfg: Config): Promise<Response> {
  const url = new URL(req.url);
  const at = url.pathname.lastIndexOf("/v1/"); // Supabase puts its own prefix and the function first: /functions/v1/lizpilot-share/v1/share
  const path = at >= 0 ? url.pathname.slice(at) : url.pathname;
  if (req.method === "GET" && path === "/v1/health") return json(200, { ok: true, now: Math.floor(cfg.now()) });

  if (path === "/v1/purge" && req.method === "POST") { // the daily retention job
    if (!secretOk(req.headers.get("X-LizPilot-Purge"), cfg.purgeSecret)) return json(403, { error: "purge" });
    const n = await store.purgeBefore(day(cfg.now()) - cfg.retentionDays * 86400);
    return json(200, { ok: true, deleted: n });
  }
  if (path === "/v1/shares" || path.startsWith("/v1/shares/")) { // the developers, read only
    if (req.method !== "GET" || !secretOk(req.headers.get("X-LizPilot-Reader"), cfg.readerSecret)) return json(403, { error: "reader" });
    if (path === "/v1/shares") {
      const brand = url.searchParams.get("brand");
      const limit = Math.max(1, Math.min(200, Number(url.searchParams.get("limit") ?? "30") || 30));
      return json(200, { shares: (await store.listShares(brand && CODE.test(brand) ? brand : null, limit)).map(publicRow) });
    }
    const id = path.slice("/v1/shares/".length);
    if (!/^[A-Za-z0-9-]{1,64}$/.test(id)) return json(404, { error: "not found" });
    const data = await store.getPackage(id);
    return data ? new Response(data, { status: 200, headers: { "Content-Type": "application/octet-stream", "Cache-Control": "no-store" } })
                : json(404, { error: "not found" });
  }
  if (req.method !== "POST" || !["/v1/register", "/v1/share", "/v1/forget"].includes(path)) return json(404, { error: "not found" });

  const now = cfg.now();
  const ts = Number(req.headers.get("X-LizPilot-Time"));
  const device = req.headers.get("X-LizPilot-Device") ?? "";
  const sig = (req.headers.get("X-LizPilot-Signature") ?? "").toLowerCase();
  if (!HEX32.test(device) || !HEX64.test(sig) || !Number.isInteger(ts)) return json(401, { error: "headers" });
  if (Math.abs(ts - now) > MAX_SKEW_S) return json(401, { error: "clock", now: Math.floor(now) }); // the comma corrects its clock

  const body = await readBody(req, path === "/v1/share" ? cfg.maxBytes : 4096);
  if (body === null) return json(413, { error: "too big" });

  if (path === "/v1/register") {
    let key: string;
    try {
      const reg = JSON.parse(new TextDecoder().decode(body));
      if (reg.device !== device || typeof reg.key !== "string" || !HEX64.test(reg.key)) return json(400, { error: "register" });
      if (typeof reg.build !== "string" || !cfg.buildSecrets.some((b) => secretOk(reg.build, b))) return json(403, { error: "build" });
      key = reg.key;
    } catch {
      return json(400, { error: "register" });
    }
    if (!sameHex(await sign(key, ts, "POST", path, body), sig)) return json(401, { error: "signature" });
    const known = await store.getDevice(device);
    if (known) return sameHex(known.key, key) ? json(200, { ok: true }) : json(409, { error: "taken" });
    if (await store.devicesSince(day(now)) >= cfg.newDevicesPerDay) return json(429, { error: "busy" });
    await store.addDevice(device, key, day(now));
    return json(201, { ok: true });
  }

  const known = await store.getDevice(device);
  if (!known) return json(path === "/v1/forget" ? 404 : 401, { error: "unknown" });
  if (!sameHex(await sign(known.key, ts, "POST", path, body), sig)) return json(401, { error: "signature" });

  if (path === "/v1/forget") {
    const n = await store.forgetDevice(device);
    return json(200, { ok: true, deleted: n });
  }

  let manifest: Record<string, unknown>, payload: Uint8Array;
  try {
    const raw = unpack(body);
    payload = raw.payload;
    manifest = await cleanManifest(raw.manifest, payload);
  } catch (e) {
    return json(400, { error: `package: ${(e as Error).message}` });
  }
  const mark = manifest.mark as Record<string, unknown>;
  const already = await store.findShare(device, mark.id as string);
  if (already) return json(200, { ok: true, id: already, again: true });
  if (await store.sharesSince(device, day(now)) >= cfg.perDevicePerDay) return json(429, { error: "per device" });
  if (await store.sharesSince(null, day(now)) >= cfg.perDayAll) return json(429, { error: "busy" });
  if (await store.bytesSince(day(now)) + body.length > cfg.bytesPerDayAll) return json(429, { error: "busy" });

  const car = manifest.car as Record<string, string | null>, sw = manifest.software as Record<string, string | null>;
  const id = cfg.newId();
  const row: ShareRow = {
    id, device, mark_id: mark.id as string, created_at: day(now),
    brand: car.brand, model: car.model, reason: mark.reason as string | null, label: mark.label as string | null, note: mark.note as string,
    edition: sw.edition, branch: sw.branch, git_commit: sw.commit, bytes: body.length, object: `${device}/${id}.lzs`, manifest,
  };
  await store.putShare(row, body);
  if (cfg.notify) {
    try { await cfg.notify(row); } catch { /* the package is kept; the notice is only a convenience */ }
  }
  return json(201, { ok: true, id });
}

// A new package opens an issue in a private repository, for whoever develops: car, reason, version. Never the device, and never
// what the owner wrote (owner, 05/11): the note stays in the package, for whoever downloads it.
// Whatever an owner typed goes into the issue as inert text: no mention, link, image or markup can act from it.
export function inert(text: string): string {
  return plain(text, 500).replace(/[\\`*_{}\[\]()<>#+!|~]/g, (c) => `\\${c}`).replace(/@/g, "@\u200b").replace(/:\/\//g, ":\u200b//");
}

export function githubNotifier(token: string, repo: string, fetchFn: typeof fetch = fetch) {
  return async (row: ShareRow) => {
    const what = row.reason === "proprio" ? "motivo próprio" : row.label ?? row.reason ?? "sem motivo";
    const title = `[envio] ${row.brand ?? "?"} ${row.model ?? "?"}: ${inert(what)}`.slice(0, 120);
    const cut = (row.manifest.cut ?? {}) as Record<string, unknown>;
    const body = [
      `Envio \`${row.id}\` (${(row.bytes / 1024).toFixed(0)} KB, ${cut.seconds ?? "?"} s).`,
      "",
      `- Carro: ${row.brand ?? "?"} / ${row.model ?? "?"}`,
      `- Motivo: ${inert(row.reason === "proprio" ? "motivo próprio" : row.label ?? row.reason ?? "?")}${row.note ? " (com nota)" : ""}`,
      `- Software: ${row.edition ?? "?"} ${row.branch ?? "?"} @ ${row.git_commit ?? "?"}`,
      `- VIN procurado na CAN: ${cut.vin_checked ? "sim" : "não (carro sem VIN conhecido)"}`,
      "",
      "Baixar: `python tools/lizpilot/envios.py baixar " + row.id + "`",
    ].join("\n");
    const r = await fetchFn(`https://api.github.com/repos/${repo}/issues`, {
      method: "POST",
      headers: { Authorization: `Bearer ${token}`, Accept: "application/vnd.github+json", "User-Agent": "lizpilot-receiver" },
      body: JSON.stringify({ title, body, labels: ["envio"] }),
    });
    if (!r.ok) throw new Error(`github ${r.status}`);
  };
}

export class MemoryStore implements Store {
  devices = new Map<string, { key: string; at: number }>();
  shares: ShareRow[] = [];
  objects = new Map<string, Uint8Array>();
  async getDevice(id: string) { const d = this.devices.get(id); return d ? { key: d.key } : null; }
  async addDevice(id: string, key: string, now: number) { this.devices.set(id, { key, at: now }); }
  async devicesSince(since: number) { return [...this.devices.values()].filter((d) => d.at >= since).length; }
  async sharesSince(device: string | null, since: number) {
    return this.shares.filter((s) => (device === null || s.device === device) && s.created_at >= since).length;
  }
  async bytesSince(since: number) { return this.shares.filter((s) => s.created_at >= since).reduce((a, s) => a + s.bytes, 0); }
  async findShare(device: string, markId: string) { return this.shares.find((s) => s.device === device && s.mark_id === markId)?.id ?? null; }
  async putShare(row: ShareRow, data: Uint8Array) { this.shares.push(row); this.objects.set(row.object, data); }
  async purgeBefore(before: number) {
    const old = this.shares.filter((s) => s.created_at < before);
    for (const s of old) this.objects.delete(s.object);
    this.shares = this.shares.filter((s) => s.created_at >= before);
    return old.length;
  }
  async listShares(brand: string | null, limit: number) {
    return this.shares.filter((s) => brand === null || s.brand === brand).sort((a, b) => b.created_at - a.created_at).slice(0, limit);
  }
  async getPackage(id: string) { const s = this.shares.find((x) => x.id === id); return s ? this.objects.get(s.object) ?? null : null; }
  async forgetDevice(id: string) {
    const mine = this.shares.filter((s) => s.device === id);
    for (const s of mine) this.objects.delete(s.object);
    this.shares = this.shares.filter((s) => s.device !== id);
    this.devices.delete(id);
    return mine.length;
  }
}
