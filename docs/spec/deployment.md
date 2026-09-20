# Spec: Deployment

A public URL for a reviewer, on a free host, deployed by CI on every push to `main` that
passes the suite. Chosen for no card and an easy signup, not for production: the service is
one free instance that sleeps when idle. Related ADRs: 031, 034, 035.

## Host

Render, free plan, Docker runtime. Render builds the repository's `Dockerfile` itself, so the
pipeline never builds or pushes an image. The free instance has 512 MB, spins down after 15
minutes without traffic and takes about a minute to wake; the first request after an idle
period waits that long. Render injects `PORT` (10000) and `RENDER_GIT_COMMIT` at run time.

If Render asks for a card at signup, the fallback is Zeabur: the code is host-neutral, and
only `render.yaml` and the hook step of the workflow would change.

## Blueprint: `render.yaml`

One service, at the repository root, synced by Render from `main`:

| Field | Value | Why |
| --- | --- | --- |
| `type`, `runtime` | `web`, `docker` | The image is the artifact; nothing is rebuilt differently for the host |
| `plan` | `free` | One instance, which ADR-031 requires anyway |
| `autoDeploy` | `false` | A push must not deploy before the suite has passed; CI fires the hook |
| `healthCheckPath` | `/health` | Never calls a model, so Render's probes cost nothing |
| `dockerCommand` | the image's CMD, preceded by `export APP_REVISION="$RENDER_GIT_COMMIT"` | The host-specific variable name stays in the host's file (ADR-035) |
| `envVars` | `GEMINI_API_KEY` with `sync: false`; the three `GEMINI_MODEL_*` with values | The key is set once in the dashboard and never in the repository; model names are environment values, equal to `.env.example` |

No `numInstances`: the free plan is one instance and the service must stay one.
`tests/test_deployment_config.py` asserts every row of this table against the file, and that
every variable named in it is a `Settings` field.

## Pipeline: the `deploy` job

In `.github/workflows/ci.yml`, after `check`:

- Runs on `push` to `main` or on `workflow_dispatch`, and only when the repository variable
  `LIVE_URL` is set. Before the one-time setup below the job is skipped, not failed.
- `concurrency: deploy`, without cancelling: two pushes deploy in order.
- `environment: production` with `url: ${{ vars.LIVE_URL }}`, so the repository page links to
  the live service.
- Step 1 POSTs `${{ secrets.RENDER_DEPLOY_HOOK_URL }}&ref=$GITHUB_SHA`. The hook pins the
  deploy to the commit CI just verified. An unset secret fails the step with its name.
- Step 2 polls `$LIVE_URL/health` every 20 seconds for up to 20 minutes, until the body has
  `revision == $GITHUB_SHA`; it then requires `status == "ok"`, `configured == true` and
  `documents >= 1`, and fails on a timeout. The old instance answers until the new one is
  live, which is why the revision, not the status code, ends the wait (ADR-035).

## The revision

`Settings.app_revision` reads `APP_REVISION`, empty by default; `.env.example` documents it.
`GET /health` reports it as `revision`, null when unset or when the service is unconfigured.
`GET /` redirects (307) to `/docs`, outside the OpenAPI schema.

## Configuration

| Where | Name | What |
| --- | --- | --- |
| Render dashboard | `GEMINI_API_KEY` | The demo key. Anyone with the URL can spend its free-tier quota; revoke it after the interview |
| GitHub secret | `RENDER_DEPLOY_HOOK_URL` | From the service's Settings page; contains its own key |
| GitHub variable | `LIVE_URL` | `https://<service>.onrender.com`, no trailing slash |

## Runbook, first time

1. Push the commit that adds `render.yaml` and the job. CI runs; `deploy` is skipped.
2. render.com, sign in with GitHub. New, Blueprint, pick the repository. Set `GEMINI_API_KEY`
   when prompted. Render builds and deploys once on its own.
3. From the service page copy the Deploy Hook URL and the `onrender.com` URL, then:
   `gh secret set RENDER_DEPLOY_HOOK_URL` and `gh variable set LIVE_URL --body <url>`.
4. `gh workflow run CI --ref main`, or push the README commit that adds the URL. The `deploy`
   job has to go green on the revision check.

## Redeploy and rollback

Every later push to `main` that passes `check` deploys itself. `gh workflow run CI --ref main`
redeploys the head of `main` without a commit. To roll back, revert on `main` and push; the
pipeline deploys the revert like any other commit.

## Limits

One instance, in memory, no authentication: the README's Limitations already hold and gain the
sleep. Render's request timeout is not documented; `/ask` takes about a minute live and is
checked once by hand after the first deploy. A skipped `deploy` job means `LIVE_URL` is unset,
not that the deploy succeeded.
