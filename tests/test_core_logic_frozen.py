import hashlib
import inspect
import textwrap

import beetsplug.smartimport as smartimport


# Exact source fingerprints for the protected matching/import core in v1.0.0.
#
# Do not use ast.dump() for these snapshots: Python adds/changes AST fields
# between interpreter releases, which makes otherwise identical source code
# hash differently across the supported Python 3.10-3.14 matrix.
#
# These source fingerprints are deliberately strict. If one of the protected
# methods changes intentionally, review that diff first and only then refresh
# the corresponding fingerprint.
EXPECTED = {
    "_score_album": "67bb818cc93ca8a5bda334e37f1dfa5e9fc5c861c9ad3191128a5092bc201f25",
    "_candidate_pool": "527a7f70d8009788e6ccd4c35c274364cb7f79ebd3c5b3ea7adade43058afc5c",
    "_local_match": "34b977c384c486db5933d958416a019e429b071075505ae0fa342adab1f1b1fd",
    "_recording_matches_track": "1afa38cb6a8acd08da1147eab22f88313c22ccef3ff179539bff5469bb30b45a",
    "_target_track_on_release": "02bebc44218634a7d0a95de56e7c7dd66832ff5a992757cc99b190c6e99e4dd6",
    "_musicbrainz_existing_match": "4dbada83a1eb55fb6135bf9ec3001e7ef66c9d768acf3f3d0e4e5ff709dbe2c8",
    "_duplicate_reason": "aeef9a4635e55f6266e0c8e2b7e0bc654d07607aaf2d59e9fda945b654943373",
    "_target_track_from_existing_release": "9eb27ea6bdeaab8f48132d6f34aa7e4e910e82c58e07aa85611557314d8e0df4",
    "_apply_safe_disc_fallback": "3f9fb65e53498e6ada278c289ae524ddd7d517dbf6a810c665bfeab24b824821",
    "_apply_target_track": "0ce6daf5228c8b8bfb456d900ffe67ba218b376689a440c6a7fd4712bc105274",
    "_group_key": "e1275868cf8e36256eb1b158bdf79fd2893a419d4b38b7d5a1e51793d45f6533",
    "_group_is_coherent": "4554d988b310b5400dabc2b6766e66ce5946c8b2725a45a022432eafb6551156",
    "_prefer_album_check": "854cdf91e919d3ea038376ec29c5f7217c76c3292216e8ed0602eddcadfe9742",
}


def normalized_method_hash(name: str) -> str:
    method = getattr(smartimport.SmartImportPlugin, name)
    source = inspect.getsource(method)
    source = textwrap.dedent(source).replace("\r\n", "\n").replace("\r", "\n")
    source = "\n".join(line.rstrip() for line in source.split("\n")).strip() + "\n"
    return hashlib.sha256(source.encode("utf-8")).hexdigest()


def test_production_core_logic_is_frozen():
    actual = {name: normalized_method_hash(name) for name in EXPECTED}
    assert actual == EXPECTED
