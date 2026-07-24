# Dalston web console

The console is a React/TypeScript application built with Vite. The gateway
serves the production build from `web/dist` and exposes authenticated
aggregation endpoints under `/api/console`.

## Development

From the repository root:

```bash
npm ci --prefix web
npm run dev --prefix web
```

The Vite server uses the API proxy/base settings in `web/vite.config.ts`. Start
one gateway runtime separately; do not run a local gateway and the Docker
gateway at the same time.

Production checks:

```bash
npm run lint --prefix web
npm run build --prefix web
```

`build` runs the TypeScript project build before Vite. The output directory is
`web/dist`.

The repository's browser tests live under `tests/web`. Design-specific linting
is exposed through the repository workflow/Make setup where configured; the
package itself currently defines `dev`, `build`, `lint`, and `preview`.

## Routes

All routes except `/login` require an authenticated console session.

| Route | Function |
| --- | --- |
| `/` | Dashboard |
| `/queue` | Queue board and task/job pivots |
| `/jobs` | Batch jobs |
| `/jobs/new` | Submit file or URL transcription |
| `/jobs/:jobId` | Job result, pipeline, audio, and actions |
| `/jobs/:jobId/tasks/:taskId` | Task details and artifacts |
| `/realtime` | Persisted realtime sessions |
| `/realtime/live` | Live native realtime transcription |
| `/realtime/sessions/:sessionId` | Session details and retained artifacts |
| `/engines` | Live engine inventory |
| `/engines/:engineId` | Engine details |
| `/infrastructure` | Nodes and control-plane health |
| `/models` | Model registry and download management |
| `/keys` | API-key management |
| `/webhooks` | Webhook endpoints |
| `/webhooks/:endpointId` | Endpoint deliveries, retry, and secret rotation |
| `/audit` | Audit-event search |
| `/settings` | Runtime settings exposed by the console API |

Route definitions in `web/src/App.tsx` are authoritative.

## Code map

- `src/pages`: route-level screens.
- `src/components`: shared domain and UI components.
- `src/api/client.ts`: HTTP client.
- `src/api/types.ts`: API-facing TypeScript types.
- `src/hooks`: React Query hooks and mutations.
- `src/contexts`: authentication and live-session state.
- `src/lib`: formatting, retention, stage, string, and timing helpers.

The project uses React Query for server state and React Router for navigation.
Keep API calls in the client/hooks layers instead of issuing ad hoc requests
from visual components.

## Authentication

The console login exchanges a Dalston API key for the configured console
session mechanism. Never persist raw API keys in new browser storage. Protected
routes are wrapped by `ProtectedRoute`; authorization is still enforced by the
gateway and must not rely on UI visibility.

## Adding or changing a page

Update the route, page component, API types/client or hook, loading/empty/error
states, and browser coverage together. Reuse the existing component and design
tokens in `src/components/ui` and `src/index.css`. Verify narrow and wide
layouts for table-heavy pages.

Run lint and the production build before handing off a console change.
