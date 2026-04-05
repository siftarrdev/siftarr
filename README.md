# Arbitratarr

Media search and download decision middleware for users who want granular control over release selection.

## Overview

Arbitratarr sits between Overseerr (requests), Prowlarr (indexers), and qBittorrent. It provides:

- **Rule-based filtering**: Reject releases matching exclusion patterns (e.g., CAM, TS, HDCAM)
- **Rule-based scoring**: Prefer releases matching scorer patterns (e.g., x265, specific release groups)
- **Season-first TV logic**: Prefers season packs, falls back to individual episodes
- **Staging mode**: Review torrents before sending to qBittorrent
- **Automatic retry**: Pending requests are retried every 24 hours

## Quick Start

### Docker Compose

```yaml
version: "3.8"

services:
  arbitratarr:
    build: .
    container_name: arbitratarr
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
      - OVERSEERR_API_KEY=your_api_key
      - PROWLARR_URL=http://prowlarr:9696
      - PROWLARR_API_KEY=your_api_key
      - QBITTORRENT_URL=http://qbittorrent:8080
      - QBITTORRENT_USERNAME=admin
      - QBITTORRENT_PASSWORD=your_password
```

### Configuration

Create a `.env` file or set environment variables:

| Variable | Description | Required |
|----------|-------------|----------|
| `OVERSEERR_URL` | Overseerr URL | Yes |
| `OVERSEERR_API_KEY` | Overseerr API key | Yes |
| `PROWLARR_URL` | Prowlarr URL | Yes |
| `PROWLARR_API_KEY` | Prowlarr API key | Yes |
| `QBITTORRENT_URL` | qBittorrent URL | Yes |
| `QBITTORRENT_USERNAME` | qBittorrent username | Yes |
| `QBITTORRENT_PASSWORD` | qBittorrent password | Yes |
| `TZ` | Timezone (default: UTC) | No |
| `PUID` | User ID for file permissions | No |
| `PGID` | Group ID for file permissions | No |

### Overseerr Webhook Setup

1. In Overseerr, go to Settings → Notifications → Webhooks
2. Add a new webhook with the following URL:
   ```
   http://your-arbitratarr:8000/webhook/overseerr
   ```
3. Enable the "Media Requested" and "Media Approved" events

## Usage

### Web Interface

Access the web UI at `http://localhost:8000`:

- **Dashboard**: View active requests, pending items, and staged torrents
- **Rules**: Configure filtering and scoring rules
- **Settings**: Toggle staging mode, trigger manual retries

### Rule Configuration

#### Exclusion Rules
Release titles matching exclusion patterns are automatically rejected.

Example: `CAM|TS|HDCAM|SCR` rejects camera recordings and screeners.

#### Requirement Rules
Release titles must match at least one requirement pattern.

Example: `1080p|2160p|720p` requires HD resolution.

#### Scorer Rules
Matching scorer patterns adds points to the release's score.

| Pattern | Points |
|---------|--------|
| `x265\|HEVC` | +50 |
| `MeGusta` | +100 |
| `SPiCYLAMA\|LAMA` | +100 |

The highest-scoring release that passes all filters is selected.

### Staging Mode

When staging mode is enabled:
1. Approved releases are saved to `/data/staging/` instead of being sent to qBittorrent
2. Files are named: `{title}_{release_group}_{id}.torrent` and `.json`
3. Review and approve/discard from the Dashboard

### Status Flow

```
received → searching → pending (no matches, retry later)
                        ↓
              staged (staging mode on)
                        ↓
              downloading → completed
```

## Development

### Setup

```bash
# Install dependencies
uv sync

# Run database migrations
uv run alembic upgrade head

# Run the application
uv run uvicorn arbitratarr.main:app --reload
```

### Code Quality

```bash
# Format code
ruff format .

# Lint
ruff check .

# Type check
pyright
```

### Testing

```bash
pytest tests/
```

## Architecture

```
┌─────────────┐     ┌──────────────┐     ┌────────────┐
│  Overseerr  │────▶│ Arbitratarr  │────▶│  qBittorrent │
│  (requests) │     │   (logic)    │     │ (download) │
└─────────────┘     └──────────────┘     └────────────┘
                           │
                           ▼
                    ┌──────────────┐
                    │   Prowlarr   │
                    │  (indexers)  │
                    └──────────────┘
```

## License

MIT
