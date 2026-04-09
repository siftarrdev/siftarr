# Project Specification: Siftarr

## 1. Project Overview
Siftarr is a custom Python middleware application that handles media search and download decisions. It sits between Overseerr (requests), Prowlarr (indexers), and qBittorrent. It is designed for users who want granular control over release selection that standard *arr apps do not provide.

## 2. Technology Stack
* **Language:** Python 3.12+ (managed by `uv`).
* **Backend:** FastAPI (handles Webhooks, REST API, and Async Background Tasks).
* **Frontend:** FastAPI + Jinja2 Templates + Tailwind CSS (via CDN).
* **Database:** SQLite (stored in `/data/db/`).
* **Download Client:** qBittorrent via `qbittorrent-api`.

## 3. Infrastructure & Configuration
The app is designed to run in a minimal Docker container using `docker-compose`.

### 3.1 Volume Mount
A single mount point `/data` must contain:
* `/data/db/` - SQLite database files.
* `/data/staging/` - Directory for `.torrent` files when in Staging Mode.

### 3.2 Environment Variables
Configuration is handled strictly via environment variables. The app should read these on startup.
* **System:** `TZ`, `PUID` (e.g., 1000), `PGID` (e.g., 1000) to ensure compatibility with file ACLs.
* **Overseerr:** `OVERSEERR_URL`, `OVERSEERR_API_KEY`
* **Prowlarr:** `PROWLARR_URL`, `PROWLARR_API_KEY`
* **qBittorrent:** `QBITTORRENT_URL`, `QBITTORRENT_USERNAME`, `QBITTORRENT_PASSWORD`

## 4. Logic & Workflows

### 4.1 Search Strategy (Strict ID Matching)
To avoid mismatching, the Prowlarr Torznab API queries MUST use the `tmdbid` or `tvdbid` provided by the Overseerr payload, not raw text strings.

### 4.2 TV Show Decision Logic (Season-First)
When a TV request is received:
1.  **Search Season Pack:** Query Prowlarr for the entire season pack.
2.  **Evaluate Packs:** If results exist, evaluate them against the Rule Engine. If a pack passes all filters and meets the highest score, send to qBit and stop.
3.  **Fallback to Episodes:** If no Season Pack passes, initiate searches for individual episodes. Evaluate and send them one by one.

### 4.3 The "Pending" Queue & Background Tasks
* If no results are found in Prowlarr, or if all results are rejected by the Rule Engine, the request status becomes **"Pending"**.
* Use FastAPI Background Tasks (or `APScheduler`) to run an async job every 24 hours that:
    1. Polls Overseerr for any missed approved requests.
    2. Re-queries Prowlarr for all items in the "Pending" queue.

### 4.4 Rule Engine Criteria
* **Size Limits:** Global max/min file size thresholds (e.g., `< 10GB`).
* **Regex Exclusions:** Immediate rejection if pattern matches (e.g., `CAM|TS|HDCAM`).
* **Regex Requirements:** Release must match at least one (e.g., `1080p|2160p`).
* **Scoring:** Weighted points for specific groups or codecs (e.g., `x265` = +50, `FraMeSToR` = +100). Pick the release with the highest total score that passes all filters.

## 5. Web Interface
* **Styling:** Must use Tailwind CSS. **Must be forced into Dark Mode** (e.g., `<html class="dark">` and heavily utilize `dark:bg-gray-900`, `dark:text-white`, etc.).
* **Dashboard:** Tabbed view for "Active Requests", "Pending Search", and "Staged Torrents".
* **Staged View:** A list of torrents currently in `/data/staging/`. Provide a button to "Approve" (sends to qBit) or "Discard" (deletes file).
* **Rules Page:** Interface to manage the regex lists, size limits, and scoring values.

## 6. Action Execution
* **Category Tagging:** When sending to qBittorrent, use the category `radarr` or `sonarr` based on media type so external media managers can process the completed downloads.
* **Staging Mode Toggle:** A global setting (can be in DB or Env). If enabled, save `.torrent` files to `/data/staging/`. If disabled, send magnet/torrent directly to qBittorrent.