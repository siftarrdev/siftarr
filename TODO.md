## New Features
- Sort/filter dashboard by request title, type status requested by, and more
- add option to approve or deny requests directly from the dashboard
- add option to view request details (including synopsis, cover photo and torrent matches) from the dashboard when focusing on a request
- Rules should have the option to only apply to movies or TV shows, or both
- Add option to only apply rules to requests from certain users or groups
- Increase unit test coverage to 85% (currently at 47% - need router integration tests and complex async service tests)
- Requests and settings don't seem to be populating into the dashboard when the server is restarted, need to investigate if this is a caching issue or if the requests are not being saved to the database correctly

## Bugs