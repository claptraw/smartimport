from __future__ import annotations

import hashlib
import os
import re
import shlex
import shutil
import time
import unicodedata
import uuid
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Iterable

from beets import plugins as beets_plugins
from beets.library import Item
from beets.plugins import BeetsPlugin
from beets.ui import Subcommand, UserError


__version__ = "1.0.0"
REPOSITORY_URL = "https://github.com/claptraw/smartimport"

DEFAULT_AUDIO_EXTENSIONS = (
    ".flac",
    ".mp3",
    ".m4a",
    ".mp4",
    ".ogg",
    ".opus",
    ".wav",
    ".aiff",
    ".aif",
)
AUDIO_EXTENSIONS = frozenset(DEFAULT_AUDIO_EXTENSIONS)

# Human-readable route subdirectories used by the public workflow.
# Matching decisions never depend on these names.
PUBLIC_ROUTE_NAMES = {
    "unreadable": "unreadable",
    "missing_required_tags": "missing-required-tags",
    "ambiguous_match": "ambiguous-match",
    "stale_library_entry": "stale-library-entry",
    "attach_error": "attach-error",
    "missing_album_tag": "missing-album-tag",
    "incoherent_release_group": "incoherent-release-group",
    "cleanup_manual": "beets-match-uncertain",
}
VARIOUS_ARTISTS = {
    "various artists",
    "various",
    "va",
}

VERSION_MARKERS = {
    "acoustic",
    "edit",
    "extended",
    "instrumental",
    "live",
    "mix",
    "radio",
    "remaster",
    "remastered",
    "remix",
    "slowed",
    "sped up",
    "version",
}

# Album-level fields that must remain identical for every track in one local
# Beets album. In particular, Navidrome's default persistent album ID uses the
# MusicBrainz album ID when available and otherwise falls back to album artist,
# album title and release date. Mixed values can therefore split one Beets album
# into several Navidrome albums.
IDENTITY_FIELDS = (
    "album",
    "albumartist",
    "albumartist_credit",
    "albumartist_sort",
    "albumartists",
    "albumartists_credit",
    "albumartists_sort",
    "albumdisambig",
    "albumstatus",
    "albumtype",
    "albumtypes",
    "asin",
    "barcode",
    "catalognum",
    "comp",
    "country",
    "day",
    "label",
    "language",
    "mb_albumartistid",
    "mb_albumartistids",
    "mb_albumid",
    "mb_releasegroupid",
    "month",
    "original_day",
    "original_month",
    "original_year",
    "release_group_title",
    "releasegroupdisambig",
    "script",
    "year",
)


# Conservative repair set. Fields such as genres, labels, catalog numbers and
# styles can legitimately differ at track level and are not rewritten by the
# repair command.
SAFE_REPAIR_FIELDS = (
    "mb_albumartistid",
    "mb_albumartistids",
    "mb_albumid",
    "mb_releasegroupid",
)

# These fields participate in Navidrome's fallback album identity when no
# MusicBrainz album ID is present. They may represent genuinely different
# releases, so they are only written for explicitly selected album IDs.
FALLBACK_REPAIR_FIELDS = (
    "album",
    "albumartist",
    "albumdisambig",
    "year",
    "month",
    "day",
)

REPAIR_FIELDS = SAFE_REPAIR_FIELDS + FALLBACK_REPAIR_FIELDS


def text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", "replace")
    return str(value)


def normalize(value: Any) -> str:
    value = unicodedata.normalize("NFKC", text(value))
    value = value.casefold().strip()
    value = value.replace("’", "'").replace("‘", "'")
    value = value.replace("–", "-").replace("—", "-")
    value = re.sub(r"\s+", " ", value)
    return value


def primary_artist(value: Any) -> str:
    value = normalize(value)
    value = re.split(
        r"\s+(?:feat\.?|ft\.?|featuring)\s+",
        value,
        maxsplit=1,
        flags=re.IGNORECASE,
    )[0]
    return value.strip()


def artist_parts(value: Any) -> set[str]:
    full = normalize(value)
    primary = primary_artist(value)
    parts = {
        p.strip()
        for p in re.split(
            r"\s+(?:feat\.?|ft\.?|featuring|x)\s+|\s*[,&;/+]\s*",
            full,
            flags=re.IGNORECASE,
        )
        if p.strip()
    }
    if full:
        parts.add(full)
    if primary:
        parts.add(primary)
    return parts


def artists_overlap(left: Any, right: Any) -> bool:
    left_parts = artist_parts(left)
    right_parts = artist_parts(right)
    return bool(left_parts and right_parts and left_parts.intersection(right_parts))


def similarity(left: Any, right: Any) -> float:
    return SequenceMatcher(None, normalize(left), normalize(right)).ratio()


def valid_uuid(value: Any) -> bool:
    try:
        uuid.UUID(text(value).strip())
    except (ValueError, TypeError, AttributeError):
        return False
    return True


def is_various(value: Any) -> bool:
    return normalize(value) in VARIOUS_ARTISTS


def title_markers(value: Any) -> set[str]:
    value = normalize(value)
    return {marker for marker in VERSION_MARKERS if marker in value}


def safe_component(value: Any, fallback: str = "Unbekannt") -> str:
    value = text(value).strip() or fallback
    value = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", value)
    value = re.sub(r"\s+", " ", value).strip(" .")
    return value[:120] or fallback


def duration_close(left: float, right: float, absolute: float) -> bool:
    if not left or not right:
        return True
    allowed = max(absolute, max(left, right) * 0.02)
    return abs(left - right) <= allowed


def item_value(item: Any, key: str, default: Any = "") -> Any:
    try:
        return item.get(key, default, with_album=False)
    except TypeError:
        return item.get(key, default)
    except Exception:
        return default


@dataclass
class TrackSummary:
    item_id: int
    path: str
    title: str
    artist: str
    album: str
    albumartist: str
    track: int
    tracktotal: int
    disc: int
    disctotal: int
    length: float
    mb_trackid: str


@dataclass
class AlbumSummary:
    album: Any
    album_id: int
    title: str
    albumartist: str
    year: int
    albumtype: str
    albumtypes: str
    comp: bool
    mb_albumid: str
    mb_releasegroupid: str
    track_artists: set[str] = field(default_factory=set)
    tracktotals: set[int] = field(default_factory=set)
    tracks: list[TrackSummary] = field(default_factory=list)

    @property
    def title_key(self) -> str:
        return normalize(self.title)

    @property
    def artist_key(self) -> str:
        return normalize(self.albumartist)


@dataclass
class IncomingTrack:
    path: Path
    item: Any

    @property
    def title(self) -> str:
        return text(item_value(self.item, "title")) or self.path.stem

    @property
    def artist(self) -> str:
        return text(item_value(self.item, "artist"))

    @property
    def album(self) -> str:
        return text(item_value(self.item, "album"))

    @property
    def albumartist(self) -> str:
        return text(item_value(self.item, "albumartist"))

    @property
    def year(self) -> int:
        return int(item_value(self.item, "year", 0) or 0)

    @property
    def track(self) -> int:
        return int(item_value(self.item, "track", 0) or 0)

    @property
    def tracktotal(self) -> int:
        return int(item_value(self.item, "tracktotal", 0) or 0)

    @property
    def disc(self) -> int:
        return int(item_value(self.item, "disc", 0) or 0)

    @property
    def length(self) -> float:
        return float(item_value(self.item, "length", 0.0) or 0.0)

    @property
    def mb_albumid(self) -> str:
        return text(item_value(self.item, "mb_albumid"))

    @property
    def mb_releasegroupid(self) -> str:
        return text(item_value(self.item, "mb_releasegroupid"))

    @property
    def mb_trackid(self) -> str:
        return text(item_value(self.item, "mb_trackid"))

    @property
    def isrc(self) -> str:
        return text(item_value(self.item, "isrc"))


@dataclass
class TargetTrack:
    release_id: str = ""
    title: str = ""
    artist: str = ""
    track: int = 0
    tracktotal: int = 0
    disc: int = 0
    disctotal: int = 0
    mb_trackid: str = ""
    mb_releasetrackid: str = ""
    resolution: str = ""


@dataclass
class MatchResult:
    album: AlbumSummary | None
    score: float
    margin: float
    reason: str
    ambiguous: bool = False
    target_track: TargetTrack | None = None


class LibraryIndex:
    def __init__(self, lib: Any):
        self.albums: list[AlbumSummary] = []
        self.by_title: dict[str, list[AlbumSummary]] = defaultdict(list)
        self.by_mbid: dict[str, AlbumSummary] = {}
        self.by_releasegroup: dict[str, list[AlbumSummary]] = defaultdict(list)
        self.by_artist: dict[str, list[AlbumSummary]] = defaultdict(list)

        for album in lib.albums():
            tracks: list[TrackSummary] = []
            track_artists: set[str] = set()
            tracktotals: set[int] = set()

            for item in album.items():
                artist = text(item_value(item, "artist"))
                track_artists.update(artist_parts(artist))
                tracktotal = int(item_value(item, "tracktotal", 0) or 0)
                if tracktotal:
                    tracktotals.add(tracktotal)
                tracks.append(
                    TrackSummary(
                        item_id=int(item.id),
                        path=os.fsdecode(item.path) if item.path else "",
                        title=text(item_value(item, "title")),
                        artist=artist,
                        album=text(item_value(item, "album")),
                        albumartist=text(item_value(item, "albumartist")),
                        track=int(item_value(item, "track", 0) or 0),
                        tracktotal=tracktotal,
                        disc=int(item_value(item, "disc", 0) or 0),
                        disctotal=int(item_value(item, "disctotal", 0) or 0),
                        length=float(item_value(item, "length", 0.0) or 0.0),
                        mb_trackid=text(item_value(item, "mb_trackid")),
                    )
                )

            summary = AlbumSummary(
                album=album,
                album_id=int(album.id),
                title=text(album.get("album", "")),
                albumartist=text(album.get("albumartist", "")),
                year=int(album.get("year", 0) or 0),
                albumtype=normalize(album.get("albumtype", "")),
                albumtypes=normalize(album.get("albumtypes", "")),
                comp=bool(album.get("comp", False)),
                mb_albumid=text(album.get("mb_albumid", "")),
                mb_releasegroupid=text(album.get("mb_releasegroupid", "")),
                track_artists=track_artists,
                tracktotals=tracktotals,
                tracks=tracks,
            )
            self.albums.append(summary)
            self.by_title[summary.title_key].append(summary)

            if valid_uuid(summary.mb_albumid):
                self.by_mbid[normalize(summary.mb_albumid)] = summary
            if valid_uuid(summary.mb_releasegroupid):
                self.by_releasegroup[normalize(summary.mb_releasegroupid)].append(
                    summary
                )

            for artist_key in artist_parts(summary.albumartist).union(track_artists):
                self.by_artist[artist_key].append(summary)

        self.title_counts = Counter(
            summary.title_key for summary in self.albums if summary.title_key
        )

    def albums_for_artist(self, artist: str) -> list[AlbumSummary]:
        found: dict[int, AlbumSummary] = {}
        for part in artist_parts(artist):
            for album in self.by_artist.get(part, []):
                found[album.album_id] = album
        return list(found.values())


class AppriseNotifier:
    """Optional notification adapter.

    Notification failures are deliberately isolated from import semantics.
    Apprise is lazy-loaded only when notifications are enabled, and the user
    keeps service URLs in a separate Apprise configuration file.
    """

    def __init__(self, plugin: "SmartImportPlugin"):
        self.plugin = plugin
        self._apprise = None

    def enabled(self) -> bool:
        try:
            return self.plugin.config["notifications"]["enabled"].get(bool)
        except Exception:
            return False

    def _load(self, strict: bool = False):
        if not self.enabled():
            return None
        if self._apprise is not None:
            return self._apprise

        config_path = self.plugin.config["notifications"]["apprise_config"].as_str().strip()
        if not config_path:
            message = (
                "smartimport.notifications.enabled is true but "
                "smartimport.notifications.apprise_config is not set"
            )
            if strict:
                raise UserError(message)
            self.plugin._log.warning(message)
            return None

        path = Path(config_path).expanduser()
        if not path.is_file():
            message = f"Apprise configuration file not found: {path}"
            if strict:
                raise UserError(message)
            self.plugin._log.warning(message)
            return None

        try:
            import apprise
        except ImportError as exc:
            message = (
                "Apprise notifications are enabled but Apprise is not installed. "
                "Install smartimport with the notifications extra: "
                "pip install 'beets-smartimport[notifications]'"
            )
            if strict:
                raise UserError(message) from exc
            self.plugin._log.warning(message)
            return None

        app = apprise.Apprise()
        cfg = apprise.AppriseConfig()
        if not cfg.add(str(path)):
            message = f"Apprise could not load configuration: {path}"
            if strict:
                raise UserError(message)
            self.plugin._log.warning(message)
            return None
        app.add(cfg)
        if len(app) == 0:
            message = f"Apprise configuration contains no usable notification services: {path}"
            if strict:
                raise UserError(message)
            self.plugin._log.warning(message)
            return None

        self._apprise = app
        return app

    def send(self, title: str, body: str, kind: str = "info", strict: bool = False) -> bool:
        app = self._load(strict=strict)
        if app is None:
            return False
        try:
            from apprise import NotifyType

            notify_type = {
                "info": NotifyType.INFO,
                "success": NotifyType.SUCCESS,
                "warning": NotifyType.WARNING,
                "failure": NotifyType.FAILURE,
            }.get(kind, NotifyType.INFO)
            tag = self.plugin.config["notifications"]["tag"].as_str().strip()
            result = app.notify(
                title=title,
                body=body,
                tag=tag or None,
                notify_type=notify_type,
            )
        except Exception as exc:
            if strict:
                raise UserError(f"Apprise notification failed: {exc}") from exc
            self.plugin._log.warning("Apprise notification failed: {}", exc)
            return False
        return bool(result)


class SmartImportPlugin(BeetsPlugin):
    def __init__(self):
        super().__init__()
        self._release_cache: dict[str, dict[str, Any]] = {}
        self.config.add(
            {
                "incoming": "",
                "staging": "",
                "manual": "",
                "duplicates": "",
                "failed": "",
                "manual_import_config": "",
                "extensions": list(DEFAULT_AUDIO_EXTENSIONS),
                "min_age_minutes": 2,
                "local_score_threshold": 75,
                "local_score_margin": 15,
                "duration_tolerance_seconds": 5,
                "musicbrainz_fallback": True,
                "prefer_existing_album": True,
                "replace_missing_items": False,
                "sync_artwork": True,
                "sync_animated_artwork": False,
                "notifications": {
                    "enabled": False,
                    "apprise_config": "",
                    "tag": "",
                    "notify_on_success": False,
                    "notify_on_attention": True,
                    "notify_on_failure": True,
                    "notify_on_noop": False,
                    "notify_on_dry_run": False,
                },
            }
        )
        self.notifier = AppriseNotifier(self)

    def commands(self):
        smartimport = Subcommand(
            "smartimport",
            help="attach new files to existing albums or stage new releases",
        )
        smartimport.parser.add_option(
            "--dry-run",
            action="store_true",
            default=False,
            help="show decisions only; do not modify files or the library",
        )
        smartimport.parser.add_option(
            "--no-musicbrainz",
            action="store_true",
            default=False,
            help="disable the MusicBrainz fallback for single/album edge cases",
        )
        smartimport.func = self.smartimport

        cleanup = Subcommand(
            "smartcleanup",
            help="move files left after a normal Beets import to manual review",
        )
        cleanup.parser.add_option(
            "--dry-run", action="store_true", default=False
        )
        cleanup.func = self.smartcleanup

        repair = Subcommand(
            "smartrepair",
            help="audit or repair album identity fields between albums and tracks",
        )
        repair.parser.add_option(
            "--apply",
            action="store_true",
            default=False,
            help="write repairs to the database and file tags",
        )
        repair.parser.add_option(
            "--no-write",
            action="store_true",
            default=False,
            help="with --apply, update only the Beets database and not file tags",
        )
        repair.parser.add_option(
            "--include-fallback-fields",
            action="store_true",
            default=False,
            help=(
                "also align album title, album artist and release date; "
                "only allowed together with --album-id"
            ),
        )
        repair.parser.add_option(
            "--album-id",
            action="append",
            type="int",
            dest="album_ids",
            default=[],
            help="limit repair to a Beets album ID; may be specified multiple times",
        )
        repair.func = self.smartrepair

        notify_test = Subcommand(
            "smartnotifytest",
            help="send a test notification through the configured Apprise services",
        )
        notify_test.func = self.smartnotifytest

        return [smartimport, cleanup, repair, notify_test]

    def _path(self, key: str) -> Path:
        raw = self.config[key].as_str().strip()
        if not raw:
            raise UserError(f"smartimport.{key} is required")
        path = Path(raw).expanduser()
        if not path.is_absolute():
            raise UserError(f"smartimport.{key} must be an absolute path: {raw}")
        if path == Path(path.anchor):
            raise UserError(f"smartimport.{key} may not point to a filesystem root: {path}")
        return path

    def _route_name(self, key: str) -> str:
        try:
            return PUBLIC_ROUTE_NAMES[key]
        except KeyError as exc:
            raise UserError(f"unknown smartimport route: {key}") from exc

    def _audio_extensions(self) -> frozenset[str]:
        try:
            raw_values = self.config["extensions"].as_str_seq()
        except Exception as exc:
            raise UserError("smartimport.extensions must be a list of file extensions") from exc
        values = set()
        for raw in raw_values:
            value = text(raw).strip().casefold()
            if not value:
                continue
            if not value.startswith("."):
                value = f".{value}"
            if not re.fullmatch(r"\.[a-z0-9][a-z0-9+_-]*", value):
                raise UserError(f"invalid smartimport audio extension: {raw!r}")
            values.add(value)
        if not values:
            raise UserError("smartimport.extensions may not be empty")
        return frozenset(values)

    def _validate_paths(self) -> dict[str, Path]:
        keys = ("incoming", "staging", "manual", "duplicates", "failed")
        paths = {key: self._path(key) for key in keys}
        normalized = {key: path.resolve(strict=False) for key, path in paths.items()}
        reverse: dict[Path, list[str]] = defaultdict(list)
        for key, path in normalized.items():
            reverse[path].append(key)
        duplicates = [names for names in reverse.values() if len(names) > 1]
        if duplicates:
            joined = "; ".join(", ".join(names) for names in duplicates)
            raise UserError(f"smartimport paths must be distinct; duplicates: {joined}")
        return paths

    def _ensure_directories(self) -> None:
        paths = self._validate_paths()
        self._audio_extensions()
        for path in paths.values():
            path.mkdir(parents=True, exist_ok=True)

    def _ready_files(self) -> list[Path]:
        root = self._path("incoming")
        extensions = self._audio_extensions()
        min_age = float(self.config["min_age_minutes"].get()) * 60.0
        now = time.time()
        files = []
        for path in root.rglob("*"):
            if not path.is_file() or path.suffix.casefold() not in extensions:
                continue
            try:
                age = now - path.stat().st_mtime
            except OSError:
                continue
            if age >= min_age:
                files.append(path)
        return sorted(files)

    def _read_incoming(self, path: Path, dry_run: bool = False) -> IncomingTrack | None:
        try:
            item = Item.from_path(str(path))
            return IncomingTrack(path=path, item=item)
        except Exception as error:
            print(f"UNREADABLE: {path}: {error}")
            if not dry_run:
                self._move_file(path, self._path("failed") / self._route_name("unreadable"))
            return None

    def _score_album(self, track: IncomingTrack, album: AlbumSummary) -> tuple[float, list[str]]:
        score = 0.0
        reasons: list[str] = []

        incoming_title = normalize(track.album)
        incoming_albumartist = normalize(track.albumartist)
        incoming_artist = normalize(track.artist)

        if valid_uuid(track.mb_albumid) and valid_uuid(album.mb_albumid):
            if normalize(track.mb_albumid) == normalize(album.mb_albumid):
                score += 180
                reasons.append("identical MusicBrainz album ID")
            else:
                score -= 200
                reasons.append("conflicting MusicBrainz album ID")

        if valid_uuid(track.mb_releasegroupid) and valid_uuid(album.mb_releasegroupid):
            if (
                normalize(track.mb_releasegroupid)
                == normalize(album.mb_releasegroupid)
            ):
                score += 140
                reasons.append("identical MusicBrainz release group")
            else:
                score -= 100
                reasons.append("conflicting MusicBrainz release group")

        title_exact = bool(incoming_title and incoming_title == album.title_key)
        if title_exact:
            score += 65
            reasons.append("identical album title")
            if self._current_index.title_counts[incoming_title] == 1:
                score += 20
                reasons.append("album title is unique in the library")
        elif incoming_title and similarity(incoming_title, album.title_key) >= 0.96:
            score += 40
            reasons.append("album title is nearly identical")

        candidate_is_various = is_various(album.albumartist) or album.comp
        incoming_aa_reliable = bool(incoming_albumartist) and not is_various(
            incoming_albumartist
        )

        if incoming_aa_reliable and incoming_albumartist == album.artist_key:
            score += 45
            reasons.append("identical album artist")

        if artists_overlap(track.artist, album.albumartist):
            score += 35
            reasons.append("track artist matches the album artist")
        elif any(part in album.track_artists for part in artist_parts(track.artist)):
            score += 25
            reasons.append("track artist occurs on the existing album")
        elif title_exact and not candidate_is_various:
            # An exact but generic album title must never outweigh a clear
            # artist conflict. Otherwise unrelated albums such as two releases
            # both named "Touch Me" can be selected.
            score -= 80
            reasons.append("artist metadata conflicts strongly")

        if track.year and album.year and track.year == album.year:
            score += 10
            reasons.append("identical release year")

        if track.tracktotal and track.tracktotal in album.tracktotals:
            score += 8
            reasons.append("identical total track count")

        # Existing title/track evidence is useful, primarily for duplicate
        # detection and for compilations with unreliable album artists.
        for existing in album.tracks:
            if normalize(existing.title) == normalize(track.title):
                score += 12
                reasons.append("track title already exists on the album")
                break

        return score, reasons

    def _candidate_pool(self, track: IncomingTrack) -> list[AlbumSummary]:
        pool: dict[int, AlbumSummary] = {}

        if valid_uuid(track.mb_albumid):
            match = self._current_index.by_mbid.get(normalize(track.mb_albumid))
            if match:
                pool[match.album_id] = match

        if valid_uuid(track.mb_releasegroupid):
            for album in self._current_index.by_releasegroup.get(
                normalize(track.mb_releasegroupid), []
            ):
                pool[album.album_id] = album

        album_key = normalize(track.album)
        if album_key:
            for album in self._current_index.by_title.get(album_key, []):
                pool[album.album_id] = album

        # Near-identical album titles are only considered when artist evidence
        # also exists. This covers punctuation/case variants without allowing a
        # broad fuzzy search across generic titles.
        for album in self._current_index.albums_for_artist(track.artist):
            if album_key and similarity(album_key, album.title_key) >= 0.96:
                pool[album.album_id] = album

        return list(pool.values())

    def _local_match(self, track: IncomingTrack) -> MatchResult:
        candidates = []
        for album in self._candidate_pool(track):
            score, reasons = self._score_album(track, album)
            candidates.append((score, album, reasons))

        if not candidates:
            return MatchResult(None, 0, 0, "no local album candidate")

        candidates.sort(key=lambda row: row[0], reverse=True)
        best_score, best_album, reasons = candidates[0]
        second_score = candidates[1][0] if len(candidates) > 1 else 0
        margin = best_score - second_score
        threshold = float(self.config["local_score_threshold"].get())
        required_margin = float(self.config["local_score_margin"].get())

        if best_score < threshold:
            return MatchResult(
                None,
                best_score,
                margin,
                f"local candidate below threshold: {best_album.albumartist} - {best_album.title}",
            )

        if len(candidates) > 1 and margin < required_margin:
            names = ", ".join(
                f"{album.albumartist} - {album.title} ({score:.0f})"
                for score, album, _ in candidates[:3]
            )
            return MatchResult(
                None,
                best_score,
                margin,
                f"multiple similarly strong local candidates: {names}",
                ambiguous=True,
            )

        return MatchResult(
            best_album,
            best_score,
            margin,
            "; ".join(reasons),
        )

    def _recording_matches_track(self, track: IncomingTrack, recording: dict[str, Any]) -> bool:
        if similarity(track.title, recording.get("title", "")) < 0.88:
            return False

        incoming_markers = title_markers(track.title)
        recording_markers = title_markers(recording.get("title", ""))
        if incoming_markers != recording_markers:
            return False

        credit = recording.get("artist-credit-phrase", "")
        credit_names = [
            text(part.get("artist", {}).get("name", ""))
            for part in recording.get("artist-credit", [])
            if isinstance(part, dict) and part.get("artist")
        ]
        if credit:
            artist_matches = artists_overlap(track.artist, credit)
        elif credit_names:
            artist_matches = any(
                artists_overlap(track.artist, name) for name in credit_names
            )
        else:
            artist_matches = True
        if not artist_matches:
            return False

        length_ms = recording.get("length")
        if length_ms:
            try:
                recording_length = float(length_ms) / 1000.0
            except (TypeError, ValueError):
                recording_length = 0.0
            tolerance = float(
                self.config["duration_tolerance_seconds"].get()
            )
            if not duration_close(track.length, recording_length, tolerance):
                return False

        return True

    def _musicbrainz_client(self):
        try:
            import musicbrainzngs as mb
        except ImportError:
            self._log.warning(
                "MusicBrainz fallback is unavailable because musicbrainzngs is not installed"
            )
            return None
        mb.set_useragent("beets-smartimport", __version__, REPOSITORY_URL)
        if hasattr(mb, "set_rate_limit"):
            mb.set_rate_limit(1.0)
        return mb

    def _musicbrainz_recordings(self, track: IncomingTrack) -> list[dict[str, Any]]:
        mb = self._musicbrainz_client()
        if mb is None:
            return []

        try:

            if valid_uuid(track.mb_trackid):
                result = mb.get_recording_by_id(
                    track.mb_trackid,
                    includes=["artists", "releases"],
                )
                return [result.get("recording", {})]

            if track.isrc:
                result = mb.get_recordings_by_isrc(
                    track.isrc,
                    includes=["artists", "releases"],
                )
                return result.get("isrc", {}).get("recording-list", [])

            result = mb.search_recordings(
                recording=track.title,
                artist=primary_artist(track.artist),
                limit=10,
            )
            return result.get("recording-list", [])
        except Exception as error:
            self._log.warning("MusicBrainz query failed: {}", error)
            return []

    def _release_list(self, recording: dict[str, Any]) -> list[dict[str, Any]]:
        recording_id = recording.get("id")
        if not valid_uuid(recording_id):
            return recording.get("release-list", []) or []

        mb = self._musicbrainz_client()
        if mb is None:
            return recording.get("release-list", []) or []

        try:
            result = mb.browse_releases(
                recording=recording_id,
                includes=["release-groups", "artist-credits"],
                limit=100,
            )
            releases = result.get("release-list", []) or []
            return releases or recording.get("release-list", []) or []
        except Exception as error:
            self._log.warning("MusicBrainz release query failed: {}", error)
            return recording.get("release-list", []) or []

    def _release_details(self, release_id: str) -> dict[str, Any]:
        release_id = normalize(release_id)
        if not valid_uuid(release_id):
            return {}
        if release_id in self._release_cache:
            return self._release_cache[release_id]

        mb = self._musicbrainz_client()
        if mb is None:
            self._release_cache[release_id] = {}
            return {}

        try:
            result = mb.get_release_by_id(
                release_id,
                includes=[
                    "recordings",
                    "artist-credits",
                    "release-groups",
                ],
            )
            release = result.get("release", {}) or {}
        except Exception as error:
            self._log.warning("MusicBrainz release {} could not be loaded: {}", release_id, error)
            release = {}

        self._release_cache[release_id] = release
        return release

    def _target_track_on_release(
        self,
        release: dict[str, Any],
        recording_id: str,
    ) -> TargetTrack | None:
        media = release.get("medium-list", []) or []
        disctotal = int(release.get("medium-count", 0) or len(media) or 0)

        for disc_index, medium in enumerate(media, start=1):
            tracks = medium.get("track-list", []) or []
            tracktotal = int(medium.get("track-count", 0) or len(tracks) or 0)
            disc = int(medium.get("position", 0) or disc_index)

            for position, release_track in enumerate(tracks, start=1):
                recording = release_track.get("recording", {}) or {}
                if normalize(recording.get("id", "")) != normalize(recording_id):
                    continue

                artist = release_track.get("artist-credit-phrase", "")
                if not artist:
                    artist = recording.get("artist-credit-phrase", "")
                if not artist:
                    artist = " & ".join(
                        text(part.get("artist", {}).get("name", ""))
                        for part in recording.get("artist-credit", [])
                        if isinstance(part, dict)
                    )

                try:
                    track_number = int(release_track.get("position", 0) or position)
                except (TypeError, ValueError):
                    track_number = position

                return TargetTrack(
                    release_id=text(release.get("id")),
                    title=text(release_track.get("title") or recording.get("title")),
                    artist=text(artist),
                    track=track_number,
                    tracktotal=tracktotal,
                    disc=disc,
                    disctotal=disctotal,
                    mb_trackid=text(recording.get("id")),
                    mb_releasetrackid=text(release_track.get("id")),
                )

        return None

    def _musicbrainz_existing_match(self, track: IncomingTrack) -> MatchResult:
        artist_albums = self._current_index.albums_for_artist(track.artist)
        if not artist_albums:
            return MatchResult(None, 0, 0, "no existing albums for the artist")

        albums_by_title: dict[str, list[AlbumSummary]] = defaultdict(list)
        albums_by_mbid: dict[str, AlbumSummary] = {}
        for album in artist_albums:
            albums_by_title[album.title_key].append(album)
            if valid_uuid(album.mb_albumid):
                albums_by_mbid[normalize(album.mb_albumid)] = album

        scored: dict[int, tuple[float, AlbumSummary, list[str], TargetTrack]] = {}

        for recording in self._musicbrainz_recordings(track):
            if not self._recording_matches_track(track, recording):
                continue

            recording_id = text(recording.get("id"))
            if not valid_uuid(recording_id):
                continue

            for release_summary in self._release_list(recording):
                release_id = normalize(release_summary.get("id", ""))
                release_title = normalize(release_summary.get("title", ""))
                if not valid_uuid(release_id):
                    continue

                candidate_albums: dict[int, AlbumSummary] = {}
                exact = albums_by_mbid.get(release_id)
                if exact:
                    candidate_albums[exact.album_id] = exact
                for album in albums_by_title.get(release_title, []):
                    candidate_albums[album.album_id] = album

                if not candidate_albums:
                    continue

                release = self._release_details(release_id)
                if not release:
                    continue
                target_track = self._target_track_on_release(release, recording_id)
                if target_track is None:
                    continue

                release_group = release.get("release-group", {}) or {}
                primary_type = normalize(release_group.get("primary-type", ""))
                secondary_types = {
                    normalize(value)
                    for value in release_group.get("secondary-type-list", [])
                }
                release_year = 0
                date = text(release.get("date", ""))
                if len(date) >= 4 and date[:4].isdigit():
                    release_year = int(date[:4])

                for album in candidate_albums.values():
                    score = 0.0
                    reasons: list[str] = []

                    if release_id == normalize(album.mb_albumid):
                        score += 180
                        reasons.append("existing MusicBrainz release ID contains the recording")

                    if release_title == album.title_key:
                        score += 100
                        reasons.append("MusicBrainz confirms the recording on this album")
                    else:
                        continue

                    if artists_overlap(track.artist, album.albumartist) or any(
                        part in album.track_artists for part in artist_parts(track.artist)
                    ):
                        score += 25
                        reasons.append("artist matches the existing album")

                    if normalize(track.album) == album.title_key:
                        score += 25
                        reasons.append("download album title also matches")

                    if track.tracktotal and target_track.tracktotal == track.tracktotal:
                        score += 8
                        reasons.append("download tag track count matches")

                    if album.tracktotals and target_track.tracktotal in album.tracktotals:
                        score += 12
                        reasons.append("track count matches the local release")

                    if album.year and release_year and album.year == release_year:
                        score += 12
                        reasons.append("release year matches")

                    prefer_album = bool(self.config["prefer_existing_album"].get())
                    local_type = album.albumtype or primary_type
                    if prefer_album and local_type == "album":
                        score += 25
                        reasons.append("existing full album is preferred")
                    elif local_type == "ep":
                        score += 8

                    if "compilation" in secondary_types or "soundtrack" in secondary_types:
                        score -= 5

                    old = scored.get(album.album_id)
                    if old is None or score > old[0]:
                        scored[album.album_id] = (
                            score, album, reasons, target_track
                        )

        if not scored:
            return MatchResult(
                None,
                0,
                0,
                "MusicBrainz does not confirm an existing album",
            )

        candidates = sorted(scored.values(), key=lambda row: row[0], reverse=True)
        if bool(self.config["prefer_existing_album"].get()):
            regular_albums = [
                row for row in candidates if row[1].albumtype == "album"
            ]
            if regular_albums:
                candidates = regular_albums

        best_score, best_album, reasons, target_track = candidates[0]
        second_score = candidates[1][0] if len(candidates) > 1 else 0
        margin = best_score - second_score

        if best_score < 100 or (len(candidates) > 1 and margin < 20):
            names = ", ".join(
                f"{album.albumartist} - {album.title} ({score:.0f})"
                for score, album, _, _ in candidates[:3]
            )
            return MatchResult(
                None,
                best_score,
                margin,
                f"MusicBrainz result is ambiguous: {names}",
                ambiguous=True,
            )

        return MatchResult(
            best_album,
            best_score,
            margin,
            "; ".join(reasons),
            target_track=target_track,
        )

    def _duplicate_reason(
        self, track: IncomingTrack, existing: TrackSummary, album: AlbumSummary
    ) -> str:
        """Return the duplicate reason for one existing library item.

        This method only compares metadata. Whether the existing Beets item still
        points to a real file is handled separately so that a deleted or missing
        file can be replaced instead of blocking the new import.
        """
        tolerance = float(self.config["duration_tolerance_seconds"].get())
        both_recording_ids = (
            valid_uuid(track.mb_trackid)
            and valid_uuid(existing.mb_trackid)
        )
        if (
            both_recording_ids
            and normalize(track.mb_trackid) == normalize(existing.mb_trackid)
        ):
            return "identical MusicBrainz recording ID"
        if (
            both_recording_ids
            and normalize(track.mb_trackid) != normalize(existing.mb_trackid)
        ):
            return ""

        if normalize(track.title) == normalize(existing.title):
            artist_ok = artists_overlap(track.artist, existing.artist) or is_various(
                album.albumartist
            )
            if artist_ok and duration_close(
                track.length, existing.length, tolerance
            ):
                return "title, artist and duration already exist"

        if (
            track.track
            and existing.track == track.track
            and (not track.disc or not existing.disc or track.disc == existing.disc)
            and similarity(track.title, existing.title) >= 0.90
            and title_markers(track.title) == title_markers(existing.title)
            and (
                artists_overlap(track.artist, existing.artist)
                or is_various(album.albumartist)
            )
        ):
            return "track position, title and artist already exist"

        return ""

    def _is_duplicate(
        self, track: IncomingTrack, album: AlbumSummary
    ) -> tuple[bool, str, list[int]]:
        """Distinguish real duplicates from stale Beets database entries.

        A metadata match is only a duplicate while the file referenced by the
        existing Beets item is still present. Missing files are returned as stale
        item IDs. Whether they may be replaced is controlled by
        ``replace_missing_items``.
        """
        stale_item_ids: list[int] = []
        stale_details: list[str] = []

        for existing in album.tracks:
            reason = self._duplicate_reason(track, existing, album)
            if not reason:
                continue

            try:
                file_exists = bool(existing.path) and Path(existing.path).is_file()
            except OSError:
                file_exists = False

            if file_exists:
                return (
                    True,
                    f"{reason}; existing item ID {existing.item_id}, "
                    f"file: {existing.path}",
                    [],
                )

            stale_item_ids.append(existing.item_id)
            stale_details.append(
                f"item ID {existing.item_id}, missing file: "
                f"{existing.path or '<no path>'}"
            )

        if stale_item_ids:
            return (
                False,
                "matching Beets record points to a missing file: "
                + "; ".join(stale_details),
                stale_item_ids,
            )

        return False, "", []

    def _confirm_stale_items_missing(
        self, lib: Any, item_ids: Iterable[int]
    ) -> tuple[bool, list[int]]:
        """Recheck missing files immediately before a replacement transaction."""
        reappeared: list[int] = []
        for item_id in dict.fromkeys(int(value) for value in item_ids):
            existing = lib.get_item(item_id)
            if existing is None:
                continue
            existing_path = os.fsdecode(existing.path) if existing.path else ""
            try:
                if existing_path and Path(existing_path).is_file():
                    reappeared.append(item_id)
            except OSError:
                continue
        return not reappeared, reappeared

    def _remove_stale_items(self, lib: Any, item_ids: Iterable[int]) -> list[int]:
        """Remove confirmed missing-file records without deleting any file."""
        removed: list[int] = []
        for item_id in dict.fromkeys(int(value) for value in item_ids):
            existing = lib.get_item(item_id)
            if existing is None:
                continue

            existing_path = os.fsdecode(existing.path) if existing.path else ""
            try:
                if existing_path and Path(existing_path).is_file():
                    # The file reappeared between duplicate detection and attach.
                    continue
            except OSError:
                pass

            existing.remove(delete=False, with_album=False)
            removed.append(item_id)

        return removed

    def _canonicalize_item(self, item: Any, album: Any) -> None:
        incoming_album_mbid = text(item_value(item, "mb_albumid"))
        canonical_album_mbid = text(album.get("mb_albumid", ""))

        for key in IDENTITY_FIELDS:
            try:
                value = album.get(key, "")
                item[key] = value
            except Exception:
                # Older Beets versions may not expose every newer field.
                continue

        # A release-track ID belongs to a concrete MusicBrainz release. It must
        # not survive when the local album deliberately uses another release ID
        # or has no MusicBrainz release ID at all.
        if normalize(incoming_album_mbid) != normalize(canonical_album_mbid):
            try:
                item["mb_releasetrackid"] = ""
            except Exception:
                pass


    def _recording_identity_matches_track(
        self, track: IncomingTrack, recording: dict[str, Any]
    ) -> bool:
        """Match title/version and artist without rejecting duration variants.

        The normal resolver remains strict and still checks the duration. This
        secondary identity check is only used on the exact MusicBrainz release
        already assigned to the local Beets album. It covers masters whose file
        duration differs from MusicBrainz while keeping title/version and artist
        checks conservative.
        """
        if similarity(track.title, recording.get("title", "")) < 0.96:
            return False

        if title_markers(track.title) != title_markers(recording.get("title", "")):
            return False

        credit = recording.get("artist-credit-phrase", "")
        credit_names = [
            text(part.get("artist", {}).get("name", ""))
            for part in recording.get("artist-credit", [])
            if isinstance(part, dict) and part.get("artist")
        ]

        if credit:
            return artists_overlap(track.artist, credit)
        if credit_names:
            return any(artists_overlap(track.artist, name) for name in credit_names)
        return True

    def _target_from_release_track(
        self,
        release: dict[str, Any],
        medium: dict[str, Any],
        release_track: dict[str, Any],
        disc_index: int,
        position: int,
    ) -> TargetTrack:
        """Build a TargetTrack from one concrete release-track entry."""
        recording = release_track.get("recording", {}) or {}
        media = release.get("medium-list", []) or []
        tracks = medium.get("track-list", []) or []

        try:
            disctotal = int(release.get("medium-count", 0) or len(media) or 0)
        except (TypeError, ValueError):
            disctotal = len(media)

        try:
            tracktotal = int(medium.get("track-count", 0) or len(tracks) or 0)
        except (TypeError, ValueError):
            tracktotal = len(tracks)

        try:
            disc = int(medium.get("position", 0) or disc_index)
        except (TypeError, ValueError):
            disc = disc_index

        try:
            track_number = int(release_track.get("position", 0) or position)
        except (TypeError, ValueError):
            track_number = position

        artist = release_track.get("artist-credit-phrase", "")
        if not artist:
            artist = recording.get("artist-credit-phrase", "")
        if not artist:
            artist = " & ".join(
                text(part.get("artist", {}).get("name", ""))
                for part in recording.get("artist-credit", [])
                if isinstance(part, dict)
            )

        return TargetTrack(
            release_id=text(release.get("id")),
            title=text(release_track.get("title") or recording.get("title")),
            artist=text(artist),
            track=track_number,
            tracktotal=tracktotal,
            disc=disc,
            disctotal=disctotal,
            mb_trackid=text(recording.get("id")),
            mb_releasetrackid=text(release_track.get("id")),
        )

    def _target_track_from_existing_release(
        self, track: IncomingTrack, album: AlbumSummary
    ) -> TargetTrack | None:
        """Resolve the incoming recording on the exact existing MB release.

        Resolution is intentionally layered and conservative:

        1. Keep the existing strict title/artist/version/duration match.
        2. If the source uses album-wide numbering (for example track 22/24 on
           a two-disc release), map that global position to a MusicBrainz medium
           only when the source total exactly equals the release total and the
           mapped title/artist still match.
        3. As a final fallback, accept a unique title/artist/version match on the
           exact release even when the duration differs.

        No position is guessed from the local partial album contents.
        """
        if not valid_uuid(album.mb_albumid):
            return None

        release = self._release_details(album.mb_albumid)
        if not release:
            return None

        release_rows: list[tuple[TargetTrack, dict[str, Any]]] = []
        strict_candidates: list[TargetTrack] = []
        relaxed_candidates: list[TargetTrack] = []
        seen_strict: set[tuple[int, int, str]] = set()
        seen_relaxed: set[tuple[int, int, str]] = set()

        media = release.get("medium-list", []) or []
        release_total = 0

        for disc_index, medium in enumerate(media, start=1):
            tracks = medium.get("track-list", []) or []
            try:
                medium_total = int(medium.get("track-count", 0) or len(tracks) or 0)
            except (TypeError, ValueError):
                medium_total = len(tracks)
            release_total += medium_total

            for position, release_track in enumerate(tracks, start=1):
                recording = dict(release_track.get("recording", {}) or {})
                recording_id = text(recording.get("id"))
                if not valid_uuid(recording_id):
                    continue

                # MusicBrainz may place useful credits or duration on the
                # release-track object instead of the nested recording.
                if not recording.get("artist-credit") and release_track.get("artist-credit"):
                    recording["artist-credit"] = release_track.get("artist-credit")
                if (
                    not recording.get("artist-credit-phrase")
                    and release_track.get("artist-credit-phrase")
                ):
                    recording["artist-credit-phrase"] = release_track.get(
                        "artist-credit-phrase"
                    )
                if not recording.get("title") and release_track.get("title"):
                    recording["title"] = release_track.get("title")
                if not recording.get("length") and release_track.get("length"):
                    recording["length"] = release_track.get("length")

                target = self._target_from_release_track(
                    release, medium, release_track, disc_index, position
                )
                release_rows.append((target, recording))

                key = (target.disc, target.track, normalize(target.mb_trackid))
                if self._recording_matches_track(track, recording) and key not in seen_strict:
                    seen_strict.add(key)
                    strict_candidates.append(target)

                if (
                    self._recording_identity_matches_track(track, recording)
                    and key not in seen_relaxed
                ):
                    seen_relaxed.add(key)
                    relaxed_candidates.append(target)

        # Preserve the previous strict behavior first.
        if track.disc > 0 and track.track > 0:
            positioned = [
                candidate
                for candidate in strict_candidates
                if candidate.disc == track.disc and candidate.track == track.track
            ]
            if len(positioned) == 1:
                positioned[0].resolution = "exakte Disc-/Trackposition"
                return positioned[0]

        if track.track:
            numbered = [
                candidate for candidate in strict_candidates
                if candidate.track == track.track
            ]
            if len(numbered) == 1:
                numbered[0].resolution = "exakte Trackposition"
                return numbered[0]

        if len(strict_candidates) == 1:
            strict_candidates[0].resolution = "strict MusicBrainz match"
            return strict_candidates[0]

        # Some download sources number tracks continuously across all media and
        # omit disc/disctotal. Convert only when the advertised source total is
        # exactly the total number of tracks on the assigned MusicBrainz release.
        if (
            track.disc <= 0
            and track.track > 0
            and track.tracktotal > 0
            and release_total > 0
            and track.tracktotal == release_total
            and track.track <= release_total
        ):
            global_position = 0
            mapped: TargetTrack | None = None
            mapped_recording: dict[str, Any] | None = None

            for target, recording in release_rows:
                global_position += 1
                if global_position == track.track:
                    mapped = target
                    mapped_recording = recording
                    break

            if (
                mapped is not None
                and mapped_recording is not None
                and self._recording_identity_matches_track(track, mapped_recording)
            ):
                mapped.resolution = "globale Tracknummer in Discposition umgerechnet"
                return mapped

        # Final conservative fallback: on the exact assigned release, one unique
        # title/artist/version match is sufficient even if the duration differs.
        if len(relaxed_candidates) == 1:
            relaxed_candidates[0].resolution = (
                "unique title/artist match on the exact release"
            )
            return relaxed_candidates[0]

        return None

    def _apply_safe_disc_fallback(self, item: Any, album: AlbumSummary) -> bool:
        """Fill Disc 1 only when the existing album is clearly single-disc.

        A lone existing Disc 2 is not treated as evidence because it may belong to
        a real multi-disc edition or already be mistagged.
        """
        current_disc = int(item_value(item, "disc", 0) or 0)
        if current_disc > 0:
            return False

        existing_discs = {row.disc for row in album.tracks if row.disc > 0}
        existing_disctotals = {
            row.disctotal for row in album.tracks if row.disctotal > 0
        }

        clearly_single_disc = (
            existing_disctotals == {1}
            or (not existing_disctotals and existing_discs == {1})
        )
        if not clearly_single_disc:
            return False

        try:
            item["disc"] = 1
            item["disctotal"] = 1
            return True
        except Exception:
            return False

    def _apply_target_track(
        self, item: Any, album: AlbumSummary, target: TargetTrack | None
    ) -> None:
        if target is None:
            return

        for key, value in (
            ("title", target.title),
            ("artist", target.artist),
            ("track", target.track),
            ("tracktotal", target.tracktotal),
            ("disc", target.disc),
            ("disctotal", target.disctotal),
            ("mb_trackid", target.mb_trackid),
        ):
            if value not in (None, "", 0):
                try:
                    item[key] = value
                except Exception:
                    pass

        try:
            if (
                valid_uuid(album.mb_albumid)
                and normalize(album.mb_albumid) == normalize(target.release_id)
            ):
                item["mb_releasetrackid"] = target.mb_releasetrackid
            else:
                item["mb_releasetrackid"] = ""
        except Exception:
            pass

    @staticmethod
    def _loaded_plugin(name: str) -> Any | None:
        """Return an already-loaded Beets plugin without instantiating it."""
        try:
            for plugin in beets_plugins.find_plugins():
                if getattr(plugin, "name", "") == name:
                    return plugin
        except Exception:
            pass
        return None

    def _embed_album_artwork_only(self, album: Any, embedart: Any) -> int:
        """Embed only the album image, without sending Beets write events.

        EmbedArt normally writes via Item.try_write(), which sends the global
        ``write`` event and can therefore activate unrelated write-listener
        plugins. smartimport must not do that here: this helper mirrors the
        relevant EmbedArt options but writes only the MediaFile image field.
        """
        if embedart is None:
            return 0

        try:
            from mediafile import MediaFile
            from beets.util import syspath
            from beetsplug._utils import art as beets_art
        except ImportError as exc:
            self._log.warning("Artwork embedding helpers are unavailable: {}", exc)
            return 0

        try:
            if not embedart.config["auto"].get(bool):
                return 0
        except Exception:
            pass

        artpath = getattr(album, "artpath", None)
        if not artpath or not os.path.isfile(os.fsdecode(artpath)):
            return 0

        try:
            maxwidth = embedart.config["maxwidth"].get(int)
        except Exception:
            maxwidth = 0
        try:
            quality = embedart.config["quality"].get(int)
        except Exception:
            quality = 0
        try:
            compare_threshold = embedart.config["compare_threshold"].get(int)
        except Exception:
            compare_threshold = 0
        try:
            ifempty = embedart.config["ifempty"].get(bool)
        except Exception:
            ifempty = False

        imagepath = artpath
        if maxwidth:
            imagepath = beets_art.resize_image(
                embedart._log, imagepath, maxwidth, quality
            )
        image = beets_art.mediafile_image(imagepath, maxwidth)

        embedded = 0
        for album_item in album.items():
            if ifempty and beets_art.get_art(embedart._log, album_item):
                continue
            if compare_threshold:
                similar = beets_art.check_art_similarity(
                    embedart._log,
                    album_item,
                    imagepath,
                    compare_threshold,
                )
                if similar is not True:
                    continue

            # Deliberately bypass Item.write()/Item.try_write(). Those methods
            # send Beets' global write event. MediaFile changes only the
            # embedded image field and leaves smartimport/other plugin logic
            # completely outside this artwork-only post-step.
            media = MediaFile(syspath(album_item.path))
            media.images = [image]
            media.save()
            embedded += 1

        try:
            if embedart.config["remove_art_file"].get(bool):
                embedart.remove_artfile(album)
        except Exception:
            pass

        return embedded

    def _sync_existing_album_artwork(
        self,
        lib: Any,
        album_id: int,
        release_id_hint: str = "",
    ) -> str:
        """Apply only FetchArt plus isolated embedded-art synchronization.

        This intentionally runs after smartimport has already completed its
        existing add/write/move path. Artwork errors are therefore non-fatal
        and must never change the smartimport decision or roll back the item.
        """
        if not self.config["sync_artwork"].get(bool):
            return ""

        db_album = lib.get_album(album_id)
        if db_album is None:
            return "Artwork skipped: Beets album not found"

        embedart = self._loaded_plugin("embedart")
        artpath = getattr(db_album, "artpath", None)
        art_exists = bool(artpath and os.path.isfile(os.fsdecode(artpath)))

        # Existing canonical album art: never search the Web again. Only
        # synchronize that image into the album files, without Beets write
        # events or unrelated plugin callbacks.
        if art_exists:
            embedded = self._embed_album_artwork_only(db_album, embedart)
            if embedded:
                return (
                    "Artwork: existing album cover synchronized in isolation to "
                    f"{embedded} track(s)"
                )
            return "Artwork skipped: embedart unavailable or disabled"

        # A stale/missing artpath must not prevent FetchArt from replacing it.
        # Keep this change in-memory only; if no new art is found, the existing
        # database value is left untouched.
        if artpath:
            db_album.artpath = None

        fetchart = self._loaded_plugin("fetchart")
        art_for_album = getattr(fetchart, "art_for_album", None)
        set_art = getattr(fetchart, "_set_art", None)
        if not callable(art_for_album) or not callable(set_art):
            return "Artwork skipped: fetchart unavailable"

        try:
            if not fetchart.config["auto"].get(bool):
                return "Artwork skipped: fetchart.auto is disabled"
        except Exception:
            pass

        # Old/as-is library albums often have no MusicBrainz release IDs even
        # though smartimport has just resolved the incoming recording against
        # one exact MusicBrainz release. Normal APPLY imports would expose that
        # release identity to FetchArt, allowing Cover Art Archive lookups.
        # Supply the already-resolved release only temporarily for the artwork
        # lookup; restore the album fields before anything can be stored.
        original_mb_albumid = text(getattr(db_album, "mb_albumid", ""))
        original_mb_releasegroupid = text(
            getattr(db_album, "mb_releasegroupid", "")
        )
        artwork_release_id = normalize(release_id_hint)
        artwork_releasegroup_id = ""
        if valid_uuid(artwork_release_id):
            try:
                release = self._release_details(artwork_release_id)
                release_group = release.get("release-group", {}) or {}
                artwork_releasegroup_id = text(release_group.get("id", ""))
            except Exception:
                artwork_releasegroup_id = ""

        if (
            not valid_uuid(original_mb_albumid)
            and valid_uuid(artwork_release_id)
        ):
            db_album.mb_albumid = artwork_release_id
        if (
            not valid_uuid(original_mb_releasegroupid)
            and valid_uuid(artwork_releasegroup_id)
        ):
            db_album.mb_releasegroupid = artwork_releasegroup_id

        # FetchArt's normal setter emits art_set, which would make EmbedArt use
        # Item.try_write() and in turn notify every global write-listener plugin.
        # Disable only EmbedArt's automatic listener while the cover is assigned,
        # then restore it and perform an artwork-only MediaFile image write.
        embedart_auto: bool | None = None
        if embedart is not None:
            try:
                embedart_auto = embedart.config["auto"].get(bool)
                embedart.config["auto"].set(False)
            except Exception:
                embedart_auto = None

        candidate = None
        try:
            candidate = art_for_album(db_album, [db_album.path], False)
        finally:
            # These identity hints are lookup-only. smartimport's existing
            # album identity in the Beets database must remain untouched.
            db_album.mb_albumid = original_mb_albumid
            db_album.mb_releasegroupid = original_mb_releasegroupid

        try:
            if candidate is not None:
                set_art(db_album, candidate)
        finally:
            if embedart is not None and embedart_auto is not None:
                try:
                    embedart.config["auto"].set(embedart_auto)
                except Exception:
                    pass

        refreshed = lib.get_album(album_id)
        refreshed_art = getattr(refreshed, "artpath", None) if refreshed else None
        if not (
            refreshed
            and refreshed_art
            and os.path.isfile(os.fsdecode(refreshed_art))
        ):
            return "Artwork: fetchart found no suitable album cover"

        embedded = self._embed_album_artwork_only(refreshed, embedart)
        if embedded:
            return (
                "Artwork: missing album cover fetched and synchronized in isolation to "
                f"{embedded} track(s)"
            )
        return "Artwork: cover found; embedart unavailable or disabled"

    def _sync_existing_album_animated_artwork(
        self,
        lib: Any,
        album_id: int,
    ) -> str:
        """Run only fetchanimated's isolated existing-album post-step.

        Animated artwork is deliberately independent from cover.jpg,
        album.artpath and embedded static artwork. The helper is called only
        after smartimport's existing add/write/move transaction has completed.
        Any error is cosmetic and must never change smartimport's result.
        """
        if not self.config["sync_animated_artwork"].get(bool):
            return ""

        fetchanimated = self._loaded_plugin("fetchanimated")
        ensure_album_assets = getattr(
            fetchanimated,
            "ensure_album_assets",
            None,
        )
        if not callable(ensure_album_assets):
            return "Animated artwork skipped: fetchanimated unavailable"

        try:
            return str(ensure_album_assets(lib, album_id))
        except Exception as error:
            return f"Animated artwork warning: {error}"

    def _attach(
        self,
        lib: Any,
        track: IncomingTrack,
        album: AlbumSummary,
        target_track: TargetTrack | None = None,
        stale_item_ids: Iterable[int] = (),
    ) -> tuple[bool, str]:
        item = track.item

        # MusicBrainz fallback matches already carry a TargetTrack. Strong local
        # matches do not, so resolve the position on the exact existing release
        # before writing the file. The release cache prevents repeated API calls
        # for several tracks added to the same album in one run.
        resolved_target = target_track or self._target_track_from_existing_release(
            track, album
        )

        self._canonicalize_item(item, album.album)
        self._apply_target_track(item, album, resolved_target)
        used_disc_fallback = False
        if resolved_target is None:
            used_disc_fallback = self._apply_safe_disc_fallback(item, album)
        item.album_id = album.album_id

        try:
            stale_ids = list(dict.fromkeys(int(value) for value in stale_item_ids))
            if stale_ids:
                still_missing, reappeared = self._confirm_stale_items_missing(
                    lib, stale_ids
                )
                if not still_missing:
                    return (
                        False,
                        "replacement aborted because a previously missing file "
                        "reappeared for Beets item ID(s): "
                        + ", ".join(str(value) for value in reappeared),
                    )

            lib.add(item)
            item.write()
            item.move()

            # Only now is the replacement durable enough to remove stale DB
            # rows. If this cleanup fails, the successful import is kept and
            # reported with a warning rather than deleting/moving it again.
            removed_stale_ids = self._remove_stale_items(lib, stale_ids)
            retained_stale_ids = [
                value for value in stale_ids if value not in removed_stale_ids
            ]

            # Artwork is deliberately isolated from the proven smartimport
            # transaction. A FetchArt/EmbedArt problem may be reported, but it
            # can never turn a successful attach into a failed import.
            try:
                artwork_detail = self._sync_existing_album_artwork(
                    lib,
                    album.album_id,
                    resolved_target.release_id if resolved_target else "",
                )
            except Exception as artwork_error:
                artwork_detail = f"Artwork warning: {artwork_error}"

            # Independent companion to the static artwork post-step. It does
            # not depend on cover.jpg/artpath and runs even when FetchArt found
            # no static cover. Its failure is cosmetic only.
            try:
                animated_artwork_detail = (
                    self._sync_existing_album_animated_artwork(
                        lib,
                        album.album_id,
                    )
                )
            except Exception as animated_artwork_error:
                animated_artwork_detail = (
                    f"Animated artwork warning: {animated_artwork_error}"
                )

            detail = f"Item ID {item.id}"
            if artwork_detail:
                detail += f", {artwork_detail}"
            if animated_artwork_detail:
                detail += f", {animated_artwork_detail}"
            if removed_stale_ids:
                detail += (
                    ", replaced stale Beets item ID(s): "
                    + ", ".join(str(value) for value in removed_stale_ids)
                )
            if retained_stale_ids:
                detail += (
                    ", warning: stale Beets item ID(s) could not be removed: "
                    + ", ".join(str(value) for value in retained_stale_ids)
                )
            if resolved_target is not None:
                detail += (
                    f", MusicBrainz position: Disc "
                    f"{resolved_target.disc}/{resolved_target.disctotal}, "
                    f"Track {resolved_target.track}/{resolved_target.tracktotal}"
                )
                if resolved_target.resolution:
                    detail += f" [{resolved_target.resolution}]"
            elif used_disc_fallback:
                detail += ", unambiguous single-disc album: Disc 1/1"
            return True, detail
        except Exception as error:
            try:
                if getattr(item, "id", None) is not None:
                    item.remove(delete=False, with_album=False)
            except Exception:
                pass
            return False, str(error)

    def _move_file(self, source: Path, destination_dir: Path) -> Path:
        destination_dir.mkdir(parents=True, exist_ok=True)
        target = destination_dir / source.name
        counter = 1
        while target.exists():
            target = destination_dir / f"{source.stem}_{counter}{source.suffix}"
            counter += 1
        shutil.move(str(source), str(target))
        self._prune(source.parent)
        return target

    def _prune(self, directory: Path) -> None:
        incoming = self._path("incoming")
        while directory != incoming and incoming in directory.parents:
            try:
                directory.rmdir()
            except OSError:
                break
            directory = directory.parent

    def _group_key(self, track: IncomingTrack) -> tuple[str, str]:
        album = normalize(track.album)
        owner = normalize(track.albumartist)
        if not owner or is_various(owner):
            try:
                relative_parent = track.path.parent.relative_to(
                    self._path("incoming")
                )
            except ValueError:
                relative_parent = Path(".")
            if str(relative_parent) not in {"", "."}:
                owner = f"folder:{normalize(relative_parent)}"
            else:
                owner = "various-or-unknown"
        return album, owner

    def _group_is_coherent(self, tracks: list[IncomingTrack]) -> bool:
        if len(tracks) <= 1:
            return True

        artists = {primary_artist(track.artist) for track in tracks if track.artist}
        if len(artists) <= 1:
            return True

        totals = {track.tracktotal for track in tracks if track.tracktotal}
        positions = [track.track for track in tracks if track.track]
        coherent_numbering = (
            len(totals) == 1
            and len(positions) == len(tracks)
            and len(set(positions)) == len(positions)
        )
        if coherent_numbering:
            return True

        parents = {track.path.parent for track in tracks}
        return len(parents) == 1 and next(iter(parents)) != self._path("incoming")

    def _stage_group(self, tracks: list[IncomingTrack]) -> Path:
        first = tracks[0]
        key = repr(self._group_key(first)).encode("utf-8")
        digest = hashlib.sha1(key).hexdigest()[:8]
        owner = first.albumartist
        if not owner or is_various(owner):
            owner = first.artist or "Various Artists"
        folder = (
            f"{digest} - {safe_component(owner)} - "
            f"{safe_component(first.album, first.title)}"
        )
        destination = self._path("staging") / folder
        destination.mkdir(parents=True, exist_ok=True)
        for track in tracks:
            self._move_file(track.path, destination)
        return destination

    def _prefer_album_check(
        self, track: IncomingTrack, local: MatchResult
    ) -> bool:
        if not bool(self.config["prefer_existing_album"].get()):
            return False
        if local.album is None:
            return True
        release_type = local.album.albumtype
        album_tag = normalize(track.album)
        single_like_tag = (
            album_tag == normalize(track.title)
            or " single" in album_tag
            or album_tag.endswith("- single")
        )
        return release_type in {"single", "ep"} or single_like_tag

    def _notifications_enabled_for_run(self, dry_run: bool) -> bool:
        if not self.notifier.enabled():
            return False
        if dry_run and not self.config["notifications"]["notify_on_dry_run"].get(bool):
            return False
        return True

    def _notify_run_summary(
        self, *, dry_run: bool, ready: int, attached: int, staged: int,
        manual: int, duplicates: int, failed: int, replacements: int
    ) -> None:
        if not self._notifications_enabled_for_run(dry_run):
            return
        if ready == 0:
            if not self.config["notifications"]["notify_on_noop"].get(bool):
                return
            kind = "info"
        elif failed:
            if not self.config["notifications"]["notify_on_failure"].get(bool):
                return
            kind = "failure"
        elif manual or duplicates:
            if not self.config["notifications"]["notify_on_attention"].get(bool):
                return
            kind = "warning"
        else:
            if not self.config["notifications"]["notify_on_success"].get(bool):
                return
            kind = "success"

        mode = "dry-run" if dry_run else "run"
        body = (
            f"smartimport {mode}: ready={ready}, attached={attached}, "
            f"staged={staged}, manual={manual}, duplicates={duplicates}, "
            f"failed={failed}, replacements={replacements}."
        )
        self.notifier.send("Beets smartimport", body, kind)

    def smartnotifytest(self, lib: Any, opts: Any, args: list[str]) -> None:
        if not self.notifier.enabled():
            raise UserError(
                "Apprise notifications are disabled in smartimport.notifications.enabled"
            )
        if not self.notifier.send(
            "Beets smartimport test",
            "Your smartimport notifications are working.",
            "success",
            strict=True,
        ):
            raise UserError("Apprise did not confirm the smartimport test notification")
        print("smartimport Apprise test notification sent successfully.")

    def smartimport(self, lib: Any, opts: Any, args: list[str]) -> None:
        self._ensure_directories()
        files = self._ready_files()
        dry_run = bool(opts.dry_run)
        if not files:
            print(f"No import-ready audio files found under {self._path('incoming')}.")
            self._notify_run_summary(
                dry_run=dry_run, ready=0, attached=0, staged=0, manual=0,
                duplicates=0, failed=0, replacements=0
            )
            return

        stats = {
            "attached": 0,
            "staged": 0,
            "manual": 0,
            "duplicates": 0,
            "failed": 0,
            "replacements": 0,
        }
        self._current_index = LibraryIndex(lib)
        print(
            f"smartimport: {len(files)} file(s), "
            f"{len(self._current_index.albums)} existing Beets album(s)."
        )

        unknown_groups: dict[tuple[str, str], list[IncomingTrack]] = defaultdict(list)

        for path in files:
            track = self._read_incoming(path, dry_run=bool(opts.dry_run))
            if track is None:
                stats["failed"] += 1
                continue

            if not track.title or not track.artist:
                print(f"MANUAL: {path.name}: title or artist is missing.")
                stats["manual"] += 1
                if not opts.dry_run:
                    self._move_file(
                        path, self._path("manual") / self._route_name("missing_required_tags")
                    )
                continue

            local = self._local_match(track)
            selected = local
            source = "local"

            use_mb = bool(self.config["musicbrainz_fallback"].get())
            check_mb = (
                local.album is None
                or local.ambiguous
                or self._prefer_album_check(track, local)
            )
            if use_mb and not opts.no_musicbrainz and check_mb:
                mb_match = self._musicbrainz_existing_match(track)
                if mb_match.album is not None:
                    replace_local = local.album is None or local.ambiguous
                    if (
                        not replace_local
                        and bool(self.config["prefer_existing_album"].get())
                        and mb_match.album.albumtype == "album"
                        and local.album.albumtype != "album"
                    ):
                        replace_local = True
                    if replace_local:
                        selected = mb_match
                        source = "MusicBrainz"
                elif mb_match.ambiguous and local.album is None:
                    selected = mb_match
                    source = "MusicBrainz"

            if selected.ambiguous:
                print(f"MANUAL: {path.name}: {selected.reason}")
                stats["manual"] += 1
                if not opts.dry_run:
                    self._move_file(
                        path, self._path("manual") / self._route_name("ambiguous_match")
                    )
                continue

            if selected.album is not None:
                duplicate, duplicate_reason, stale_item_ids = self._is_duplicate(
                    track, selected.album
                )
                if duplicate:
                    print(
                        f"DUPLICATE: {path.name} -> "
                        f"{selected.album.albumartist} - {selected.album.title}: "
                        f"{duplicate_reason}"
                    )
                    stats["duplicates"] += 1
                    if not opts.dry_run:
                        self._move_file(
                            path, self._path("duplicates") / safe_component(
                                selected.album.albumartist
                            ) / safe_component(selected.album.title)
                        )
                    continue

                if stale_item_ids and not self.config["replace_missing_items"].get(bool):
                    stats["manual"] += 1
                    print(
                        f"MANUAL: {path.name}: {duplicate_reason}; automatic "
                        "replacement is disabled."
                    )
                    if not opts.dry_run:
                        self._move_file(
                            path, self._path("manual") / self._route_name("stale_library_entry")
                        )
                    continue

                if stale_item_ids:
                    stats["replacements"] += len(stale_item_ids)
                    print(f"REPLACEMENT: {path.name}: {duplicate_reason}")

                print(
                    f"MATCH ({source}, score {selected.score:.0f}): "
                    f"{path.name} -> {selected.album.albumartist} - "
                    f"{selected.album.title}; {selected.reason}"
                )
                if not opts.dry_run:
                    ok, detail = self._attach(
                        lib,
                        track,
                        selected.album,
                        selected.target_track,
                        stale_item_ids,
                    )
                    if ok:
                        stats["attached"] += 1
                        print(f"  Successfully attached: {detail}")
                        # Keep the in-memory index current for later files in
                        # this same batch.
                        self._current_index = LibraryIndex(lib)
                    else:
                        stats["failed"] += 1
                        print(f"  ERROR while attaching: {detail}")
                        if path.exists():
                            self._move_file(
                                path, self._path("failed") / self._route_name("attach_error")
                            )
                continue

            if not track.album:
                print(f"MANUAL: {path.name}: album tag is missing.")
                stats["manual"] += 1
                if not opts.dry_run:
                    self._move_file(
                        path, self._path("manual") / self._route_name("missing_album_tag")
                    )
                continue

            print(
                f"NEW RELEASE: {path.name}: no existing album was confirmed "
                "unambiguously; staging for a normal Beets release import."
            )
            unknown_groups[self._group_key(track)].append(track)

        for tracks in unknown_groups.values():
            first = tracks[0]
            if not self._group_is_coherent(tracks):
                print(
                    f"MANUAL: {len(tracks)} files tagged as album "
                    f"{first.album!r} do not form an unambiguous release group."
                )
                stats["manual"] += len(tracks)
                if not opts.dry_run:
                    target = self._path("manual") / self._route_name("incoherent_release_group")
                    for track in tracks:
                        self._move_file(track.path, target)
                continue

            if opts.dry_run:
                display_owner = first.albumartist
                if not display_owner or is_various(display_owner):
                    display_owner = first.artist or "Various Artists"

                stats["staged"] += len(tracks)
                print(
                    f"STAGING (dry-run): {len(tracks)} file(s) for "
                    f"{display_owner} - {first.album}"
                )
            else:
                destination = self._stage_group(tracks)
                stats["staged"] += len(tracks)
                print(
                    f"STAGING: moved {len(tracks)} file(s) to {destination}."
                )

        self._notify_run_summary(
            dry_run=dry_run,
            ready=len(files),
            attached=stats["attached"],
            staged=stats["staged"],
            manual=stats["manual"],
            duplicates=stats["duplicates"],
            failed=stats["failed"],
            replacements=stats["replacements"],
        )

    def smartcleanup(self, lib: Any, opts: Any, args: list[str]) -> None:
        self._ensure_directories()
        staging = self._path("staging")
        extensions = self._audio_extensions()
        groups = [path for path in staging.iterdir() if path.is_dir()]
        moved = 0

        for group in groups:
            audio = [
                path
                for path in group.rglob("*")
                if path.is_file() and path.suffix.casefold() in extensions
            ]
            if not audio:
                shutil.rmtree(group, ignore_errors=True)
                continue

            target = self._path("manual") / self._route_name("cleanup_manual") / group.name
            if not opts.dry_run:
                target.parent.mkdir(parents=True, exist_ok=True)
                if target.exists():
                    target = target.with_name(
                        f"{target.name}-{int(time.time())}"
                    )

            print(
                f"MANUAL: {len(audio)} file(s) remained after the Beets import: "
                f"{group} -> {target}"
            )
            if not opts.dry_run:
                shutil.move(str(group), str(target))

            manual_config = self.config["manual_import_config"].as_str().strip()
            if manual_config:
                manual_command = (
                    f"beet -vv -c {shlex.quote(manual_config)} import "
                    f"{shlex.quote(str(target))}"
                )
            else:
                manual_command = f"beet -vv import {shlex.quote(str(target))}"
            print("MANUAL IMPORT COMMAND:")
            print(f"  {manual_command}")
            moved += len(audio)

        print(f"Smart Cleanup complete: {moved} file(s) moved to manual review.")
        if moved and self._notifications_enabled_for_run(bool(opts.dry_run)):
            if self.config["notifications"]["notify_on_attention"].get(bool):
                self.notifier.send(
                    "Beets smartimport: manual review required",
                    f"SmartCleanup moved {moved} file(s) to manual review.",
                    "warning",
                )

    def smartrepair(self, lib: Any, opts: Any, args: list[str]) -> None:
        if (
            opts.apply
            and opts.include_fallback_fields
            and not opts.album_ids
        ):
            print(
                "Safety stop: --include-fallback-fields with --apply requires "
                "at least one --album-id."
            )
            return

        selected_ids = set(opts.album_ids or [])
        reported_items = 0
        reported_fields = 0
        applied_items = 0
        applied_fields = 0
        changed_albums = 0
        conflicts = 0

        for album in lib.albums():
            if selected_ids and int(album.id) not in selected_ids:
                continue

            items = list(album.items())
            canonical_overrides: dict[str, str] = {}
            conflict_fields: set[str] = set()
            album_dirty = False

            # Preserve useful IDs when an older album row is blank but every
            # valid track-level value agrees. Conflicting IDs are never
            # resolved automatically.
            for key in ("mb_albumid", "mb_releasegroupid"):
                album_value = text(album.get(key, ""))
                valid_item_values = {
                    text(item_value(item, key, "")).strip()
                    for item in items
                    if valid_uuid(item_value(item, key, ""))
                }

                if valid_uuid(album_value):
                    canonical_overrides[key] = album_value
                elif len(valid_item_values) == 1:
                    promoted = next(iter(valid_item_values))
                    canonical_overrides[key] = promoted
                    print(
                        f"PROMOTE ALBUM ID: {album.albumartist} - "
                        f"{album.album}: {key} -> {promoted}"
                    )
                    if opts.apply:
                        album[key] = promoted
                        changed_albums += 1
                        album_dirty = True
                elif len(valid_item_values) > 1:
                    conflict_fields.add(key)
                    conflicts += 1
                    values = ", ".join(sorted(valid_item_values))
                    print(
                        f"CONFLICT: {album.albumartist} - {album.album}: "
                        f"multiple {key} values: {values}"
                    )

            if opts.apply and album_dirty:
                try:
                    album.store()
                except Exception as error:
                    print(
                        f"Album could not be stored: "
                        f"{album.albumartist} - {album.album}: {error}"
                    )

            for item in items:
                differences: list[tuple[str, Any, Any, bool]] = []
                for key in REPAIR_FIELDS:
                    if key in conflict_fields:
                        continue
                    try:
                        album_value = canonical_overrides.get(
                            key, album.get(key, "")
                        )
                        current = item_value(item, key, "")
                    except Exception:
                        continue
                    if current != album_value:
                        safe = key in SAFE_REPAIR_FIELDS
                        differences.append((key, current, album_value, safe))

                if not differences:
                    continue

                reported_items += 1
                reported_fields += len(differences)
                print(
                    f"DIFFERENCE: album ID {album.id} / "
                    f"{album.albumartist} - {album.album} / "
                    f"{item.artist} - {item.title}"
                )
                for key, old, new_value, safe in differences:
                    mode = "SAFE" if safe else "FALLBACK"
                    print(f"  [{mode}] {key}: {old!r} -> {new_value!r}")

                if opts.apply:
                    applicable = [
                        row
                        for row in differences
                        if row[3] or opts.include_fallback_fields
                    ]
                    if not applicable:
                        continue
                    album_id_changed = any(
                        key == "mb_albumid" and old != new_value
                        for key, old, new_value, _ in applicable
                    )
                    for key, _, new_value, _ in applicable:
                        try:
                            item[key] = new_value
                        except Exception:
                            continue
                    if album_id_changed:
                        try:
                            item["mb_releasetrackid"] = ""
                        except Exception:
                            pass
                    item.store()
                    if not opts.no_write:
                        item.write()
                    applied_items += 1
                    applied_fields += len(applicable)

        mode = "applied" if opts.apply else "reported only"
        print(
            f"SmartRepair: {reported_items} track(s), "
            f"{reported_fields} field difference(s), "
            f"{applied_items} track(s) and {applied_fields} field(s) "
            f"{mode}, {changed_albums} album ID update(s), "
            f"{conflicts} conflict(s)."
        )

