<div align="center">

<img src="icons/brand/siftarr-network-hub.png" alt="Siftarr" width="120">

# Siftarr

**Choose the right release before it reaches your download client.**

Siftarr is a FastAPI web app that sits between Overseerr, Prowlarr, Plex, and qBittorrent. It searches, filters, scores, stages, and sends media releases according to rules you control.

[Getting started](#getting-started-docker-compose) · [Features](#features) · [First run](#first-run) · [Troubleshooting](#troubleshooting)

</div>

---

## Why Siftarr?

Use Siftarr when you want more control than “grab the first result”. Keep bad releases out, prefer the qualities and groups you like, review staged decisions, and retry requests that have no acceptable match yet.

```text
Overseerr webhook/manual sync ──► Siftarr ──► staged review or qBittorrent
                                  │
                                  ├──► Prowlarr release search
                                  └──► Plex availability/completion checks
```

## Features

- **Rule-based release decisions**: exclusions, requirements, weighted scoring, and size limits.
- **Movie + TV aware**: TV searches prefer season packs when useful and fall back to missing episodes.
- **Safe staging mode**: review selected torrents before sending them to qBittorrent.
- **Pending retries**: retry requests that do not have an acceptable release yet.
- **Plex SSO browser access**: first Plex login claims the instance as the admin account.
- **Dashboard controls**: inspect requests, releases, staged items, activity, stats, and settings.
- **Operational tools**: connection tests, Overseerr sync, Plex sync, default-rule seeding, qBit move/retention settings, and manual maintenance actions.

## Integrations

| Integration | What Siftarr uses it for |
| --- | --- |
| **Overseerr** | Request webhooks, request metadata, and manual request sync. |
| **Prowlarr** | Indexer searches by stable media IDs and title strategies. |
| **qBittorrent** | Sending approved torrents/magnets and completion/move tracking. |
| **Plex** | Browser SSO, availability checks, and library sync/polling. |

## Getting started: Docker Compose

Docker Compose is the recommended way to run Siftarr.

1. Create a directory for Siftarr and add this `docker-compose.yml`:

   ```yaml
   services:
     siftarr:
       image: ghcr.io/siftarrdev/siftarr:latest
       container_name: siftarr
       restart: unless-stopped
       env_file:
         - .env
       ports:
         - "8000:8000"
       volumes:
         - ./data:/data
         # Optional first-run rule seed exported from Siftarr's Rules page:
         # - ./rules.json:/data/config/rules.json:ro
   ```

2. Create `.env` next to the Compose file:

   ```dotenv
   TZ=UTC

   OVERSEERR_URL=http://overseerr:5055
   OVERSEERR_API_KEY=your_overseerr_key

   PROWLARR_URL=http://prowlarr:9696
   PROWLARR_API_KEY=your_prowlarr_key

   QBITTORRENT_URL=http://qbittorrent:8080
   QBITTORRENT_API_KEY=your_qbittorrent_web_api_key

   PLEX_URL=http://plex:32400

   # Optional: provide a fixed programmatic/API key instead of the generated one.
   # SIFTARR_API_KEY=change_me_to_a_long_random_value
   ```

3. Start Siftarr:

   ```bash
   docker compose up -d
   ```

4. Open <http://localhost:8000>.

> Running from a cloned checkout? Use the local build Compose file instead:
>
> ```bash
> docker compose -f docker/docker-compose.yml up -d --build
> ```
>
> See [docker/README.md](docker/README.md) for image, volume, helper-script, and dev-container details.

## First run

1. Open Siftarr and sign in with the Plex account that should administer this instance.
2. Wait for the initial Plex sync gate to complete.
3. Go to **Settings** and confirm Overseerr, Prowlarr, qBittorrent, and Plex URLs/credentials.
4. Use **Test** or **Test All** to verify connectivity, then save.
5. Leave staging mode enabled while you review the first few decisions.
6. Go to **Rules** and create, import, or tune your release rules.
7. Add the Overseerr webhook below if you want automatic request intake.

### Overseerr webhook

In Overseerr, open **Settings → Notifications → Webhooks** and add:

```text
http://your-siftarr-host:8000/webhook/overseerr
```

Enable request events such as **Media Requested** and **Media Approved**. If a webhook is missed, run an Overseerr sync from Siftarr **Settings**.

## Day-to-day use

- **Dashboard**: monitor active, pending, staged, rejected, downloading, and completed requests; inspect release results and activity.
- **Rules**: add exclusions, requirements, scoring boosts, and size limits; import/export rules for backup or reuse.
- **Staged**: approve or discard selected releases when staging mode is enabled.
- **Stats**: review historical decisions and rule outcomes.
- **Settings**: manage integrations, scheduler intervals, API key, Plex sync, qBit mover/retention, and maintenance jobs.

Typical lifecycle:

```text
received ──► searching ──► pending retry
   │              │              │
   │              └──► rejected ◄┘
   │
   └──► staged ──► approved ──► downloading ──► completed
          │
          └──► discarded
```

When staging mode is disabled, accepted releases skip review and go directly to qBittorrent.

## Rules at a glance

| Rule type | Effect | Common use |
| --- | --- | --- |
| **Exclusion** | Rejects matching releases. | Block CAM/TS, unwanted tags, bad groups. |
| **Requirement** | Requires at least one pattern. | Require WEB-DL, 1080p/2160p, language, codec. |
| **Scoring** | Adds points to matching releases. | Prefer groups, codecs, HDR, remuxes. |
| **Size limit** | Rejects releases outside a range. | Cap huge files or reject tiny fakes. |

A release must pass exclusions, requirements, and size limits before score decides the winner.

## Data and configuration

Mount `/data` persistently. It contains the SQLite database, generated API/session secrets, settings, and staged torrent artifacts.

| Path | Purpose |
| --- | --- |
| `/data/db/` | SQLite database, app settings, generated API key, generated session secret. |
| `/data/staging/` | Staged torrent files and staging artifacts. |
| `/data/config/rules.json` | Optional read-only first-run rules seed file. |

Most settings can be edited in the UI. Useful environment variables include:

| Variable | Default | Notes |
| --- | --- | --- |
| `OVERSEERR_URL`, `OVERSEERR_API_KEY` | unset | Overseerr connection. |
| `PROWLARR_URL`, `PROWLARR_API_KEY` | unset | Prowlarr connection. |
| `QBITTORRENT_URL`, `QBITTORRENT_API_KEY` | unset | qBittorrent Web UI/API connection. |
| `PLEX_URL` | unset | Plex server URL; Plex token is normally managed by SSO. |
| `SIFTARR_API_KEY` | generated | Programmatic/webhook API key. |
| `SECRET_KEY` | generated under `/data/db/` | Optional explicit browser session signing key. |
| `SIFTARR_DB_PATH` | `/data/db/siftarr.db` | SQLite path used when `DATABASE_URL` is not set. |
| `DATABASE_URL` | SQLite under `/data/db/` | Full database URL override. |
| `STAGING_MODE_ENABLED` | `true` | Stage selected releases before qBittorrent. |
| `RETRY_INTERVAL_HOURS` | `24` | Pending retry cadence. |
| `MAX_RETRY_DURATION_DAYS` | `7` | Pending retry window. |

## Troubleshooting

- **Cannot log in**: the first Plex account to sign in claims the instance; use that account for future browser access.
- **Webhook not arriving**: confirm Overseerr can reach `/webhook/overseerr` on the Siftarr host and port.
- **No releases found**: verify Prowlarr indexers work and the request has TMDB/TVDB metadata.
- **Everything is rejected**: test rules from the **Rules** page and check exclusions/requirements first.
- **qBittorrent send fails**: verify URL, API key, Web UI settings, and container networking.
- **Sessions reset after restart**: make sure `/data` is persistent or set a stable `SECRET_KEY`.
- **Database/permission errors**: check host ownership/write permissions for the mounted data directory.

## Developers

Development setup and contribution workflow live in [CONTRIBUTING.md](CONTRIBUTING.md). Repository orientation lives in [repo-map.md](repo-map.md), and additional documentation starts at [docs/README.md](docs/README.md).

## License

MIT — see [LICENSE](LICENSE).
