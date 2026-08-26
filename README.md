# 🎵 smartimport

smartimport is a [beets](https://beets.io/) plugin, that aims on making the import process with beets as smooth and automatic as possible.

It first checks whether a new track belongs to an album that already exists in your beets library. If the match is safe, smartimport attaches the track directly to that existing album - no more duplicate albums in Navidrome because beets chooses the Europe-release for one song and the US-release for another song of the same album. 
If smartimport can't confirm an existing album, the files are staged for the normal beets importer. Ambiguous or incomplete files are kept separate for manual review instead of being force-matched.

The goal is simple: **make routine music intake more automatic while keeping beets in control of uncertain releases.**

## Features

- Automatically checks if a new song fits into an already existing album in your library.
- If it does, the new song gets attached to that album automatically - no more split albums in Navidrome.
- Resolves track and disc positions from the exact MusicBrainz release when possible.
- Handles multi-disc releases and global track numbering.
- Detects real duplicates before attaching a new file.
- Can optionally replace a matching beets database entry whose audio file is genuinely missing.
- Sends unknown releases to a staging folder for the **normal beets importer** to take over instead of inventing a match.
- Sends ambiguous, incomplete, or unsafe files to dedicated manual-review folders.
- Dry-run mode lets you preview decisions before files or the beets database are changed.
- Optional static artwork synchronization with beets `fetchart` / `embedart`.
- Optional native integration with [fetchanimated](https://github.com/claptraw/fetchanimated) for aquiring Apple Music animated album covers.
- Optional provider-independent notifications through [Apprise](https://github.com/caronc/apprise).
- Notification and artwork failures are isolated from the import itself and cannot turn a successful attach into a failed import.

## How smartimport fits into beets

smartimport does **not** replace the normal beets importer.

```text
New audio files
      ↓
  smartimport
      │
      ├─ Safe match to an existing album
      │      ↓
      │  Attach directly to that album
      │
      ├─ New / unknown release
      │      ↓
      │   Staging
      │      ↓
      │  Normal `beet import -m`
      │
      └─ Ambiguous / incomplete / unsafe
             ↓
        Manual review
```

This is especially useful for automated download folders where some files are additions to albums you already own while other files are completely new releases.

## Requirements

- A working beets installation with an up-to-date beets library database.
- Python 3.10 through 3.14.
- beets 2.13.1 or newer within the beets 2.x series.
- `musicbrainzngs` for smartimport's MusicBrainz fallback and exact release/track lookups.
- Optional: Apprise for notifications.
- Optional: beets `fetchart` / `embedart` for static album cover synchronization.
- Optional: [fetchanimated](https://github.com/claptraw/fetchanimated) for Apple Music animated album covers.

smartimport reads the albums already known to your beets database. It does not scan your entire music directory to discover existing albums on its own.

## Installation

There are two supported installation methods. **You do not have to use `pip install` for the smartimport plugin itself** if you prefer the classic beets `pluginpath` method.

### Option 1: Copy `smartimport.py` into your beets plugin folder

This is the simplest choice if you already manage custom beets plugins as individual `.py` files. Download `smartimport.py` from the GitHub Release or copy `beetsplug/smartimport.py` from this repository.

#### 1. Install the required Python dependency

smartimport uses the Python package `musicbrainzngs` to reliably identify album identities. Install it into the **same environment where beets runs**, e.g. your beets Docker container:

```bash
python -m pip install musicbrainzngs
```

If you also want Apprise notifications:

```bash
python -m pip install apprise
```

`musicbrainzngs` is a Python dependency, not another beets plugin. It is not embedded inside the standalone `smartimport.py` file, and you do not add it to the beets `plugins:` line.

#### 2. Copy the plugin file

Create a `beetsplug` folder next to your beets configuration if you do not already have one:

```text
beets-config/
├── config.yaml
└── beetsplug/
    └── smartimport.py
```

Copy `beetsplug/smartimport.py` from this repository into that folder.

Then open your beets `config.yaml` and point the `pluginpath` to the location of that `beetsplug` folder:

```yaml
pluginpath:
  - /path/to/beets-config/beetsplug
```

If you already use `pluginpath`, add the directory to the existing list instead of replacing it.

Add `smartimport` to your existing plugin list in your `config.yaml`:

```yaml
plugins: fetchart embedart smartimport
```

Do not remove your existing plugins; just add `smartimport`.

#### 3. Add the smartimport configuration

Copy the `smartimport:` section from [`examples/config.yaml`](examples/config.yaml) into your beets `config.yaml` and replace the example paths with your own paths.

Then, restart beets and you're ready to go. You may want to check if smartimport is running with the command `beet version` in your beets installation.

### Option 2: Install the packaged release

Use this method if you want Python to manage smartimport and its required Python dependencies for you.

Download the release wheel and install it into the same Python environment as beets:

```bash
python -m pip install /path/to/beets_smartimport-1.1.0-py3-none-any.whl
```

This automatically installs the required `musicbrainzngs` dependency. **You do not need to install `musicbrainzngs` separately when using the wheel/package installation.**

To install smartimport together with optional Apprise support:

```bash
python -m pip install "/path/to/beets_smartimport-1.1.0-py3-none-any.whl[notifications]"
```

When smartimport is installed as a Python package, no `pluginpath` entry is required. You still need to enable it in beets:

```yaml
plugins: fetchart embedart smartimport
```

You can also install directly from a checked-out repository:

```bash
python -m pip install .
```

or with Apprise support:

```bash
python -m pip install '.[notifications]'
```

## Docker installation

If beets runs inside Docker, the important rule is:

> smartimport and its Python dependencies must exist **inside the beets container**, not only on the Docker host.

Your beets `config.yaml`, smartimport paths, and `pluginpath` also need to use paths that are valid **inside the container**.

### Docker: manual plugin-file method

If your beets configuration is mounted into the container, you can keep `smartimport.py` in that persistent config volume, for example:

```text
/config/
├── config.yaml
└── beetsplug/
    └── smartimport.py
```

Then use:

```yaml
pluginpath:
  - /config/beetsplug
```

The Python dependency still needs to be installed inside the image. For a persistent setup, add it to your custom Dockerfile instead of installing it manually after every container recreation:

```dockerfile
FROM your-existing-beets-image

RUN python -m pip install --no-cache-dir musicbrainzngs
```

With Apprise notifications:

```dockerfile
FROM your-existing-beets-image

RUN python -m pip install --no-cache-dir musicbrainzngs apprise
```

A one-time `docker exec ... pip install ...` can be useful for testing, but the installed packages may disappear when the container is recreated.

### Docker: packaged release method

You can also bake the smartimport wheel directly into your beets image. This automatically installs `musicbrainzngs` as a dependency:

```dockerfile
FROM your-existing-beets-image

COPY beets_smartimport-1.1.0-py3-none-any.whl /tmp/
RUN python -m pip install --no-cache-dir \
    /tmp/beets_smartimport-1.1.0-py3-none-any.whl \
    && rm /tmp/beets_smartimport-1.1.0-py3-none-any.whl
```

With Apprise support:

```dockerfile
FROM your-existing-beets-image

COPY beets_smartimport-1.1.0-py3-none-any.whl /tmp/
RUN python -m pip install --no-cache-dir \
    "/tmp/beets_smartimport-1.1.0-py3-none-any.whl[notifications]" \
    && rm /tmp/beets_smartimport-1.1.0-py3-none-any.whl
```

An example Dockerfile is included under [`examples/docker/`](examples/docker/).

## Verify the installation

If beets runs as a long-lived container or service, restart that container/service after changing the plugin installation or configuration.

Then run:

```bash
beet version
```

`smartimport` should appear in the loaded plugin list.

Before the first real run, always test:

```bash
beet smartimport --dry-run
```

## Configuration

All five workflow paths are required and must point to different absolute paths. 

```yaml
# Add smartimport to your existing beets plugin list, for example:
plugins: fetchart embedart smartimport

smartimport:
  incoming: /path/to/downloads/incoming
  staging: /path/to/downloads/auto-match
  manual: /path/to/downloads/manual-review
  duplicates: /path/to/downloads/duplicates
  failed: /path/to/downloads/failed

  # Incoming files must remain unchanged for this long before processing.
  # Prevents that files are being processed, that haven't been downloaded completely.
  min_age_minutes: 2

  # Conservative matching defaults.
  local_score_threshold: 75
  local_score_margin: 15
  duration_tolerance_seconds: 5
  musicbrainz_fallback: true
  prefer_existing_album: true

  # Disabled by default. When enabled, smartimport may replace a matching
  # beets database item only when its referenced audio file is actually missing.
  replace_missing_items: false

  # Optional artwork integrations.
  sync_artwork: true
  sync_animated_artwork: false

  extensions:
    - flac
    - mp3
    - m4a
    - mp4
    - ogg
    - opus
    - wav
    - aiff
    - aif

  notifications:
    enabled: false
    # apprise_config: /path/to/apprise.conf
    tag: ""
    notify_on_success: false
    notify_on_attention: true
    notify_on_failure: true
    notify_on_noop: false
    notify_on_dry_run: false
```

The complete configuration with comments is available in [`examples/config.yaml`](examples/config.yaml).

### Workflow folders

- `incoming` - your normal downloads folder; new audio files waiting for smartimport.
- `staging` - coherent releases that should be handled by the normal beets importer.
- `manual` - files that need your manual review.
- `duplicates` - confirmed duplicates.
- `failed` - technical failures such as unreadable files or failed direct attachment.

smartimport creates subfolders in those workflow folders so you know why a file landed there, such as `ambiguous-match`, `missing-required-tags`, `attach-error`, or `beets-match-uncertain`.

## Everyday workflow

### 1. Put new files into `incoming`

smartimport waits until a file has been unchanged for at least `min_age_minutes` before it becomes eligible. The default is two minutes, which helps avoid processing files that are still downloading or being copied.

### 2. Preview the decisions

```bash
beet smartimport --dry-run
```

No files or beets database entries are changed.

### 3. Process the files

```bash
beet smartimport
```

Safe existing-album matches are attached directly. Unknown coherent releases are moved to `staging`. Unsafe cases are routed to review folders.

### 4. Import staged releases with normal beets

smartimport deliberately leaves new releases to the normal beets autotagger.

Use beets' move mode so successfully imported files are consumed from staging:

```bash
beet import -m /path/to/staging/release-folder
```

This works independently of your normal global beets `copy` / `move` settings.

### 5. Clean up anything beets did not consume

```bash
beet smartcleanup
```

Files still remaining in staging are moved into the manual-review area instead of silently staying behind. If a later SmartImport run produces the exact same deterministic release folder while that release is still waiting for manual review, `smartcleanup` merges the new files into the existing release folder instead of creating a timestamp-suffixed sibling folder. Matching decisions are not re-run or changed by this merge.

A small POSIX automation example is included in [`examples/automation.sh`](examples/automation.sh).

## Commands

| Command | Purpose |
|---|---|
| `beet smartimport --dry-run` | Preview smartimport decisions without changing anything. |
| `beet smartimport` | Process eligible files from `incoming`. |
| `beet smartimport --no-musicbrainz` | Run once without the MusicBrainz fallback. |
| `beet smartcleanup --dry-run` | Preview which staged files would be sent to manual review. |
| `beet smartcleanup` | Move staged files left behind by beets to manual review. |
| `beet smartrepair` | Audit safe album-identity inconsistencies without writing changes. |
| `beet smartrepair --apply` | Apply safe MusicBrainz album/release-group repairs. |
| `beet smartnotifytest` | Send a test notification through Apprise. |

For additional beets output while testing:

```bash
beet -vv smartimport --dry-run
```

## MusicBrainz dependency

smartimport uses `musicbrainzngs` for its MusicBrainz fallback and exact release/track lookups.

What you need to do depends on how smartimport is installed:

| smartimport installation | What you need to do |
|---|---|
| Release wheel / `python -m pip install .` | Nothing extra. `musicbrainzngs` is installed automatically as a declared dependency. |
| Manual `smartimport.py` + `pluginpath` | Run `python -m pip install musicbrainzngs` in the same Python environment/container as beets. |

You do **not** add `musicbrainzngs` to the beets `plugins:` list.

## Apprise notifications

Notifications are optional. smartimport does not hard-code a notification provider; Apprise lets you choose any service supported by your Apprise installation.

### 1. Install Apprise

If you installed the release wheel with the `[notifications]` extra, Apprise is already installed.

For a manual plugin-file installation:

```bash
python -m pip install apprise
```

### 2. Create an Apprise configuration file

For example:

```text
# /path/to/apprise.conf
# Add one or more real Apprise service URLs here.
discord://WEBHOOK_ID/WEBHOOK_TOKEN
tgram://BOT_TOKEN/CHAT_ID
```

Keep the real file outside Git because service URLs normally contain credentials.

A placeholder-only example is included as [`examples/apprise.conf.example`](examples/apprise.conf.example).

### 3. Enable notifications in smartimport

```yaml
smartimport:
  notifications:
    enabled: true
    apprise_config: /path/to/apprise.conf
    tag: ""
    notify_on_success: false
    notify_on_attention: true
    notify_on_failure: true
    notify_on_noop: false
    notify_on_dry_run: false
```

If beets runs in Docker, `apprise_config` must be the path to the file **inside the container**.

By default smartimport notifies only when attention is useful: manual-review/duplicate results or failures. You can enable successful-run, no-op, or dry-run notifications separately.

### 4. Test the notification setup

```bash
beet smartnotifytest
```

A successful test sends an English test message through the configured Apprise services.

Notification errors during normal smartimport runs are non-fatal. A broken notification service cannot change a match, move a file, remove a database entry, or turn a successful import into a failure.

## Static and animated artwork

### Static covers

With:

```yaml
sync_artwork: true
```

smartimport can run its existing-album artwork post-step when beets `fetchart` / `embedart` are available. Artwork problems are treated as cosmetic and do not roll back a successful track attachment.

### Apple Music animated album artwork with fetchanimated

[fetchanimated](https://github.com/claptraw/fetchanimated) is a separate beets plugin for downloading Apple Music animated album covers.

If both plugins are installed and loaded, smartimport can call fetchanimated natively after attaching a track to an existing album:

```yaml
plugins: fetchart embedart fetchanimated smartimport

smartimport:
  sync_animated_artwork: true
```

If fetchanimated is not installed, leave the option disabled. A missing or failed animated-artwork post-step never causes the smartimport attachment itself to fail.

## Replacing missing beets items

The safe public default is:

```yaml
replace_missing_items: false
```

With this setting, a matching beets database entry whose audio file is missing is sent to manual review.

If you deliberately enable:

```yaml
replace_missing_items: true
```

smartimport checks again that the old file is still missing immediately before replacement. The new item must be added, written, and moved successfully **before** the stale database row is removed. If the old file reappears or the new write/move fails, smartimport keeps the old database entry.

## Metadata safety

When smartimport attaches a track to an existing album, it synchronizes the album identity required to keep the new track on the intended local album and resolves track/disc position when possible.

smartimport does **not** clear or overwrite incoming `comments`, `genres`, or `style` merely because a track was attached to an existing album. These fields may be track-specific or managed by another plugin.

## What smartimport does not do

- It does not replace beets' normal autotagger for new releases.
- It does not force ambiguous matches.
- It does not require a specific downloader, music server, NAS, Docker image, folder layout, or notification provider.
- It does not require FetchAnimated or Apprise for the normal import workflow.
- It does not silently enable missing-file replacement; that feature is opt-in.
- It does not make artwork or notification failures fatal to an otherwise successful attach.

## Troubleshooting

| Problem | What to check |
|---|---|
| `smartimport` does not appear in `beet version` | Check the `plugins:` line. For manual installs, also check `pluginpath`. For package installs, verify it was installed into the same Python environment as beets. |
| `musicbrainzngs is not installed` | Manual plugin installs need `python -m pip install musicbrainzngs` in the beets environment/container. Package/wheel installs pull it automatically. |
| smartimport cannot see files | Use paths that exist inside the environment/container where beets runs and verify filesystem permissions. |
| Files never become ready | Check `min_age_minutes` and whether another application is still modifying the files. |
| A track goes to `ambiguous-match` | smartimport found no sufficiently safe unique existing-album match. Review it manually rather than lowering thresholds blindly. |
| A release remains in staging after `beet import` | Use `beet import -m ...` so beets consumes the staged files, then run `beet smartcleanup`. |
| Apprise test fails | Verify Apprise is installed, the config path is correct inside the beets environment, and the service URL works. |
| Artwork fails but the track imported | This is expected safety behavior; artwork post-steps are non-fatal. |

## Development

For development from a source checkout:

```bash
python -m pip install '.[test,notifications]'
pytest
python -m build
```

The test suite protects the production-tested matching/scoring core and covers configuration safety, metadata preservation, stale-item replacement, artwork isolation, notifications, and package registration.

## AI-assisted development

AI-assisted tools were used during development and documentation. AI-generated or AI-suggested changes included in releases were reviewed and tested by the maintainer.

## License

The source code in this repository is released under the MIT License. See [`LICENSE`](LICENSE).

## Legal disclaimer

smartimport is an independent, unofficial project and is not affiliated with or endorsed by beets, MusicBrainz, Apprise, Apple, or Apple Music.

Users are responsible for their own music files, metadata, service usage, backups, and compliance with applicable terms and laws.
