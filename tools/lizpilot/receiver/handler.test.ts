// node --test --experimental-strip-types tools/lizpilot/receiver/handler.test.ts
import assert from "node:assert/strict";
import { test } from "node:test";
import { DEFAULTS, githubNotifier, handle, MemoryStore, sha256hex, sign, unpack, type Config } from "./handler.ts";

const DEVICE = "ab".repeat(16);
const KEY = "11".repeat(32);
const enc = new TextEncoder();
// the same vector is in openpilot/selfdrive/hexa/tests/test_share.py (VECTOR_SIG): both sides sign the same way
const VECTOR_SIG = "a9fec4e84c31022756b5670c1c1c18733ae1fc303d5d1d3d10efb616cc42e834";

function world(over: Partial<Config> = {}) {
  let t = 1_700_000_000, n = 0;
  const store = new MemoryStore();
  const cfg: Config = { ...DEFAULTS, now: () => t, newId: () => `id${++n}`, ...over };
  return { store, cfg, tick: (s: number) => { t += s; }, now: () => t };
}

async function call(w: ReturnType<typeof world>, path: string, body: Uint8Array, opts: { device?: string; key?: string; ts?: number; sig?: string } = {}) {
  const ts = opts.ts ?? w.now();
  const sig = opts.sig ?? await sign(opts.key ?? KEY, ts, "POST", path, body);
  const req = new Request(`https://x.supabase.co/functions/v1/lizpilot-share${path}`, {
    method: "POST", body,
    headers: { "X-LizPilot-Time": String(ts), "X-LizPilot-Device": opts.device ?? DEVICE, "X-LizPilot-Signature": sig },
  });
  const r = await handle(req, w.store, w.cfg);
  return { status: r.status, body: await r.json() };
}

const register = (w: ReturnType<typeof world>, device = DEVICE, key = KEY) =>
  call(w, "/v1/register", enc.encode(JSON.stringify({ device, key })), { device, key });

async function pkg(over: Record<string, unknown> = {}, payload = new Uint8Array([0x28, 0xb5, 0x2f, 0xfd, 1, 2, 3])) {
  const manifest = {
    format: 1, mark: { id: "ab12cd34", reason: "curva", label: "curva difícil", note: "na serra", delay_s: 2, source: "celular", who: "Conrado" },
    month: "2026-11", car: { brand: "gwm", model: "GWM_HAVAL_H6" },
    software: { edition: "h6gt", branch: "h6gt", commit: "0123456789ab", version: "0.10.1" },
    window: { before_s: 30, after_s: 15 }, cut: { services: { carState: 4501 }, left_out: ["gpsLocationExternal"], can_frames_dropped: 1,
                                                    can_addresses_with_vin: ["0:0x3e1"], vin_checked: true, seconds: 45 },
    sha256: await sha256hex(payload), bytes: payload.length, extra: "dropped", ...over,
  };
  const m = enc.encode(JSON.stringify(manifest));
  const out = new Uint8Array(8 + m.length + payload.length);
  out.set(enc.encode("LZS1"));
  new DataView(out.buffer).setUint32(4, m.length);
  out.set(m, 8);
  out.set(payload, 8 + m.length);
  return out;
}

test("the signature matches the comma's vector", async () => {
  const sig = await sign(KEY, 1700000000, "post", "/v1/share", enc.encode("hello"));
  assert.equal(sig.length, 64);
  assert.equal(sig, VECTOR_SIG);
});

test("register once, then the same key is fine and another is refused", async () => {
  const w = world();
  assert.equal((await register(w)).status, 201);
  assert.equal((await register(w)).status, 200);
  assert.equal((await register(w, DEVICE, "22".repeat(32))).status, 409);
  assert.equal((await call(w, "/v1/register", enc.encode(JSON.stringify({ device: "cd".repeat(16), key: KEY })))).status, 400); // not its id
});

test("a share is kept with a clean manifest", async () => {
  const w = world();
  await register(w);
  const r = await call(w, "/v1/share", await pkg());
  assert.deepEqual(r, { status: 201, body: { ok: true, id: "id1" } });
  const row = w.store.shares[0];
  assert.equal(row.object, `${DEVICE}/id1.lzs`);
  assert.equal(row.brand, "gwm");
  assert.equal(row.git_commit, "0123456789ab");
  assert.equal((row.manifest as any).extra, undefined); // only the known fields stay
  assert.equal((row.manifest as any).mark.who, undefined);
  assert.ok(w.store.objects.get(row.object));
  const again = await call(w, "/v1/share", await pkg());
  assert.deepEqual(again.body, { ok: true, id: "id1", again: true }); // the same mark twice is once
  assert.equal(w.store.shares.length, 1);
});

test("unsigned, badly signed, unknown or off-clock requests are refused", async () => {
  const w = world();
  await register(w);
  const body = await pkg();
  assert.equal((await call(w, "/v1/share", body, { sig: "00".repeat(32) })).status, 401);
  assert.equal((await call(w, "/v1/share", body, { key: "22".repeat(32) })).status, 401);
  assert.equal((await call(w, "/v1/share", body, { device: "cd".repeat(16) })).body.error, "unknown");
  const off = await call(w, "/v1/share", body, { ts: w.now() - 3600 });
  assert.deepEqual(off, { status: 401, body: { error: "clock", now: w.now() } });
  assert.equal((await call(w, "/v1/share", body, { device: "nothex" })).status, 401);
  assert.equal(w.store.shares.length, 0);
});

test("bad packages are refused", async () => {
  const w = world();
  await register(w);
  const bad = [
    enc.encode("not a package"),
    await pkg({ format: 2 }),
    await pkg({ sha256: "00".repeat(32) }),
    await pkg({ mark: { id: "../../etc" } }),
    await pkg({}, new Uint8Array([1, 2, 3, 4])), // not zstd
  ];
  for (const b of bad) assert.equal((await call(w, "/v1/share", b)).status, 400);
  const tooBig = world({ maxBytes: 100 });
  await register(tooBig);
  assert.equal((await call(tooBig, "/v1/share", await pkg())).status, 413);
});

test("limits per device and for everyone", async () => {
  const w = world({ perDevicePerDay: 2, perDayAll: 3 });
  await register(w);
  for (const id of ["aaaaaaa1", "aaaaaaa2"]) assert.equal((await call(w, "/v1/share", await pkg({ mark: { id } }))).status, 201);
  assert.equal((await call(w, "/v1/share", await pkg({ mark: { id: "aaaaaaa3" } }))).status, 429);
  w.tick(86401);
  assert.equal((await call(w, "/v1/share", await pkg({ mark: { id: "aaaaaaa3" } }))).status, 201);
  const reg = world({ newDevicesPerDay: 1 });
  await register(reg);
  assert.equal((await register(reg, "cd".repeat(16), "33".repeat(32))).status, 429);
});

test("forget deletes the packages, the rows and the key", async () => {
  const w = world();
  await register(w);
  await call(w, "/v1/share", await pkg());
  await register(w, "cd".repeat(16), "33".repeat(32));
  await call(w, "/v1/share", await pkg(), { device: "cd".repeat(16), key: "33".repeat(32) });
  assert.deepEqual(await call(w, "/v1/forget", new Uint8Array()), { status: 200, body: { ok: true, deleted: 1 } });
  assert.equal(w.store.shares.length, 1);
  assert.equal(w.store.objects.size, 1);
  assert.equal(await w.store.getDevice(DEVICE), null);
  assert.equal((await call(w, "/v1/forget", new Uint8Array())).status, 404); // gone: the comma counts it as done
});

test("the notice carries the car and the reason, never the device", async () => {
  const sent: any[] = [];
  const fake = (async (url: string, init: any) => { sent.push({ url, ...JSON.parse(init.body) }); return new Response("{}", { status: 201 }); }) as typeof fetch;
  const w = world({ notify: githubNotifier("t", "CPontes/lizpilot-envios", fake) });
  await register(w);
  await call(w, "/v1/share", await pkg());
  assert.equal(sent.length, 1);
  assert.equal(sent[0].url, "https://api.github.com/repos/CPontes/lizpilot-envios/issues");
  assert.match(sent[0].title, /gwm GWM_HAVAL_H6: curva difícil/);
  assert.ok(!JSON.stringify(sent[0]).includes(DEVICE));
  const failing = world({ notify: async () => { throw new Error("down"); } });
  await register(failing);
  assert.equal((await call(failing, "/v1/share", await pkg())).status, 201); // the package is kept anyway
});

test("health answers with the clock, the rest is not found", async () => {
  const w = world();
  const r = await handle(new Request("https://x/functions/v1/lizpilot-share/v1/health"), w.store, w.cfg);
  assert.deepEqual(await r.json(), { ok: true, now: w.now() });
  assert.equal((await handle(new Request("https://x/v1/other", { method: "POST" }), w.store, w.cfg)).status, 404);
});

test("unpack refuses lengths that don't fit", () => {
  const b = new Uint8Array(12);
  b.set(enc.encode("LZS1"));
  new DataView(b.buffer).setUint32(4, 999);
  assert.throws(() => unpack(b));
});
