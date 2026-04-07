## New Features
- Posters aren't correctly being shown in the details view, need to investigate if this is an issue with the image proxy or if the images are not being saved to the database correctly
- Add option to only apply rules to requests from certain users or groups
- Increase unit test coverage to 85% (currently at 47% - need router integration tests and complex async service tests)
- make the different sections in settings as collapsible sections to make it easier to navigate
- harmonise the UI design of the dashboard and settings pages to make it more consistent and visually appealing. Include better use of spacing, typography, and color to improve readability and user experience.

## Bugs
- Failed requests without a rejection_reason are invisible in the dashboard (not in Active, Finished, or Rejected tabs)
- Bulk reject action passes no rejection reason, making bulk-rejected requests invisible in the Rejected tab
- TVDecisionService doesn't propagate rejection reasons to the pending queue or return dict (MovieDecisionService does)
- asyncio.gather in TVDecisionService called without return_exceptions=True — a single search failure crashes all in-flight searches
- No concurrency limiter (asyncio.Semaphore) for Prowlarr searches — a show with 20+ episodes fires 20+ simultaneous requests
- Year can be None if Overseerr is unreachable at request creation time, with no retroactive fill mechanism
- Duplicated year extraction logic between webhooks.py and settings.py