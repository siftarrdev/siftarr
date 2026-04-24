<div align="center">

<img src="icons/brand/siftarr-network-hub.png" alt="Siftarr" width="120">

# Siftarr

**Media release filtering and scoring middleware**

Siftarr sits between your request, indexer, and download tools so you can decide which releases are accepted, rejected, staged for review, or sent to qBittorrent.

</div>

---

## What Siftarr does

Siftarr receives approved media requests, searches indexers through Prowlarr, evaluates every candidate against your rules, then either stages the best torrent for review or sends it directly to qBittorrent.

```text
Overseerr webhook ──► Siftarr ──► qBittorrent
                         │
                         ├──► Prowlarr search
                         └──► Plex availability checks
```

Use Siftarr when you want more control than a simple first-match download flow: block unwanted releases, require quality terms, prefer trusted groups/codecs, limit sizes, and keep a manual approval step before anything reaches your download client.

## Features

- **Release rules**: regex exclusions, regex requirements, weighted scoring, and size limits.
- **Movie and TV awareness**: rules can apply to movies, TV, or both; TV size limits can target episodes or season packs.
- **Season-first TV searches**: Siftarr looks for season packs before falling back to individual episodes.
- **Staging mode**: save selected torrents for review before sending them to qBittorrent.
- **Pending retries**: items with no acceptable release stay pending and are retried on a schedule.
- **Web dashboard**: view active, pending, staged, rejected, completed, and manual-search items.
- **Connection testing and maintenance actions**: validate integrations, sync Overseerr requests, reseed default rules, and trigger retries from the UI.
- **Plex polling support**: use Plex to help track media availability and recent scans.

## Supported integrations

| Integration | Purpose | Required? |
|-------------|---------|-----------|
| **Overseerr** | Sends request webhooks and provides request metadata. | Yes, for automated request intake |
| **Prowlarr** | Searches configured indexers by TMDB/TVDB IDs. | Yes |
| **qBittorrent** | Receives approved torrents or magnets. | Yes, unless only reviewing staged decisions |
| **Plex** | Checks library availability and recent scan state. | Optional but recommended |

## Install and deploy

### Option 1: Docker Compose image

Create a Compose file such as:

```yaml
services:
  siftarr:
    image: ghcr.io/siftarrdev/siftarr:latest
    container_name: siftarr
    restart: unless-stopped
    ports:
      - "8000:8000"
    volumes:
      - ./data:/data
```

Start it with:

```bash
docker compose up -d
```

Then open `http://localhost:8000`.

### Option 2: Local Docker build

If you are running from a cloned checkout and want to build the image locally, use the Compose file in `docker/`:

```bash
docker compose -f docker/docker-compose.yml up -d --build
```

### Optional environment configuration

Most settings can be entered in the web UI after first launch. Environment variables are useful for pre-seeding values or running non-interactively. Values saved in **Settings** are used at runtime.

```yaml
environment:
  - TZ=UTC
  - OVERSEERR_URL=http://overseerr:5055
  - OVERSEERR_API_KEY=your_key
  - PROWLARR_URL=http://prowlarr:9696
  - PROWLARR_API_KEY=your_key
  - QBITTORRENT_URL=http://qbittorrent:8080
  - QBITTORRENT_USERNAME=admin
  - QBITTORRENT_PASSWORD=your_password
  - PLEX_URL=http://plex:32400
  - PLEX_TOKEN=your_token
```

Common variables:

| Variable | Default | Description |
|----------|---------|-------------|
| `TZ` | `UTC` | Timezone used by the app. |
| `PUID` / `PGID` | `1000` | Container file ownership IDs. |
| `OVERSEERR_URL` / `OVERSEERR_API_KEY` | unset | Overseerr connection. |
| `PROWLARR_URL` / `PROWLARR_API_KEY` | unset | Prowlarr connection. |
| `QBITTORRENT_URL` | unset | qBittorrent Web UI URL. |
| `QBITTORRENT_USERNAME` / `QBITTORRENT_PASSWORD` | `admin` / `adminadmin` | qBittorrent credentials. |
| `PLEX_URL` / `PLEX_TOKEN` | unset | Plex connection. |
| `STAGING_MODE_ENABLED` | `true` | Stage selected torrents instead of sending directly. |
| `RETRY_INTERVAL_HOURS` | `24` | How often pending requests are retried. |
| `MAX_RETRY_DURATION_DAYS` | `7` | How long pending requests remain retryable. |
| `PLEX_POLL_INTERVAL_MINUTES` | `360` | Plex polling interval. |
| `MAX_EPISODE_DISCOVERY` | `30` | Maximum episodes discovered per TV sync pass. |
| `PLEX_RECENT_SCAN_INTERVAL_MINUTES` | `5` | Recent Plex scan polling interval. |
| `OVERSEERR_SYNC_CONCURRENCY` | `16` | Overseerr sync concurrency. |
| `PLEX_SYNC_CONCURRENCY` | `16` | Plex sync concurrency. |
| `SIFTARR_DB_PATH` | `/data/db/siftarr.db` | SQLite database path used to build the default database URL. |
| `DATABASE_URL` | SQLite under `/data/db/` | Full database URL override. |

## First-run setup

1. Open Siftarr at `http://localhost:8000`.
2. Go to **Settings**.
3. Enter URLs and credentials for Overseerr, Prowlarr, qBittorrent, and optionally Plex.
4. Use **Test** beside each service, or **Test All**, to confirm connectivity.
5. Save settings.
6. Keep staging mode enabled for the first few requests so you can verify release decisions before downloads start.
7. Go to **Rules** and review or create your filtering and scoring rules.

## Overseerr webhook setup

In Overseerr:

1. Open **Settings → Notifications → Webhooks**.
2. Add a webhook pointing to `http://your-siftarr-host:8000/webhook/overseerr`.
3. Enable request events such as **Media Requested** and **Media Approved**.
4. Save the webhook and send a test notification if available.

Siftarr can also sync requests from Overseerr from the **Settings** page if a webhook was missed or Siftarr was offline.

## Using the web UI

- **Dashboard**: monitor active requests, pending searches, staged torrents, rejected releases, completion state, and manual release searches.
- **Rules**: create, edit, enable/disable, delete, and test release rules.
- **Settings**: manage integrations, staging mode, database stats, default rules, sync jobs, retries, and Plex scheduler status.

Typical request lifecycle:

```text
received ──► searching ──► pending retry
   │              │              │
   │              └──► rejected ◄┘
   │
   └──► staged ──► approved ──► downloading ──► completed
          │
          └──► discarded
```

When staging mode is disabled, accepted releases skip the staged step and are sent directly to qBittorrent.

## Rules overview

Rules decide which release wins:

| Rule type | Effect | Example use |
|-----------|--------|-------------|
| **Exclusion** | Rejects any matching release. | Block `CAM`, `TS`, `HDCAM`, unwanted languages, or bad groups. |
| **Requirement** | Requires at least one matching pattern. | Require `1080p`, `2160p`, `WEB-DL`, or a preferred codec. |
| **Scoring** | Adds points to matching releases. | Prefer `x265`, HDR, specific release groups, or remuxes. |
| **Size limit** | Rejects releases outside a configured size range. | Cap huge files or reject files that are too small. |

Rules can be scoped to movies, TV, or both. A release must pass exclusions, requirements, and size limits before its score matters. The highest-scoring passing release is selected.

## Staging behavior

Staging mode is enabled by default for safer operation.

When enabled:

1. Siftarr saves the selected `.torrent` under `/data/staging/`.
2. The staged item appears in the dashboard.
3. You can **Approve** it to send it to qBittorrent or **Discard** it to remove it.

When disabled:

1. Siftarr sends the selected torrent or magnet directly to qBittorrent.
2. qBittorrent categories are chosen from the media type so downstream tools can process downloads.

## Data volumes

Mount `/data` somewhere persistent. Do not store it only inside the container filesystem.

| Container path | Contents |
|----------------|----------|
| `/data/db/` | SQLite database and persisted app settings. |
| `/data/staging/` | Staged torrent files and staging artifacts. |

Back up `/data/db/` before upgrades or major rule changes if you need rollback safety.

## Troubleshooting

- **Webhook is not arriving**: verify the Overseerr webhook URL is reachable from the Overseerr container/host and points to `/webhook/overseerr`.
- **No releases found**: confirm Prowlarr has working indexers and that the requested media has TMDB/TVDB metadata.
- **Everything is rejected**: use the Rules page test tool and check exclusion, requirement, and size-limit rules first.
- **qBittorrent send fails**: confirm the Web UI URL, credentials, and network path from Siftarr to qBittorrent.
- **Plex status is empty or stale**: verify the Plex URL/token and check the Plex scheduler status on the Settings page.
- **Staged files are missing after restart**: ensure `/data` is mounted to persistent storage.
- **Database or permission errors**: check ownership and write access for the host directory mounted to `/data`.

For development setup and contribution workflow, see [CONTRIBUTING.md](CONTRIBUTING.md). For the documentation index and component guides, see [docs/README.md](docs/README.md).

## License

MIT — see [LICENSE](LICENSE).
