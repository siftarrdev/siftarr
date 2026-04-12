## New Features
- Add option to only apply rules to requests from certain users or groups
- Add request timeline/audit view per request
- Add manual "choose another release" UI to re-run selection from stored candidates
- Add per-media-type download path and category overrides instead of fixed qBit categories
- Add indexer and release-source preferences, including allow/deny lists and weighted priorities
- Make settings sections collapsible for easier navigation
- Increase unit test coverage to 85% (currently at 47%; focus on router integration tests and complex async service tests)
- Add an option to click to the previous or next title in the list when in the details view, so you can easily jump to the next request without going back to the main list
- allow size rules to apply to tv seasons separately from individual episodes, since season packs can be much larger than single eps and it's reasonable to want different limits on those

## Project quality improvements
- Refactor the codebase for better separation of concerns and more consistent coding style
- Improve error handling so the app can gracefully handle unexpected errors and provide useful feedback
- Replace startup schema-repair hacks with a single explicit migration/compatibility path
- Tighten router input validation so bad payloads fail loudly
- Split large router modules into thinner handlers plus service methods
- Add integration tests for the full webhook -> search -> rule evaluation -> staging/qBit flow
- Add structured decision logs/metrics for search latency, rule failures, pending retries, and qBit handoff

## Bugs
- Searching for tv shows that don't have full seasons is not showing any results because the search is only looking for season packs; need to also search for individual episodes and allow rules to match on those
- exclusion pattern matches shouldn't report as an error in the dashboard
- send logs for rejected release when other release already sent to qbittorrent. also the sent release is downloaded by the torrent is still in staging.

- When a new tv season request is found, the app currently doesnt seem to be correctly searching for the season packs. There should also be a search all button above the seasons that looks for multiple season packs so that possible the whole series or a large part of it can be obtained by a single download. This may require a third rule type for tv seasons. then when calculating if a pack is withing the size rules we can take into account the number of seasons and use the tv season size limits as a per season limit instead of the total size of the pack. eg a pack a s1-5 with a total per season limit of 15gb, would mean we would allow the pack if it's under 75gb, but if it was a pack of s1-2 we would only allow it if it was under 30gb. This would allow us to get large parts of series that are still within reasonable size limits, while also allowing for larger season packs when they contain more seasons.
