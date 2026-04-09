<div align="center">

<img src="icons/brand/siftarr-network-hub.png" alt="Siftarr" width="120">

# Siftarr

**Media release filtering and scoring middleware**

Sits between Overseerr, Prowlarr, and qBittorrent to give you granular control over which torrents get downloaded.

</div>

---

## How it works

```
Overseerr ──► Siftarr ──► qBittorrent
                  │
                  ▼
             Prowlarr
```

1. Overseerr sends a media request webhook to Siftarr
2. Siftarr searches Prowlarr by TMDB/TVDB ID for accurate matching
3. Every release is evaluated against your custom rules (exclusions, requirements, scorers, size limits)
4. The best-scoring release that passes all filters wins — and is either staged for your review or sent straight to qBittorrent

### Key features

- **Rule engine** — Exclude bad releases, require quality benchmarks, score preferred groups/codecs
- **Season-first TV logic** — Searches for season packs first, falls back to individual episodes
- **Staging mode** — Hold torrents for manual review before they reach qBittorrent
- **Automatic retries** — Pending requests are retried every 24 hours for up to 7 days
- **Dark-mode web UI** — Dashboard, rules editor, and settings all in one place

---

## Quick Start

### Docker Compose

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

That's it — all connection settings (Overseerr, Prowlarr, qBittorrent) can be configured from the **Settings** page in the web UI and are saved to the database. Pin a specific version with `ghcr.io/siftarrdev/siftarr:v1.2.3` if you prefer.

<details>
<summary>Optional environment variables</summary>

You can also pre-configure any setting via environment variables. Values set in the web UI's Settings page take precedence over env vars.

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
    environment:
      - TZ=UTC
      - PUID=1000
      - PGID=1000
      - OVERSEERR_URL=http://overseerr:5055
      - OVERSEERR_API_KEY=your_key
      - PROWLARR_URL=http://prowlarr:9696
      - PROWLARR_API_KEY=your_key
      - QBITTORRENT_URL=http://qbittorrent:8080
      - QBITTORRENT_USERNAME=admin
      - QBITTORRENT_PASSWORD=your_password
```

| Variable | Default | Description |
|----------|---------|-------------|
| `TZ` | `UTC` | Timezone |
| `PUID` / `PGID` | `1000` | UID/GID for file permissions |
| `OVERSEERR_URL` / `OVERSEERR_API_KEY` | — | Overseerr connection |
| `PROWLARR_URL` / `PROWLARR_API_KEY` | — | Prowlarr connection |
| `QBITTORRENT_URL` / `QBITTORRENT_USERNAME` / `QBITTORRENT_PASSWORD` | — | qBittorrent connection |
| `STAGING_MODE_ENABLED` | `true` | Hold torrents for review before download |
| `RETRY_INTERVAL_HOURS` | `24` | How often to retry pending requests |
| `MAX_RETRY_DURATION_DAYS` | `7` | Give up after this many days |
| `DATABASE_URL` | `sqlite+aiosqlite:///data/db/siftarr.db` | Database connection string |

</details>

<details>
<summary>Building from source</summary>

Use the helper script to rebuild, restart, and tail logs in one step:

```bash
./docker/rebuild-run-logs.sh
```

Or build manually:

```bash
cd docker/
docker compose build siftarr
docker compose up -d siftarr
```

</details>

### Volume structure

| Path | Contents |
|------|----------|
| `/data/db/` | SQLite database |
| `/data/staging/` | Staged `.torrent` files and decision log (when staging mode is on) |

---

## Setup guide

### 1. Connect Overseerr webhook

1. Go to **Settings → Notifications → Webhooks** in Overseerr
2. Add a webhook pointing to `http://your-siftarr:8000/webhook/overseerr`
3. Enable **Media Requested** and **Media Approved** events

### 2. Create your rules

Open `http://localhost:8000/rules` to configure how releases are filtered and scored:

| Rule type | What it does | Example |
|-----------|-------------|---------|
| **Exclusion** | Rejects matching releases | `CAM\|TS\|HDCAM` |
| **Requirement** | Release must match at least one pattern | `1080p\|2160p` |
| **Scorer** | Adds points to matching releases | `x265` → +50 |
| **Size limit** | Rejects releases outside a size range | Min 1 GB, max 50 GB |

Rules can be scoped to **movies**, **TV**, or **both**. Lower-priority rules are evaluated first.

### 3. Optional: Enable staging mode

Staging mode (on by default) saves matching torrents to disk instead of sending them directly to qBittorrent. You can approve or discard each one from the dashboard.

Toggle it from **Settings** in the web UI.

---

## Using the web UI

Visit `http://localhost:8000` for the dashboard:

- **Dashboard** — View active requests, pending queue, staged torrents, completed and rejected items
- **Rules** — Create, edit, and reorder your filtering and scoring rules, with a test endpoint to try release titles against your ruleset
- **Settings** — Test connections to Overseerr/Prowlarr/qBittorrent, toggle staging mode, sync requests from Overseerr, reseed default rules, trigger manual retries

### Request lifecycle

```
received ──► searching ──► pending (retry every 24h)
                               │
                    ┌──────────┴──────────┐
                    ▼                     ▼
               staged*                downloading
                (review)               (in qBit)
                    │                     │
                    ▼                     ▼
                approved ──► completed ◄────┘
                 or
                discarded

* Staging step only if staging mode is enabled
```

---

## License

MIT — see [LICENSE](LICENSE).