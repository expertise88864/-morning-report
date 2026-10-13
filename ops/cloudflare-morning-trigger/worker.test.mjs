import test from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import worker, { MorningTrigger } from "./worker.mjs";
import * as entry from "./worker.mjs";
import { CRON, taipeiDay, trigger } from "./trigger.mjs";

const TIME = Date.parse("2026-10-13T22:05:00Z");
const TICK = { cron: CRON, scheduledTime: TIME };
const ENV = { ENABLED: "true", GITHUB_TOKEN: "test-only-token" };

function storage() {
  const values = new Map();
  let tail = Promise.resolve();
  const api = {
    async get(key) { return values.get(key); },
    async put(key, value) { values.set(key, value); },
    transaction(fn) {
      const result = tail.then(() => fn(api));
      tail = result.catch(() => {});
      return result;
    },
  };
  return api;
}

function fixture(override = () => undefined) {
  const calls = [];
  const fetcher = async (url, options) => {
    calls.push({ url, options });
    assert.ok(url.startsWith("https://api.github.com/repos/expertise88864/-morning-report/"));
    assert.equal(options.redirect, "error");
    assert.equal(options.headers.Authorization, "Bearer test-only-token");
    assert.ok(options.signal instanceof AbortSignal);
    const altered = await override(url, options);
    if (altered !== undefined) return altered;
    if (options.method === "POST") return new Response(null, { status: 204 });
    if (url.includes("/runs?")) return Response.json({ total_count: 0, workflow_runs: [] });
    return stateResponse();
  };
  return { calls, fetcher, now: () => TIME + 1000 };
}

function stateResponse(date = "2026-10-13 07:00", delivery = { success: true }) {
  return Response.json({ encoding: "base64", content: Buffer.from(JSON.stringify({
    date, delivery, note: "中文收據",
  })).toString("base64") });
}

test("UTC cron maps to next Taipei day and dispatches only rescue on main", async () => {
  assert.equal(taipeiDay(TIME), "2026-10-14");
  const io = fixture();
  assert.equal(await trigger(TICK, ENV, storage(), io), "dispatch_accepted");
  const posts = io.calls.filter(c => c.options.method === "POST");
  assert.equal(posts.length, 1);
  assert.ok(posts[0].url.endsWith("/morning-report-b.yml/dispatches"));
  assert.deepEqual(JSON.parse(posts[0].options.body), { ref: "main", inputs: { rescue: "true" } });
  assert.ok(io.calls[0].url.includes(encodeURIComponent(">=2026-10-13T16:00:00.000Z")));
});

test("disabled deployment never accesses GitHub", async () => {
  const io = fixture();
  assert.equal(await trigger(TICK, { ...ENV, ENABLED: "false" }, storage(), io), "disabled");
  assert.equal(io.calls.length, 0);
});

for (const [name, tick, now] of [
  ["wrong cron", { ...TICK, cron: "* * * * *" }, TIME],
  ["wrong hour", { ...TICK, scheduledTime: TIME + 3600000 }, TIME + 3600000],
  ["next-day replay", TICK, TIME + 86400000],
  ["too late", TICK, TIME + 1800001],
  ["future event", TICK, TIME - 1],
  ["invalid time", { ...TICK, scheduledTime: "not-time" }, TIME],
]) {
  test(`reject ${name} without network`, async () => {
    const io = fixture();
    await assert.rejects(trigger(tick, ENV, storage(), { ...io, now: () => now }));
    assert.equal(io.calls.length, 0);
  });
}

test("concurrent cron invocations and replay reserve only one POST", async () => {
  const db = storage();
  const io = fixture();
  const outcomes = await Promise.all(Array.from({ length: 8 }, () => trigger(TICK, ENV, db, io)));
  assert.equal(outcomes.filter(x => x === "dispatch_accepted").length, 1);
  assert.equal(io.calls.filter(c => c.options.method === "POST").length, 1);
  assert.equal(await trigger(TICK, ENV, db, io), "already_reserved");
});

for (const kind of ["lost response", "HTTP error"]) {
  test(`${kind} keeps reservation and never retries a possibly accepted POST`, async () => {
    const db = storage();
    const io = fixture((url, options) => {
      if (options.method === "POST") {
        if (kind === "lost response") throw new Error("network timeout");
        return new Response(null, { status: 502 });
      }
    });
    await assert.rejects(trigger(TICK, ENV, db, io));
    assert.equal(await trigger(TICK, ENV, db, io), "already_reserved");
    assert.equal(io.calls.filter(c => c.options.method === "POST").length, 1);
  });
}

test("new Taipei day has a separate reservation", async () => {
  const db = storage();
  const io = fixture();
  await trigger(TICK, ENV, db, io);
  assert.equal(await trigger({ ...TICK, scheduledTime: TIME + 86400000 }, ENV, db,
    { ...io, now: () => TIME + 86400000 }), "dispatch_accepted");
});

for (const status of ["today", "queued", "in_progress", "waiting", "requested", "pending"]) {
  test(`existing ${status} report prevents any second start`, async () => {
    const io = fixture(url => {
      if (url.includes(status === "today" ? "created=" : `status=${status}`)) {
        return Response.json({ total_count: 1, workflow_runs: [{ status: "completed" }] });
      }
    });
    assert.equal(await trigger(TICK, ENV, storage(), io),
      status === "today" ? "already_started_today" : "report_active");
    assert.equal(io.calls.filter(c => c.options.method === "POST").length, 0);
  });
}

for (const source of ["delivery_receipt", "run_manifest"]) {
  test(`${source} today blocks even when run API is empty`, async () => {
    const io = fixture(url => url.includes(source) ? stateResponse("2026-10-14 06:00") : undefined);
    assert.equal(await trigger(TICK, ENV, storage(), io), "state_already_today");
    assert.equal(io.calls.filter(c => c.options.method === "POST").length, 0);
  });
}

test("cross-midnight delivery timestamp blocks duplicate", async () => {
  const io = fixture(url => url.includes("delivery_receipt")
    ? stateResponse("2026-10-13 23:59", { success: true, delivered_at: "2026-10-14T00:01:00+08:00" }) : undefined);
  assert.equal(await trigger(TICK, ENV, storage(), io), "already_delivered");
});

for (const [name, override] of [
  ["401", () => new Response(null, { status: 401 })],
  ["429", () => new Response(null, { status: 429 })],
  ["inconsistent count", () => Response.json({ total_count: 0, workflow_runs: [{}] })],
  ["missing run count", () => Response.json({ workflow_runs: [] })],
  ["state 404", url => url.includes("/contents/") ? new Response(null, { status: 404 }) : undefined],
  ["bad state", url => url.includes("/contents/") ? Response.json({ content: "bad" }) : undefined],
  ["bad delivery", url => url.includes("/contents/") ? stateResponse("2026-10-13 00:00", null) : undefined],
  ["array delivery", url => url.includes("/contents/") ? stateResponse(undefined, []) : undefined],
  ["bad calendar date", url => url.includes("/contents/") ? stateResponse("2026-02-30 00:00") : undefined],
  ["bad delivery timestamp", url => url.includes("/contents/") ? stateResponse(undefined, { success: true, delivered_at: "bad" }) : undefined],
  ["ambiguous timestamp", url => url.includes("/contents/") ? stateResponse(undefined, { success: true, delivered_at: "2026-10-13T10:00:00" }) : undefined],
]) {
  test(`${name}: fail closed without POST`, async () => {
    const io = fixture(override);
    await assert.rejects(trigger(TICK, ENV, storage(), io));
    assert.equal(io.calls.filter(c => c.options.method === "POST").length, 0);
  });
}

test("expired preflight cannot dispatch after the bounded window", async () => {
  const io = fixture();
  let call = 0;
  await assert.rejects(trigger(TICK, ENV, storage(), { ...io, now: () => call++ ? TIME + 1800001 : TIME }));
  assert.equal(io.calls.filter(c => c.options.method === "POST").length, 0);
});

for (const timestamp of [
  "2026-02-30T07:00:00+08:00", "2026-02-29T07:00:00Z",
  "2026-04-31T07:00:00Z", "2026-10-13T24:00:00Z",
  "2026-10-13T07:60:00Z", "2026-10-13T07:00:60Z",
  "2026-10-13T07:00:00+24:00", "2026-10-13T07:00:00+08:60",
]) {
  test(`invalid ISO delivery timestamp ${timestamp} never dispatches`, async () => {
    for (const key of ["delivered_at", "first_delivered_at"]) {
      const io = fixture(url => url.includes("/contents/")
        ? stateResponse(undefined, { success: true, [key]: timestamp }) : undefined);
      await assert.rejects(trigger(TICK, ENV, storage(), io), /invalid_delivery_time/);
      assert.equal(io.calls.filter(c => c.options.method === "POST").length, 0);
    }
  });
}

test("valid leap-day/fractional delivery timestamp is accepted as prior evidence", async () => {
  const io = fixture(url => url.includes("/contents/")
    ? stateResponse(undefined, { success: true, delivered_at: "2024-02-29T07:00:00.123456+08:00" }) : undefined);
  assert.equal(await trigger(TICK, ENV, storage(), io), "dispatch_accepted");
});

test("storage failure fails closed", async () => {
  const io = fixture();
  const db = storage();
  db.transaction = async () => { throw new Error("storage unavailable"); };
  await assert.rejects(trigger(TICK, ENV, db, io));
  assert.equal(io.calls.filter(c => c.options.method === "POST").length, 0);
});

test("public fetch cannot trigger; DO errors signal cron failure without secret output", async () => {
  assert.equal((await worker.fetch(new Request("https://example.com/"))).status, 404);
  const object = new MorningTrigger({ storage: storage() }, { ENABLED: "false" });
  assert.equal((await object.fetch(new Request("https://internal/"))).status, 405);
  const response = await object.fetch(new Request("https://internal/", { method: "POST", body: "{}" }));
  assert.deepEqual(await response.json(), { result: "disabled" });
  await assert.rejects(worker.scheduled(TICK, { MORNING_TRIGGER: {
    idFromName: () => "stable", get: () => ({ fetch: async () => new Response(null, { status: 503 }) }),
  } }), /failed_closed/);
});

test("deployment stays disabled by default, private, and on the agreed daily cron", async () => {
  const config = JSON.parse(await readFile(new URL("./wrangler.json", import.meta.url), "utf8"));
  assert.deepEqual(config.triggers.crons, [CRON]);
  assert.equal(config.vars.ENABLED, "false");
  assert.equal(config.workers_dev, false);
  assert.equal(config.preview_urls, false);
  assert.equal(config.routes, undefined);
  assert.deepEqual(config.migrations[0].new_sqlite_classes, ["MorningTrigger"]);
});

test("runtime entry exports only handlers, never test constants", () => {
  assert.deepEqual(Object.keys(entry).sort(), ["MorningTrigger", "default"]);
});
