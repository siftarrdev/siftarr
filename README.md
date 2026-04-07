# Siftarr

Media search and download decision middleware for users who want granular control over release selection.

![Siftarr icon](icons/brand/siftarr-network-hub.png)

## Overview

Siftarr sits between Overseerr (requests), Prowlarr (indexers), and qBittorrent:

```
Overseerr → Siftarr → qBittorrent
                ↓
            Prowlarr
```

**Features:**
- Rule-based filtering and scoring for releases
- Season-first TV logic (prefers season packs, falls back to episodes)
- Staging mode to review torrents before download
- Automatic retry for pending requests every 24 hours

## Quick Start

### Docker Compose

```yaml
services:
  siftarr:
    image: ghcr.io/yourusername/siftarr:latest
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

Use `ghcr.io/yourusername/siftarr:latest` for the newest published image, or pin a release tag like `ghcr.io/yourusername/siftarr:v1.2.3`.

For local source builds, use the helper script:

```bash
./docker/rebuild-run-logs.sh
```

It brings the container down, rebuilds from the latest git tag, starts the new container, and tails the logs.

### Required Environment Variables

| Variable | Description |
|----------|-------------|
| `OVERSEERR_URL` / `OVERSEERR_API_KEY` | Overseerr connection |
| `PROWLARR_URL` / `PROWLARR_API_KEY` | Prowlarr connection |
| `QBITTORRENT_URL` / `QBITTORRENT_USERNAME` / `QBITTORRENT_PASSWORD` | qBittorrent connection |

### Volume Structure

The `/data` volume contains:
- `/data/db/` - SQLite database
- `/data/staging/` - Staged torrent files (when staging mode is enabled)

## Setup

### 1. Configure Overseerr Webhook

1. Go to **Settings → Notifications → Webhooks**
2. Add webhook URL: `http://your-siftarr:8000/webhook/overseerr`
3. Enable **"Media Requested"** and **"Media Approved"** events

### 2. Configure Rules

Access the web UI at `http://localhost:8000/rules`:

- **Exclusions**: Patterns that reject releases (e.g., `CAM|TS|HDCAM`)
- **Requirements**: Patterns releases must match (e.g., `1080p|2160p`)
- **Scorers**: Patterns that add points (e.g., `x265` = +50)

### 3. Optional: Enable Staging Mode

In Settings, enable staging mode to review torrents before they're sent to qBittorrent.

## Usage

The web UI at `http://localhost:8000` provides:
- **Dashboard**: Active requests, pending items, staged torrents
- **Rules**: Configure filtering and scoring
- **Settings**: Toggle staging mode, trigger manual retries

### Request Flow

```
received → searching → pending (retry every 24h)
                          ↓
                 staged (if staging mode on)
                          ↓
                 downloading → completed
```

## Development

See [docs/CONTRIBUTING.md](docs/CONTRIBUTING.md) for development setup.

## License

MIT License - See [LICENSE](LICENSE) file.
