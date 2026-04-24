# `app.siftarr.routers`

HTTP boundary for the FastAPI app. Routers should translate requests into service calls and responses, not own business decisions.

## Responsibilities

- `dashboard.py` renders the main dashboard page.
- `dashboard_api.py` returns dashboard JSON for details, release search, timeline, and enrichment flows.
- `dashboard_actions.py` handles dashboard-triggered mutations such as retries, discard, and availability actions.
- `rules.py` manages rule UI/API interactions.
- `settings.py` manages settings, maintenance, connection tests, sync jobs, and scheduler actions.
- `staged.py` exposes staged torrent approval and discard endpoints.
- `webhooks.py` receives Overseerr webhooks and queues request processing.

## Extension points

- Add route-specific Pydantic request/response models beside the route that uses them unless they are shared widely.
- Keep database access limited to loading request context or passing a session into services.
- Use FastAPI dependencies for sessions and request-scoped inputs; avoid global mutable router state.
- Register new routers in `app/siftarr/main.py`.

## Testing guidance

- Put grouped router coverage under `tests/routers/<area>/` for larger areas, or use a focused top-level `tests/test_*_router.py` file for smaller routers.
- Test status codes, redirects, validation errors, template context, JSON shapes, and service/dependency interactions.
- Mock external network clients at the service boundary; router tests should not call Overseerr, Prowlarr, qBittorrent, or Plex.

Related docs: [app package](../README.md), [services](../services/README.md), [tests](../../../tests/README.md), and [contributing guide](../../../CONTRIBUTING.md).
