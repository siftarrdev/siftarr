## New Features
- Add option to only apply rules to requests from certain users or groups so certain peoples requests get filled at 4k while others get filled at 1080p. This would be a user or group based preference that can be set in the rules.
- Add indexer and release-source preferences, including allow/deny lists and weighted priorities
- Download history & stats - Track completed downloads, show indexer performance metrics, rule effectiveness stats.
- Custom post-processing hooks - Execute user-defined scripts when downloads complete (for renaming, notifications, etc.)
- Authentication & Authorization — Zero auth on any endpoint today. Add API-key header auth at minimum; ideally integrate with Overseerr's SSO or support basic auth. This is a critical gap


- Make the UI more responsive and user-friendly, with better feedback during searches and actions, and a more intuitive layout for rules and request management.
- Make the UI better for mobile devices. Most of the UI is good. but some of the text boxes are small and hard to use on mobile. and or turn into vertical lines of text instead of sensible text boxes. Most notably in the details view.

## Bugs
- when initiating a serach it seems to fail the search if i navigate to another page before the search is complete. I would expect the search to trigger and complete regardless of where i navigate in the UI.
