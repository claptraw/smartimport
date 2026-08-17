# Security

SmartImport moves files and can modify a Beets library. Before enabling an automated loop:

- run `beet smartimport --dry-run` on representative input;
- keep backups of the Beets database and music library;
- use distinct absolute paths for incoming, staging, manual review, duplicates, and failures;
- leave `replace_missing_items: false` unless you explicitly want missing-file database rows to be replaced;
- keep credentials and Apprise service URLs outside the repository.

Please report security issues privately to the repository maintainer rather than posting secrets in a public issue.
