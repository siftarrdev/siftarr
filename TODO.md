## New Features
- Add option to only apply rules to requests from certain users or groups
- Add request timeline/audit view per request
- Add indexer and release-source preferences, including allow/deny lists and weighted priorities
- Make settings sections collapsible for easier navigation

## Project quality improvements
- Improve error handling so the app can gracefully handle unexpected errors and provide useful feedback
- Tighten router input validation so bad payloads fail loudly
- Split large router modules into thinner handlers plus service methods
- Add integration tests for the full webhook -> search -> rule evaluation -> staging/qBit flow
- Add structured decision logs/metrics for search latency, rule failures, pending retries, and qBit handoff
- Increase unit test coverage to 85% (currently at 47%; focus on router integration tests and complex async service tests)

- rely on plex for media availability instead of qbittorrent or overseerr's internal state. this will allow the app to be more resilient to errors in those systems and provide a better user experience when media is already available but the app's state is out of sync. We should only need overseerr to fetch the requests from users and to fetch the release info and movie posters. 


## Bugs
- exclusion pattern matches shouldn't report as an error in the dashboard
- send logs for rejected release when other release already sent to qbittorrent. also the sent release is downloaded by the torrent is still in staging.
- the Xgb/season display in the dashboard is always showing red, even when the release is under any rules governing the size of a season pack.