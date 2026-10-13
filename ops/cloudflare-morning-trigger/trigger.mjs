export const CRON = "5 22 * * *";
const ROOT = "https://api.github.com/repos/expertise88864/-morning-report";
const WORKFLOW = "/actions/workflows/morning-report-b.yml";
const ACTIVE = ["queued", "in_progress", "waiting", "requested", "pending"];

export function taipeiDay(ms) {
  if (!Number.isFinite(ms)) throw new Error("invalid_time");
  return new Date(ms + 8 * 3600000).toISOString().slice(0, 10);
}

export function validateTick(tick, now) {
  if (tick?.cron !== CRON || !Number.isFinite(tick.scheduledTime)) {
    throw new Error("invalid_tick");
  }
  const scheduled = new Date(tick.scheduledTime);
  if (scheduled.getUTCHours() !== 22 || scheduled.getUTCMinutes() !== 5 ||
      now < tick.scheduledTime || now - tick.scheduledTime > 30 * 60000) {
    throw new Error("outside_dispatch_window");
  }
  return taipeiDay(tick.scheduledTime);
}

function apiClient(token, fetcher) {
  if (typeof token !== "string" || !token.trim()) throw new Error("missing_secret");
  return async (path, body) => {
    const response = await fetcher(ROOT + path, {
      method: body === undefined ? "GET" : "POST",
      headers: {
        Authorization: `Bearer ${token}`,
        Accept: "application/vnd.github+json",
        "Content-Type": "application/json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "morning-report-scheduler",
        "Cache-Control": "no-cache",
      },
      body: body === undefined ? undefined : JSON.stringify(body),
      redirect: "error",
      signal: AbortSignal.timeout(15000),
    });
    if (body !== undefined) {
      if (response.status !== 204) throw new Error("dispatch_not_confirmed");
      return;
    }
    if (response.status !== 200) throw new Error("github_read_failed");
    return response.json();
  };
}

function runCount(data) {
  if (!Number.isSafeInteger(data?.total_count) || data.total_count < 0 ||
      !Array.isArray(data.workflow_runs) ||
      data.workflow_runs.length !== Math.min(data.total_count, 1)) {
    throw new Error("invalid_runs_response");
  }
  return data.total_count;
}

function validDay(date) {
  const ms = Date.parse(`${date}T00:00:00Z`);
  return Number.isFinite(ms) && new Date(ms).toISOString().slice(0, 10) === date;
}

function deliveryTime(value) {
  const match = typeof value === "string" && /^(\d{4}-\d{2}-\d{2})T(\d{2}):(\d{2}):(\d{2})(?:\.\d{1,9})?(Z|[+-](\d{2}):(\d{2}))$/.exec(value);
  if (!match || !validDay(match[1]) || Number(match[2]) > 23 ||
      Number(match[3]) > 59 || Number(match[4]) > 59 ||
      (match[5] !== "Z" && (Number(match[6]) > 23 || Number(match[7]) > 59))) {
    throw new Error("invalid_delivery_time");
  }
  const ms = Date.parse(value);
  if (!Number.isFinite(ms)) throw new Error("invalid_delivery_time");
  return ms;
}

export async function preflight(api, day) {
  // An already-started report may have spent tokens even if delivery failed.
  // Do not retry it automatically. This trigger recovers missing starts only.
  const midnight = new Date(`${day}T00:00:00+08:00`).toISOString();
  const today = await api(`${WORKFLOW}/runs?per_page=1&created=${encodeURIComponent(">=" + midnight)}`);
  if (runCount(today) > 0) return "already_started_today";
  // Include prior-day runs: a queued job can cross the Taipei date boundary.
  for (const status of ACTIVE) {
    if (runCount(await api(`${WORKFLOW}/runs?per_page=1&status=${status}`)) > 0) {
      return "report_active";
    }
  }
  for (const name of ["delivery_receipt", "run_manifest"]) {
    const file = await api(`/contents/state/${name}.json?ref=main`);
    if (file?.encoding !== "base64" || typeof file.content !== "string") {
      throw new Error("invalid_state_file");
    }
    const bytes = Uint8Array.from(atob(file.content.replace(/\s/g, "")), c => c.charCodeAt(0));
    const data = JSON.parse(new TextDecoder("utf-8", { fatal: true }).decode(bytes));
    if (typeof data?.date !== "string" || !/^\d{4}-\d{2}-\d{2}[ T]/.test(data.date) ||
        !data.delivery || typeof data.delivery !== "object" || Array.isArray(data.delivery) ||
        typeof data.delivery.success !== "boolean") {
      throw new Error("invalid_delivery_state");
    }
    const date = data.date.slice(0, 10);
    if (!validDay(date)) {
      throw new Error("invalid_state_date");
    }
    // A future/ambiguous state also blocks dispatch instead of spending again.
    if (date >= day) return "state_already_today";
    for (const key of ["delivered_at", "first_delivered_at"]) {
      if (data.delivery[key]) {
        const time = deliveryTime(data.delivery[key]);
        if (taipeiDay(time) >= day) return "already_delivered";
      }
    }
  }
  return "ready";
}

export async function trigger(tick, env, storage, { fetcher = fetch, now = Date.now } = {}) {
  if (env.ENABLED !== "true") return "disabled";
  const day = validateTick(tick, now());
  const api = apiClient(env.GITHUB_TOKEN, fetcher);
  if (await storage.get(`dispatch:${day}`)) return "already_reserved";
  const decision = await preflight(api, day);
  if (decision !== "ready") return decision;
  validateTick(tick, now());
  // Atomic durable reservation precedes the side effect. Never release it on
  // timeout/error: GitHub might have accepted a request whose response was lost.
  const claimed = await storage.transaction(async txn => {
    const key = `dispatch:${day}`;
    if (await txn.get(key)) return false;
    await txn.put(key, { status: "reserved", at: new Date(now()).toISOString() });
    return true;
  });
  if (!claimed) return "already_reserved";
  await api(`${WORKFLOW}/dispatches`, { ref: "main", inputs: { rescue: "true" } });
  await storage.put(`dispatch:${day}`, { status: "accepted", at: new Date(now()).toISOString() });
  return "dispatch_accepted";
}
