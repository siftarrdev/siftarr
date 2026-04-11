# Plan: Per-Episode Availability from Plex

## Overview

Since Overseerr only provides season-level availability, we'll integrate directly with Plex to get episode-level status. This also allows simplifying several existing workarounds built around Overseerr's lack of per-episode data.

## Key Questions

1. **Plex Access**
   - Do you have Plex credentials (username/password or token) in your `.env`?
   - Is Plex running on the same server/network?
   - Do you use Plex Pass (webhooks available)?

2. **Data Storage**
   - Should we cache episode availability in the database, or query Plex live each time?
   - If cached, how often should we refresh?

## Prerequisites (Before Implementation)

### 1. Plex Configuration

Add to `config.py`:
```python
plex_url: str | None = None      # e.g. "http://localhost:32400"
plex_token: str | None = None    # Plex X-Plex-Token
```

Add to `.env` and `docker/.env`:
```
PLEX_URL=http://localhost:32400
PLEX_TOKEN=your_plex_token
```

### 2. Plex Server Discovery

The Plex server must be network-accessible from the Siftarr host. If running in Docker, ensure the container can reach the Plex host (may need Docker networking or host mode).

To get a Plex token: https://support.plex.tv/articles/204059436-finding-an-authentication-token-x-plex-token/

## Proposed Implementation

### Phase 1: Plex API Integration Service

Create `app/siftarr/services/plex_service.py` following existing service conventions:

```python
class PlexService:
    """Service for fetching per-episode availability from Plex."""

    def __init__(self, settings: Settings | None = None):
        self.settings = settings or get_settings()
        self.base_url = str(self.settings.plex_url).rstrip("/")
        self.token = self.settings.plex_token

    async def _get_client(self) -> httpx.AsyncClient:
        return await get_shared_client()

    async def close(self) -> None:
        pass  # Uses shared client
```

Methods:
- `search_show(title: str)` - Search Plex library by title, return matching items with rating keys
- `get_show_children(rating_key: str)` - Get all seasons for a show
- `get_season_children(rating_key: str)` - Get all episodes for a season
- `get_episode_availability(rating_key: str) -> dict[tuple[int, int], bool]` - Map (season_number, episode_number) → available (has `Media`)

### Phase 2: Database Schema Update

**Add to Request model** (`app/siftarr/models/request.py`):
```python
plex_rating_key: Mapped[str | None] = mapped_column(String(100), nullable=True)
```
This is required to look up the show in Plex without repeated searches.

**Episode model** - no changes needed. We reuse the existing `status` field.

**Migration** - Follow existing pattern in `db/alembic/versions/`:
- Idempotent upgrade with `inspector.get_columns()` check
- Filename format: `YYYY_MM_DD_HHMM-add_plex_rating_key_to_requests.py`
- Update `LATEST_KNOWN_MIGRATION_REVISION` in `main.py`

### Phase 3: Episode Sync Update

Modify `EpisodeSyncService` to accept and use `PlexService`:

```python
class EpisodeSyncService:
    def __init__(self, db: AsyncSession, overseerr: OverseerrService | None = None, plex: PlexService | None = None):
        self.db = db
        self._overseerr = overseerr
        self._plex = plex
```

**Key behavior change**: After syncing episode metadata from Overseerr, query Plex for per-episode availability and override episode status:

1. Sync episode structure (titles, air dates) from Overseerr (unchanged)
2. If `PlexService` is available and request has `plex_rating_key`, query Plex for per-episode availability
3. Episodes present on Plex → `AVAILABLE`; absent → fall back to Overseerr-derived status
4. **Derive season status from episodes** instead of copying Overseerr status:
   - All episodes available → `AVAILABLE`
   - Some available → `PARTIALLY_AVAILABLE`
   - None available → original Overseerr status

This reverses the current logic where `episode_status = season_status` (line 161). Now season status is *derived from* episode statuses.

### Phase 4: Dashboard Simplification

With Plex-derived per-episode availability, several workarounds can be removed:

**Simplify `available_count`** (dashboard.py lines 540-545):

Current:
```python
if season.status == RequestStatus.AVAILABLE:
    available_count = len(episodes)
elif season.status == RequestStatus.PARTIALLY_AVAILABLE:
    available_count = 0  # workaround: Overseerr gives no per-episode data
else:
    available_count = sum(1 for ep in episodes if ep.status == RequestStatus.AVAILABLE)
```

Becomes:
```python
available_count = sum(1 for ep in episodes if ep.status == RequestStatus.AVAILABLE)
```

**Simplify pending filter** (dashboard.py lines 269-281):

Currently, `PARTIALLY_AVAILABLE` requests are included in the pending-action list because we don't know which episodes exist. With Plex data, filter on actual per-episode availability instead.

**`PARTIALLY_AVAILABLE` status on episodes** - Should only be a *season-level derived status*, not applied to individual episodes. Individual episodes should only be `AVAILABLE`, `PENDING`, or `RECEIVED`/`SEARCHING`/etc.

### Phase 5: Refresh from Plex

- Add POST endpoint `/requests/{request_id}/refresh-plex` in `dashboard.py`
- Add refresh button to existing `dashboard.html`
- This endpoint forces a Plex re-sync regardless of staleness

### Phase 6: Plex Rating Key Discovery

Finding the Plex rating key for a show. Options (in order of preference):

1. **TMDB ID match**: Search Plex library and match by `Guid` tag containing TMDB ID (most reliable, if available)
2. **Title search**: Search Plex by show title and match (less reliable for common names)
3. **Manual entry**: Allow users to set `plex_rating_key` via UI (fallback)

The TMDB ID approach uses Plex's `/library/search` endpoint with `guid` parameter or `/library/sections/{section}/search` with the TMDB GUID format `com.plexapp.agents.themoviedb://<tmdb_id>`.

## Technical Details

**Plex API Endpoints:**

```
# Find a show by TMDB ID
GET https://{plex_host}/library/search?guid=com.plexapp.agents.themoviedb://12345&X-Plex-Token={token}

# Get all seasons for a show
GET https://{plex_host}/library/metadata/{ratingKey}/children?X-Plex-Token={token}

# Get all episodes for a season
GET https://{plex_host}/library/metadata/{seasonRatingKey}/children?X-Plex-Token={token}
```

**Episode availability detection:**
```json
{
  "MediaContainer": {
    "Metadata": [
      {
        "title": "Episode 1",
        "index": 1,
        "parentIndex": 8,
        "Media": [...]     // has Media array = available on Plex
        // No "Media" key or empty array = not available
      }
    ]
  }
}
```

**Overseerr Status Codes:**

| Code | Status | Description |
|------|--------|-------------|
| 1 | unknown | Unknown status |
| 2 | pending | Request pending |
| 3 | processing | Currently processing |
| 4 | partially_available | Some episodes on Plex |
| 5 | available | All episodes on Plex |
| 6 | deleted | Removed from Plex |

## Trade-offs

| Approach | Pros | Cons |
|----------|------|------|
| **Query Plex live** | Always up-to-date | Slower, more API calls |
| **Cache in DB** | Fast, works offline | Needs refresh mechanism |

## Recommendation

Implement with **caching**:

1. Sync episode data from Plex when viewing TV details (or via background job)
2. Cache in Episode.status field (we already have this)
3. Provide manual "Refresh from Plex" button
4. Derive season status from per-episode statuses (not the other way around)
5. Optional: Plex webhook integration for real-time updates

## Implementation Order

1. Add Plex configuration to `config.py` and `.env`/`docker/.env`
2. Add `plex_rating_key` to Request model + Alembic migration
3. Create PlexService following existing service conventions (Settings injection, shared httpx client, `close()`)
4. Modify EpisodeSyncService to accept PlexService and override per-episode status
5. Update season status derivation: compute from episode statuses instead of copying Overseerr
6. Simplify dashboard `available_count` logic and pending filter
7. Add refresh-from-Plex endpoint and button
8. (Optional) Add Plex webhook endpoint for real-time updates

## Files to Modify/Create

- `app/siftarr/config.py` - Add `plex_url`, `plex_token`
- `app/siftarr/models/request.py` - Add `plex_rating_key` field
- `app/siftarr/services/plex_service.py` - **NEW**: Plex API integration
- `app/siftarr/services/episode_sync_service.py` - Inject PlexService, override episode status, derive season status
- `app/siftarr/routers/dashboard.py` - Simplify availability logic, add `/refresh-plex` endpoint
- `app/siftarr/templates/dashboard.html` - Add refresh button (modify existing)
- `db/alembic/versions/` - New migration for `plex_rating_key`
- `app/siftarr/main.py` - Update `LATEST_KNOWN_MIGRATION_REVISION`
- `docker/.env` - Add Plex credentials

## Simplification Opportunities (Enabled by This Feature)

These existing workarounds exist *because* Overseerr lacks per-episode data. With Plex integration, they can be removed or simplified:

| Current Workaround | Location | Simplification |
|---|---|---|
| `available_count = 0` for `PARTIALLY_AVAILABLE` seasons | `dashboard.py:540-545` | Just count episodes with `AVAILABLE` status |
| `episode_status = season_status` (all episodes copy season status) | `episode_sync_service.py:161` | Episodes get real status from Plex; seasons derive from episodes |
| `PARTIALLY_AVAILABLE` on individual episodes | `request.py:38` (enum) | Remove from episodes; only use for season-level derived status |
| Pending filter includes `partially_available` as "needs action" | `dashboard.py:269-281` | Check actual per-episode availability instead |
| `max_episode_discovery` heuristic when episode list unknown | `tv_decision_service.py:179-183` | Plex provides canonical episode list |
| Fallback season statuses from Overseerr request | `episode_sync_service.py:89-93` | Plex is ground truth; Overseerr fallback only for structure |

## Unanswered Questions (For User)

1. **Plex credentials**: Do you have a Plex token? If not, see https://support.plex.tv/articles/204059436-finding-an-authentication-token-x-plex-token/
2. **Plex server access**: Is the Plex server accessible from where Siftarr runs?
3. **Plex Pass**: Required for webhooks (real-time updates). Without it, we rely on manual/on-demand refresh.
4. **Plex rating key discovery**: Should we auto-discover by TMDB ID, or allow manual entry? TMDB ID matching is preferred but requires the show to be in your Plex library.