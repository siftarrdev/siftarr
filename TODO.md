## New Features
- Add option to only apply rules to requests from certain users or groups so certain peoples requests get filled at 4k while others get filled at 1080p. This would be a user or group based preference that can be set in the rules.
- Add indexer and release-source preferences, including allow/deny lists and weighted priorities
- Download history & stats - Track completed downloads, show indexer performance metrics, rule effectiveness stats.
- Bulk staging actions - Approve/reject multiple staged torrents at once from the dashboard
- Custom post-processing hooks - Execute user-defined scripts when downloads complete (for renaming, notifications, etc.)
- Authentication & Authorization — Zero auth on any endpoint today. Add API-key header auth at minimum; ideally integrate with Overseerr's SSO or support basic auth. This is a critical gap


- Show release date in the request details modal, so users can see when a release actually came out vs when it was requested. This is especially important for older requests that may have been requested before the release date.



## Bugs
- fix the search all pending requests button. it seems to be broken and only searching the last episode and staging only one release. i seem to have to manually search and stage each episode in a season pack which is a pain.
