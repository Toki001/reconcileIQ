# Tool and skill discovery

The requested `find-skills` and `plugin-management` workflows were reviewed before implementation. Local filesystem, Git, package managers and Docker provide the capabilities needed for milestone 01. No external account, financial integration, provider credential or plugin connection is required. No plugins or additional skills were installed.

Optional future frontend guidance: Vercel's `vercel-react-best-practices` covers React/Next.js performance. At discovery on 2026-10-21, the [skills leaderboard](https://www.skills.sh/) listed about 824.6K installs; the [official Vercel repository](https://github.com/vercel-labs/agent-skills) had about 32.7K stars. The skill and its scope were checked at the [skill page](https://www.skills.sh/vercel-labs/agent-skills/vercel-react-best-practices). To install it explicitly in a future task:

```sh
npx skills add vercel-labs/agent-skills --skill vercel-react-best-practices
```

Implementation references: [Next.js installation](https://nextjs.org/docs/app/getting-started/installation), [uv workspaces](https://docs.astral.sh/uv/concepts/projects/workspaces/), [Celery idempotency and acknowledgments](https://docs.celeryq.dev/en/stable/userguide/tasks.html), and [pnpm 12 workspace/build settings](https://pnpm.io/settings).
