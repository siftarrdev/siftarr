## New Features
- Posters aren't correctly being shown in the details view, need to investigate if this is an issue with the image proxy or if the images are not being saved to the database correctly
- Add option to only apply rules to requests from certain users or groups
- Increase unit test coverage to 85% (currently at 47% - need router integration tests and complex async service tests)
- make the different sections in settings as collapsible sections to make it easier to navigate
- harmonise the UI design of the dashboard and settings pages to make it more consistent and visually appealing. Include better use of spacing, typography, and color to improve readability and user experience.

- add a tab in the dashboard to see finished requests. it will show requests that are completed and downlaoded.
- add a tab in the dashboard to see rejected requests. it will show requests that have been rejected by the rules engine, along with the reason for rejection if provided.
- when rejecting a torrent, add an option to provide a reason for the rejection that is then saved to the database and can be viewed in the dashboard
- When searching for torrents with prowlarr we should make sure to add the movie year to the search query if we have it available from the media request, this should help improve the accuracy of search results and reduce the chances of incorrect matches being returned.
- When doing multiple prowlarr searches at the same time we should run them concurrently to speed up the search process, we can use asyncio.gather to run multiple search tasks at the same time and then aggregate the results once they are all complete.

## Bugs