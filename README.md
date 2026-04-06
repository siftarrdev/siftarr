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
    image: ghcr.io/yourusername/arbitratarr:latest
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
| `OVERSEERR_URL` | Overseerr base URL | Yes |
| `OVERSEERR_API_KEY` | Overseerr API key | Yes |
| `PROWLARR_URL` | Prowlarr base URL | Yes |
| `PROWLARR_API_KEY` | Prowlarr API key | Yes |
| `QBITTORRENT_URL` | qBittorrent Web UI URL | Yes |
| `QBITTORRENT_USERNAME` | qBittorrent username | Yes |
| `QBITTORRENT_PASSWORD` | qBittorrent password | Yes |
| `TZ` | Timezone (default: UTC) | No |
| `PUID` | User ID for file permissions (default: 1000) | No |
| `PGID` | Group ID for file permissions (default: 1000) | No |

### Volume Structure

The `/data` volume contains:

- `/data/db/` - SQLite database files
- `/data/staging/` - Staged torrent files (when staging mode is enabled)

## Setup Guide

### 1. Configure Overseerr Webhook

1. In Overseerr, go to **Settings → Notifications → Webhooks**
2. Add a new webhook with URL:
   ```
   http://your-arbitratarr:8000/webhook/overseerr
   ```
3. Enable **"Media Requested"** and **"Media Approved"** events
4. Test the webhook to ensure connectivity

### 2. Configure Arbitratarr Rules

Access the web UI at `http://localhost:8000` and navigate to the Rules page:

#### Exclusion Rules
Release titles matching exclusion patterns are automatically rejected.

**Example:** `CAM|TS|HDCAM|SCR` rejects camera recordings and screeners.

#### Requirement Rules
Release titles must match at least one requirement pattern.

**Example:** `1080p|2160p|720p` requires HD resolution.

#### Scorer Rules
Matching scorer patterns adds points to the release's score.

| Pattern | Points | Description |
|---------|--------|-------------|
| `x265\|HEVC` | +50 | Prefer HEVC codec |
| `MeGusta` | +100 | Prefer MeGusta releases |
| `SPiCYLAMA\|LAMA` | +100 | Prefer SPiCYLAMA releases |

The highest-scoring release that passes all filters is selected.

### 3. Staging Mode (Optional)

When staging mode is enabled in Settings:

1. Approved releases are saved to `/data/staging/` as `.torrent` and `.json` files
2. Files are named: `{title}_{release_group}_{id}.torrent`
3. Review torrents in the Dashboard and approve or discard them
4. Approved torrents are then sent to qBittorrent

## Usage

### Web Interface

Access the web UI at `http://localhost:8000`:

- **Dashboard**: View active requests, pending items, and staged torrents
- **Rules**: Configure filtering and scoring rules
- **Settings**: Toggle staging mode, trigger manual retries, view logs

### Request Status Flow

```
received → searching → pending (no matches, retry later)
                         ↓
               staged (if staging mode on)
                         ↓
               downloading → completed
```

### Manual Operations

From the Settings page, you can:

- **Trigger Manual Retry**: Immediately retry all pending requests
- **Toggle Staging Mode**: Enable/disable staging mode
- **View Logs**: Check application logs for debugging

## Architecture

```
┌─────────────┐     ┌──────────────┐     ┌────────────┐
│  Overseerr  │────▶│ Arbitratarr  │────▶│ qBittorrent│
│  (requests) │     │   (logic)    │     │ (download) │
└─────────────┘     └──────────────┘     └────────────┘
                           │
                           ▼
                    ┌──────────────┐
                    │   Prowlarr   │
                    │  (indexers)  │
                    └──────────────┘
```

## Features

### Smart TV Show Handling

For TV requests, Arbitratarr follows a season-first approach:

1. **Search Season Packs**: Queries Prowlarr for complete season packs
2. **Evaluate**: If a pack passes all rules with the highest score, it's selected
3. **Fallback**: If no suitable pack found, searches for individual episodes

### Strict ID Matching

To avoid mismatches, all Prowlarr searches use the exact `tmdbid` or `tvdbid` from Overseerr, not text search.

### Category Tagging

Downloads are automatically tagged with categories for your *arr applications:

- Movies: `radarr` category
- TV Shows: `sonarr` category

## Troubleshooting

### Common Issues

**Webhooks not working:**
- Verify Overseerr can reach Arbitratarr (check network/DNS)
- Check Arbitratarr logs for webhook payload errors
- Ensure API keys are correct

**No search results:**
- Verify Prowlarr indexers are healthy
- Check that Prowlarr can reach indexers
- Review exclusion rules (might be too strict)

**qBittorrent connection failed:**
- Verify qBittorrent Web UI is enabled
- Check credentials and URL
- Ensure network connectivity between containers

**Files not in staging:**
- Check staging mode is enabled in Settings
- Verify `/data/staging/` directory exists and has write permissions
- Check PUID/PGID match your user

### Getting Help

- Check the [Issues](https://github.com/yourusername/arbitratarr/issues) page
- Review application logs in the Settings page
- Enable debug logging for more details

## Development

Interested in contributing? See [CONTRIBUTION.md](CONTRIBUTION.md) for development setup and guidelines.

## License

MIT License - See [LICENSE](LICENSE) file for details.
