## New Features
- Add option to only apply rules to requests from certain users or groups so certain peoples requests get filled at 4k while others get filled at 1080p. This would be a user or group based preference that can be set in the rules.
- Add indexer and release-source preferences, including allow/deny lists and weighted priorities
- Download history & stats - Track completed downloads, show indexer performance metrics, rule effectiveness stats.

- Custom post-processing hooks - Execute user-defined scripts when downloads complete (for renaming, notifications, etc.)

- Make the UI more responsive and user-friendly, with better feedback during searches and actions, and a more intuitive layout for rules and request management.

- Cleanup the settings section. 
  - The connection settings should be collapsed by default. and plex should be first then overseerr, then prowlarr, then qbit. move the API access section into the connection settings section and make it more clear that this is for users who want to use the API.
  - The advanced / scheduler should be re-worked. this should allow users to specify the poll interval for qbit and overseerr separately, and it should allow users to specify the frequency of the plex sync for both the fast and full sync separately.
  - the current database stats should actually be removed.
  - the manual actions should be near the top of the page.
  - the only section that should be expanded by default should be the manual actions.
  
- There should be a new stats tab in the UI, on the level of the dashboard|rules|settings. This stats tab should show the number of requests in the system, the number of rules, and the number of downloads that have been processed by Siftarr. as well as all the historical stats including but not limited to % of autostaged requests approved, % of requests that were filled at 4k vs 1080p and any other stats that would be interesting to users.

- Add ability to sort and filter search result in details view. eg sort by size, only 4k or 1080p

- get default rules from a rules.json file supplied to the docker compose. this should be the same format as the rules export/import format.

## Bugs
