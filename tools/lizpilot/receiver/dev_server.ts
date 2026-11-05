// The receiver on a computer, with everything in memory: to try the comma's side without Supabase, and for the tests.
//   node --experimental-strip-types tools/lizpilot/receiver/dev_server.ts [port]
// Prints "listening <port>" when ready. LIZPILOT_SHARE_URL=http://<this computer>:<port> on the comma (or in a test) points at it.
import { createServer } from "node:http";
import { DEFAULTS, handle, MemoryStore } from "./handler.ts";

const store = new MemoryStore();
let n = 0;
// Dev only: the build word of share.py (or LIZPILOT_BUILD_SECRETS), and fixed purge and reader words for the tests.
const cfg = { ...DEFAULTS, now: () => Date.now() / 1000, newId: () => `dev-${++n}`,
              buildSecrets: (process.env.LIZPILOT_BUILD_SECRETS ?? "lizpilot-share-2026-11-a7c3e91f").split(","),
              purgeSecret: "dev-purge", readerSecret: "dev-reader" };

const server = createServer(async (req, res) => {
  const chunks: Buffer[] = [];
  for await (const c of req) chunks.push(c as Buffer);
  const body = Buffer.concat(chunks);
  const headers = new Headers();
  for (const [k, v] of Object.entries(req.headers)) if (typeof v === "string") headers.set(k, v);
  if (req.url === "/_dev/state") { // only here: what the memory store holds, for the tests
    res.writeHead(200, { "Content-Type": "application/json" });
    res.end(JSON.stringify({ devices: [...store.devices.keys()], shares: store.shares.map(({ manifest, ...r }) => ({ ...r, manifest })),
                            reports: store.reports }));
    return;
  }
  const r = await handle(new Request(`http://localhost${req.url}`, { method: req.method, headers, body: req.method === "POST" ? body : undefined }),
                         store, cfg);
  res.writeHead(r.status, Object.fromEntries(r.headers));
  res.end(Buffer.from(await r.arrayBuffer()));
});
server.listen(Number(process.argv[2] ?? 0), "127.0.0.1", () => {
  const a = server.address();
  console.log(`listening ${typeof a === "object" && a ? a.port : a}`);
});
