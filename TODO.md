## New Features
- Add option to only apply rules to requests from certain users or groups so certain peoples requests get filled at 4k while others get filled at 1080p. This would be a user or group based preference that can be set in the rules.
- Add indexer and release-source preferences, including allow/deny lists and weighted priorities
- Download history & stats - Track completed downloads, show indexer performance metrics, rule effectiveness stats.
- Bulk staging actions - Approve/reject multiple staged torrents at once from the dashboard
- Custom post-processing hooks - Execute user-defined scripts when downloads complete (for renaming, notifications, etc.)
- Authentication & Authorization — Zero auth on any endpoint today. Add API-key header auth at minimum; ideally integrate with Overseerr's SSO or support basic auth. This is a critical gap

## Bugs
- the requested on date seems to be the date the request was loaded from overseerr rather than the date the request was made.
- searching for and staging season packs doesnt work well. If i select a pack that is outside of the normal rules. it doenst show up in the staging area, and then i am unsure if try and stage additional season if they will be staged as well or they will replace the force staged pack.