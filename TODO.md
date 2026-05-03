## New Features
- Add option to only apply rules to requests from certain users or groups so certain peoples requests get filled at 4k while others get filled at 1080p. This would be a user or group based preference that can be set in the rules.
- Add indexer and release-source preferences, including allow/deny lists and weighted priorities

## Bugs
- A bulk search of torrents only resulted in very few staged torrents. And one of the movie releases has two staged torrents. there should only be max of one staged torrent per movie.
- make sure that the searched torrents details shows the number of files in the torrent for both tv shows or packs and movies.
- make the qbittorrent state wrap around to multiple lines when the column isn't big enough. this should be the case for everything in all of the columns in the dashboards.
- remove enabled tag from scope in the rules tab. there is already an enabled toggle.
- allow sorting by columns in teh rules tab.
- allow filtering in the rules tab. as well as sowing just movie rules or just tv show rules.

## NEXT: 
- Searching for a large list of torrents takes a while and there is no UI indication that the search is in progress. Add a loading spinner or progress bar to indicate that the search is ongoing, especially for large searches that may take several seconds to complete.
