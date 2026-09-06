# Release selection and download recovery

Quality rules express preferences, but a high score does not establish that a torrent
contains the right show, directly playable files, or a reachable complete seed.
Selection keeps these checks separate and records the evidence behind its choices.

## Rejections, approval, and recovery

Rejecting a candidate or manually discarding a staged pick records a durable decision
for that request. Search refreshes cannot erase it. Automatic selection filters those
decisions before ranking so the next eligible candidate can win. Automatic pack
supersession does not turn every covered episode into a permanent rejection.

Manual approval keeps the selected release. When current rules, identity, rejection
history, or stale seeder observations need an override, the browser explains the
issues and asks for explicit confirmation. Cancelling sends nothing to qBittorrent.
Bulk approval checks every selected torrent before submitting any of them.

An explicit manual override does not erase a durable rejection. Integrations can
clear it with `DELETE /requests/{request_id}/releases/{release_id}/reject` using the
normal authentication. A subsequent search re-evaluates the candidate normally.

The Torrent Status tab shows persisted download health. Warnings start after six
hours without progress; stalled torrents become eligible for replacement review
after 24 hours by default. Paused, stopped, queued, and checking time does not count.
Missing torrents are reported separately and never count as completed downloads.

Choosing **Review replacement** requires confirmation and checks qBittorrent again.
The action is refused if the torrent has resumed or completed, is suspended, or no
longer belongs to an active request. It temporarily suppresses the failed release
by default, resets only its affected coverage, and opens request details for manual
review. Permanent rejection is also available. It does not start a replacement,
delete the old torrent, remove downloaded files, or unzip anything.

Existing discarded rows are not blindly converted into permanent rejections during
upgrade because historical bulk cleanup and rejection cannot always be distinguished.
Existing active approved rows acquire health observations as the scheduler polls.

## File counts and archives

For individual movies and exact single TV episodes, the default structural adjustments are:

| Evidence | Score adjustment |
| --- | ---: |
| Known count of 1 to 5 files | +5 |
| 6 to 10 files | 0 |
| More than 10 files | -10 |
| Unknown or zero file count | 0 |
| Confirmed archive payload | -100 |
| Explicit RAR title marker without confirmed contents | -50 |

File-count preferences do not apply to season packs, multi-episode releases, complete
series, or movie collections. Archives receive a bounded penalty, never automatic
rejection. Unknown contents are not evidence of an archive. A `NORAR` title is a weak
hint, not proof, and cannot override archive paths in actual torrent metadata.

These defaults use a September 2026 read-only production sample as a starting point.
About 79% of cached movie and episode candidates had five files or fewer. Nearly half
of season candidates had more than ten. Live torrent file lists also showed legitimate
artwork, metadata, and subtitle files in the six-to-ten range. These observations support
the thresholds, but do not prove an optimal score weight or a download success rate.

Prowlarr file counts are indexer-supplied. Actual paths are only available when supplied
by the integration or when torrent metainfo can be inspected. Magnet contents cannot
be known before metadata arrives. Siftarr does not add a magnet to qBittorrent merely
to inspect it, download media for inspection, or extract archives.

References: [BEP 3 metainfo](https://www.bittorrent.org/beps/bep_0003.html) and
[Torznab attributes](https://torznab.github.io/spec-1.3-draft/torznab/Specification-v1.3.html).

## Downloadability preferences

TV identity checks compare the leading series title rather than accepting a prefix.
This distinguishes `Top Gear US` and `Top Gear Extra Gear` from `Top Gear`. Regional
qualifiers can also be legitimate. Configure only trusted aliases with the original
series year, for example:

```dotenv
TRUSTED_TV_TITLE_ALIASES={"top gear|2002":["Top Gear UK"],"home fires|2015":["Home Fires UK"]}
```

Aliases are empty by default. They do not waive an explicit year mismatch or other
rules. Without a trusted alias, an ambiguous manual choice requires confirmation.

Automatic selection excludes releases reporting zero seeders. Small reported swarms
also receive a bounded score penalty: 40 points for one or two seeders, and 10 points
for three or four. Equal adjusted scores use seeders as a tie-break.

An indexer count is an observation, not a health check. A saved search result does not
become fresh merely because it is loaded again. Approval must distinguish current rule
evaluation from the age of the underlying seeder observation.

## Environment settings

These settings are read by the application, not stored as editable regex rules. A
deployment must restart with the changed environment for new values to take effect.

| Variable | Default | Purpose |
| --- | ---: | --- |
| `RELEASE_FILE_COUNT_BONUS` | 5 | Small single-release file-count bonus |
| `RELEASE_HIGH_FILE_COUNT_PENALTY` | 10 | Large single-release file-count penalty |
| `RELEASE_ARCHIVE_PENALTY` | 100 | Confirmed archive payload penalty |
| `RELEASE_RAR_TITLE_PENALTY` | 50 | Unconfirmed RAR title penalty |
| `RELEASE_LOW_SEED_PENALTY` | 40 | One or two reported seeders |
| `RELEASE_MARGINAL_SEED_PENALTY` | 10 | Three or four reported seeders |
| `TV_EPISODE_FALLBACK_MAX_SIZE_GB` | 3 | Exact-episode fallback ceiling; zero disables |
| `RELEASE_OBSERVATION_MAX_AGE_HOURS` | 24 | Threshold for stale approval evidence |
| `DOWNLOAD_STALL_WARNING_HOURS` | 6 | No-progress warning threshold |
| `DOWNLOAD_STALL_RECOVERY_HOURS` | 24 | User-confirmed recovery threshold |
| `RELEASE_FAILURE_COOLDOWN_HOURS` | 24 | Temporary failed-release suppression |

The recovery threshold must not precede the warning threshold. Penalty settings are
nonnegative amounts subtracted from the score; setting an adjustment to zero disables it.

## Existing rule corrections

Application upgrades must not silently overwrite an administrator's rules. Export the
current rules before making targeted corrections and preserve unrelated fields.

For a camera/screener exclusion, include common compound tags explicitly:

```regex
\b(?:CAM|HDCAM|TS|HDTS|TC|HDTC|TELESYNC|TELECINE|SCR|DVDSCR|SCREENER)\b
```

For an HEVC preference, recognize common H265 spellings:

```regex
x265|HEVC|H[ ._-]?265
```

A rule named `1080p Movie` should use the `movie` scope if it is not intended to add
points to TV releases too. `HC` indicates hardcoded subtitles and is not by itself a
camera recording. Do not add it to the exclusion merely because it appeared in an
unwanted release.

Keep the preferred episode size limit when enabling a larger fallback. A fallback
must not waive exclusions, identity checks, minimum-size requirements, or coverage
constraints, and should only be considered when no eligible normal-size release is
available for that episode.
