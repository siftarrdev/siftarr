## New Features
- Add option to only apply rules to requests from certain users or groups so certain peoples requests get filled at 4k while others get filled at 1080p. This would be a user or group based preference that can be set in the rules.
- Add indexer and release-source preferences, including allow/deny lists and weighted priorities
- Download history & stats - Track completed downloads, show indexer performance metrics, rule effectiveness stats.
- Custom post-processing hooks - Execute user-defined scripts when downloads complete (for renaming, notifications, etc.)
- Authentication & Authorization — Zero auth on any endpoint today. Add API-key header auth at minimum; ideally integrate with Overseerr's SSO or support basic auth. This is a critical gap
- Make the UI more responsive and user-friendly, with better feedback during searches and actions, and a more intuitive layout for rules and request management.

- Cleanup the setting section.

## Bugs

 - requests in overseerr arent being marked as approved/denied when they are approved/downloading/downloaded in siftarr. If a request is in overseerr and we detect it is in qbittorrent and or available in plex, we should mark it as approved in overseerr. This is a critical gap in the user experience and workflow.