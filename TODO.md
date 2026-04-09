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

