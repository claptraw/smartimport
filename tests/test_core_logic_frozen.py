import ast
import hashlib
import inspect

import beetsplug.smartimport as smartimport


EXPECTED = {
    "_score_album": "5791ff04d06184c0f020ab30c784a0cb0e0377384a8122cec91accf3c91d81c3",
    "_candidate_pool": "5d595fcf4aa9c6b0f7d1bc2e76351fe580a84fd94c068c1842333ede1681c6c7",
    "_local_match": "1e8ec05966dfa02886c23a339c21b2d1a007257ae9720fa2580718093a4cd168",
    "_recording_matches_track": "b96f5aba358c4ae0a4c9dda8fdb9d31151fc0857a8bf08be3a6442715a4db985",
    "_target_track_on_release": "17af2b89fd8d7c13263e7e081ec41f9a7005aa27db958e16e1b04fb3d5f71c4d",
    "_musicbrainz_existing_match": "0865bda5ae94dff7f76a86de6cc51ecdae4a629f59157a891281b1c55c0e9f64",
    "_duplicate_reason": "d308ea8b42e6db1692d532c781e3a3e567bb20cba33c217360ae32ecc90dc3a8",
    "_target_track_from_existing_release": "89582e69d8c1027de6785e2c9106625a265d94edd056d58d704beef6027880d4",
    "_apply_safe_disc_fallback": "e6a80002645dd4373b6eca8b48d4b1dd945a5c5e495b3a80e0c6520224699418",
    "_apply_target_track": "98dce29f2db53a626bb8b71f987642f194298864082158525ca5fbe1fa7bc6ed",
    "_group_key": "952e72f20489d97bb30344255d599b97c98c52e8ea4d7025530956c7a1446455",
    "_group_is_coherent": "0d10e6b926e96f5d44261a77c97d6ba9920f7d864fdde2612f52824a13f18885",
    "_prefer_album_check": "817994e301293c0eee7eb3a0fcb05a77eb993c94be75c5308dee705c69931b54",
}


class NormalizeStrings(ast.NodeTransformer):
    def visit_Constant(self, node):
        if isinstance(node.value, str):
            return ast.copy_location(ast.Constant(value="<STR>"), node)
        return node


def normalized_method_hash(name: str) -> str:
    source = inspect.getsource(smartimport.SmartImportPlugin)
    tree = ast.parse(source)
    cls = next(node for node in tree.body if isinstance(node, ast.ClassDef))
    method = next(
        node
        for node in cls.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name
    )
    method = NormalizeStrings().visit(ast.fix_missing_locations(method))
    payload = ast.dump(method, include_attributes=False).encode()
    return hashlib.sha256(payload).hexdigest()


def test_production_core_logic_is_frozen():
    actual = {name: normalized_method_hash(name) for name in EXPECTED}
    assert actual == EXPECTED
