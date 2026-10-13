# Independent morning trigger

Cloudflare Cron starts the existing GitHub morning report at 06:05 Asia/Taipei
(22:05 UTC). Existing GitHub schedules remain backups. This reduces dependence
on GitHub's delayed schedule events; it does not guarantee a 07:00 delivery:
runner queues and report duration still apply.

## Duplicate-spend protection

- Only the scheduled handler can call the Durable Object; public HTTP returns
  404, workers.dev/preview URLs are disabled, and there are no routes.
- A tick must match the daily cron and arrive within 30 minutes of its intended
  time. The Taipei date, not the UTC date, identifies the day.
- Read GitHub on every invocation. Any report already started today (even a
  failed run), any active report including prior-day jobs, or today's remote
  receipt/manifest blocks a second start. This recovers missing starts only.
- Missing, malformed or unreadable evidence fails closed. It is not permission
  to spend model tokens. Existing GitHub schedules are not disabled by failure.
- SQLite-backed Durable Object storage atomically reserves the Taipei day
  before POST. A lost response or error never clears the reservation. One
  dispatch attempt per day, including simultaneous cron deliveries.
- Dispatch always targets `main`, `morning-report-b.yml`, `rescue: "true"`.
  The existing shared workflow concurrency and fresh pre-model delivery check
  remain the final defense against races with GitHub's own schedule/watchdog.
  This is not a claim of exactly-once SMTP delivery if all receipt evidence is lost.

## Deployment

Use Wrangler 4.149.0 from npm. Authenticate with the official OAuth flow; use
Windows keyring storage where available. Do not put credentials in this public
repository or pass tokens as shell arguments.

1. Run `node --test ops/cloudflare-morning-trigger/worker.test.mjs`. All GitHub
   requests are fakes. Required CI also runs these tests.
2. Complete independent reviews and exact-SHA candidate/main CI according to
   `REMOTE_CI_DELIVERY.md`. Deploy that verified revision only.
3. Deploy with `npx wrangler@4.149.0 deploy --config
   ops/cloudflare-morning-trigger/wrangler.json`. It is **disabled by default**.
4. Create a dedicated, expiring GitHub fine-grained token scoped only to
   `expertise88864/-morning-report`: Actions read/write, Contents read,
   Metadata read. Store it as the encrypted Worker secret `GITHUB_TOKEN` using
   the dashboard or Wrangler's interactive secret input. Never use the broad
   local Git credential. Record expiration privately and rotate before expiry.
5. Activate the reviewed deployment using the same deploy command with
   `--var ENABLED:true`. Keep the single durable binding name and migration.
   Do not delete/recreate its storage or rename the object during redeployment.
6. Verify the deployed version, cron, disabled HTTP routes and presence (not
   value) of the secret. Do **not** invoke a live cron/HTTP test or dispatch a
   report for verification. Accept the next official scheduled report instead.

Disable with the same deploy command and `--var ENABLED:false`. Do not remove
GitHub's schedules. Cloudflare logs expose only a decision or a generic failure,
never credentials or provider response bodies. `dispatch_accepted` means GitHub
accepted the trigger, not that email was delivered. Inspect GitHub run/receipt
before considering manual recovery; never blindly retry an unknown POST.

Each day uses a small number of API reads and at most one dispatch. There is no
LLM in the Worker. An expired token, Cloudflare outage or ambiguous GitHub response
is a trigger failure, not a successful delivery. Monitor cron errors in Cloudflare
and the existing report watchdog. This does not add a second email-alert sender.

Official references:
- https://developers.cloudflare.com/workers/configuration/cron-triggers/
- https://developers.cloudflare.com/durable-objects/api/sqlite-storage-api/
- https://developers.cloudflare.com/workers/configuration/secrets/
- https://docs.github.com/en/rest/actions/workflows#create-a-workflow-dispatch-event
