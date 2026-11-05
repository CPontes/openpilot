// Supabase Edge Function "lizpilot-share": handler.ts on Supabase, packages in a private bucket, rows in two tables (schema.sql).
// Deploy: README.md. It runs with the service role, which only this function has: the tables and the bucket have no public policy.
import { createClient } from "npm:@supabase/supabase-js@2.45.4";
import { DEFAULTS, githubNotifier, handle, type ShareRow, type Store } from "./handler.ts";

const BUCKET = "lizpilot-shares";
const db = createClient(Deno.env.get("SUPABASE_URL")!, Deno.env.get("SUPABASE_SERVICE_ROLE_KEY")!, { auth: { persistSession: false } });
const iso = (s: number) => new Date(s * 1000).toISOString();

function check<T>(r: { data: T; error: unknown }): T {
  if (r.error) throw r.error;
  return r.data;
}

const store: Store = {
  async getDevice(id) {
    const rows = check(await db.from("lizpilot_devices").select("key").eq("id", id).limit(1));
    return rows.length ? { key: rows[0].key } : null;
  },
  async addDevice(id, key, now) {
    check(await db.from("lizpilot_devices").insert({ id, key, created_at: iso(now) }));
  },
  async devicesSince(since) {
    const r = await db.from("lizpilot_devices").select("id", { count: "exact", head: true }).gte("created_at", iso(since));
    if (r.error) throw r.error;
    return r.count ?? 0;
  },
  async sharesSince(device, since) {
    let q = db.from("lizpilot_shares").select("id", { count: "exact", head: true }).gte("created_at", iso(since));
    if (device !== null) q = q.eq("device", device);
    const r = await q;
    if (r.error) throw r.error;
    return r.count ?? 0;
  },
  async bytesSince(since) {
    const rows = check(await db.from("lizpilot_shares").select("bytes").gte("created_at", iso(since)));
    return rows.reduce((a: number, r: { bytes: number }) => a + r.bytes, 0);
  },
  async findShare(device, markId) {
    const rows = check(await db.from("lizpilot_shares").select("id").eq("device", device).eq("mark_id", markId).limit(1));
    return rows.length ? rows[0].id : null;
  },
  async putShare(row: ShareRow, data: Uint8Array) {
    check(await db.storage.from(BUCKET).upload(row.object, data, { contentType: "application/octet-stream", upsert: false }));
    const r = await db.from("lizpilot_shares").insert({ ...row, created_at: iso(row.created_at) });
    if (r.error) {
      await db.storage.from(BUCKET).remove([row.object]); // no row, no package
      throw r.error;
    }
  },
  async forgetDevice(id) {
    const rows = check(await db.from("lizpilot_shares").select("object").eq("device", id));
    // by the rows, and by the folder too: a package whose row was never written (a forget in the middle of a send) goes as well
    const listed = check(await db.storage.from(BUCKET).list(id, { limit: 1000 })).map((f: { name: string }) => `${id}/${f.name}`);
    const objects = [...new Set([...rows.map((r: { object: string }) => r.object), ...listed])];
    if (objects.length) check(await db.storage.from(BUCKET).remove(objects));
    check(await db.from("lizpilot_devices").delete().eq("id", id)); // the rows go with it (on delete cascade)
    return rows.length;
  },
};

const token = Deno.env.get("LIZPILOT_GITHUB_TOKEN"), repo = Deno.env.get("LIZPILOT_GITHUB_REPO");
const cfg = {
  ...DEFAULTS,
  now: () => Date.now() / 1000,
  newId: () => crypto.randomUUID(),
  notify: token && repo ? githubNotifier(token, repo) : undefined,
};

Deno.serve(async (req) => {
  try {
    return await handle(req, store, cfg);
  } catch (e) {
    console.error("lizpilot-share", e);
    return new Response(JSON.stringify({ error: "server" }), { status: 500, headers: { "Content-Type": "application/json" } });
  }
});
