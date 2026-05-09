"""Siftarr services package.

Services are organized into thematic subpackages:

- ``admin/`` — Settings, scheduler, Plex polling
- ``dashboard/`` — Dashboard, search, detail views
- ``decisions/`` — Rule engine, decision pipeline
- ``integrations/`` — Prowlarr, qBittorrent, Overseerr, Plex, connection tester
- ``lifecycle/`` — Request lifecycle, activity log, episode sync
- ``releases/`` — Release parsing, serialization, storage, staging
- ``utils/`` — HTTP client, async utilities, type helpers, media helpers

Flat (cross-cutting):
- ``auth_service`` — API key verification
- ``metadata_service`` — Metadata enrichment
- ``request_service`` — Request loading / validation
"""
