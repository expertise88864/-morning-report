import { trigger } from "./trigger.mjs";

export class MorningTrigger {
  constructor(ctx, env) {
    this.storage = ctx.storage;
    this.env = env;
  }

  async fetch(request) {
    if (request.method !== "POST") return new Response(null, { status: 405 });
    try {
      const result = await trigger(await request.json(), this.env, this.storage);
      console.log(JSON.stringify({ component: "morning-trigger", result }));
      return Response.json({ result });
    } catch {
      // Never log provider responses, URLs with credentials, or error bodies.
      console.error("morning-trigger: failed_closed; inspect GitHub before retrying");
      return Response.json({ result: "failed_closed" }, { status: 503 });
    }
  }
}

export default {
  async scheduled(controller, env) {
    const stub = env.MORNING_TRIGGER.get(env.MORNING_TRIGGER.idFromName("morning-report-v1"));
    const response = await stub.fetch("https://internal/trigger", {
      method: "POST",
      body: JSON.stringify({ cron: controller.cron, scheduledTime: controller.scheduledTime }),
    });
    if (!response.ok) throw new Error("morning_trigger_failed_closed");
  },
  fetch() {
    return new Response("Not found", { status: 404 });
  },
};
