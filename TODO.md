## New Features
- Add option to only apply rules to requests from certain users or groups so certain peoples requests get filled at 4k while others get filled at 1080p. This would be a user or group based preference that can be set in the rules.
- Add indexer and release-source preferences, including allow/deny lists and weighted priorities
- Download history & stats - Track completed downloads, show indexer performance metrics, rule effectiveness stats.

- Custom post-processing hooks - Execute user-defined scripts when downloads complete (for renaming, notifications, etc.)

- Make the UI more responsive and user-friendly, with better feedback during searches and actions, and a more intuitive layout for rules and request management.

- There should be a new stats tab in the UI, on the level of the dashboard|rules|settings. This stats tab should show the number of requests in the system, the number of rules, and the number of downloads that have been processed by Siftarr. as well as all the historical stats including but not limited to % of autostaged requests approved, % of requests that were filled at 4k vs 1080p and any other stats that would be interesting to users.

- Add ability to sort and filter search result in details view. eg sort by size, only 4k or 1080p

- get default rules from a rules.json file supplied to the docker compose. this should be the same format as the rules export/import format.

## Bugs
- after logging in, if we restart the server the user is logged out and has to log in again. we should persist the login session across server restarts.