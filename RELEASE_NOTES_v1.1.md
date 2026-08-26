# smartimport v1.1

## Fixed

- `smartcleanup` now merges a later batch of the same still-pending release into the existing manual-review folder when the deterministic staging folder name is identical.
- The previous behavior created timestamp-suffixed sibling folders such as `Album-1787478431`, which split tracks from one new album across multiple manual-review directories when several songs arrived in separate SmartImport runs.
- Filename collisions remain non-destructive: if two files inside the same release have the same filename, smartimport keeps both by using its existing `_1`, `_2`, ... suffix behavior.

## Safety

This release intentionally does **not** change matching, MusicBrainz lookup, scoring, duplicate detection, track/disc resolution, staging group keys, or attach behavior. The existing frozen matching/import-core fingerprint test remains unchanged and passes on v1.1.
