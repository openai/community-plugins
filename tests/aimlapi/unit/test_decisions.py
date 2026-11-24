"""Unit tests for skills/aimlapi-decisions/scripts/decide.py. No network, no API key.

    python3 -m unittest tests.test_decisions
"""

from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3] / "plugins" / "aimlapi"
_spec = importlib.util.spec_from_file_location("decide", ROOT / "skills/aimlapi-decisions/scripts/decide.py")
decide = importlib.util.module_from_spec(_spec)
sys.modules["decide"] = decide
_spec.loader.exec_module(decide)

TEXT = {"type": "string"}


def doc(path: str, *variants: dict) -> dict:
    schema = {"anyOf": list(variants)} if len(variants) > 1 else variants[0]
    return {"paths": {path: {"post": {"requestBody": {"content": {"application/json": {"schema": schema}}}}}}}


SYSTEM_ONE = {
    "required": ["model", "state", "questions"],
    "properties": {
        "model": {"enum": ["vendor/judge"]},
        "state": TEXT,
        "questions": {
            "type": "object",
            "minProperties": 1,
            "maxProperties": 128,
            "additionalProperties": {"oneOf": [
                {"properties": {"type": {"enum": ["noul"]}, "instructions": TEXT, "criteria": {"type": "object"}},
                 "required": ["type", "instructions"]},
                {"properties": {"type": {"enum": ["choice"]}, "instructions": TEXT, "multiple": {"type": "boolean"},
                                "criteria": {"type": "object", "minProperties": 2, "maxProperties": 255}},
                 "required": ["type", "instructions", "criteria"]},
            ]},
        },
        "images": {"type": "array", "maxItems": 8, "description": "Up to 8 images."},
        "assets": {"type": "object", "description": "Not supported here: send images in `images`."},
        "media": {"type": "array", "maxItems": 0, "description": "Audio is refused."},
    },
}

OPENAI = {
    "required": ["model", "input", "questions"],
    "properties": {
        "model": {"enum": ["vendor/other"]},
        "input": TEXT,
        "questions": {
            "type": "array",
            "minItems": 1,
            "maxItems": 200,
            "items": {"oneOf": [
                {"properties": {"type": {"enum": ["predicate"]}, "name": TEXT, "instructions": TEXT},
                 "required": ["type", "name", "instructions"]},
            ]},
        },
        "prompt_cache_key": TEXT,
    },
}

ROUTER = {
    "properties": {
        "model": {"enum": ["vendor/router"]},
        "router": {"properties": {"decision_model": {"enum": ["vendor/judge", "vendor/judge-2"]}}},
    },
}


class Shape(unittest.TestCase):
    def test_system_one(self):
        out = decide.summarize(decide.request_variants(doc("/v1/decisions", SYSTEM_ONE))[0])
        self.assertEqual(out["shape"], "system-one")
        self.assertEqual(out["content_field"], "state")
        self.assertEqual(out["questions"], {"minProperties": 1, "maxProperties": 128})
        self.assertEqual([t["type"] for t in out["types"]], ["noul", "choice"])
        choice = out["types"][1]
        self.assertEqual(choice["required"], ["instructions", "criteria"])
        self.assertEqual(choice["fields"]["criteria"], {"minProperties": 2, "maxProperties": 255})
        self.assertIn("multiple", choice["fields"])

    def test_media_fields_accepted_or_refused(self):
        media = decide.summarize(SYSTEM_ONE)["media"]
        self.assertTrue(media["images"]["accepted"])
        self.assertEqual(media["images"]["maxItems"], 8)
        self.assertFalse(media["assets"]["accepted"])  # description starts "Not supported"
        self.assertFalse(media["media"]["accepted"])  # maxItems 0

    def test_openai_shape(self):
        out = decide.summarize(OPENAI)
        self.assertEqual(out["shape"], "openai")
        self.assertEqual(out["content_field"], "input")
        self.assertEqual(out["types"][0]["type"], "predicate")
        self.assertEqual(out["types"][0]["required"], ["name", "instructions"])
        self.assertEqual(out["other"], ["prompt_cache_key"])
        self.assertEqual(out["media"], {})

    def test_every_variant_is_returned(self):
        self.assertEqual(len(decide.request_variants(doc("/v1/decisions", SYSTEM_ONE, OPENAI))), 2)
        self.assertEqual(decide.request_variants({"paths": {}}), [])

    def test_limits_format(self):
        self.assertEqual(decide.fmt_limits({"minItems": 2, "maxItems": 10}), "2..10")
        self.assertEqual(decide.fmt_limits({"maxItems": 8}), "..8")
        self.assertEqual(decide.fmt_limits({}), "")


class Judges(unittest.TestCase):
    def test_enum_in_order_without_duplicates(self):
        router_doc = doc("/v1/chat/completions", ROUTER, ROUTER)
        self.assertEqual(decide.judges(router_doc), ["vendor/judge", "vendor/judge-2"])

    def test_not_a_router(self):
        self.assertEqual(decide.judges(doc("/v1/chat/completions", {"properties": {"model": TEXT}})), [])


if __name__ == "__main__":
    unittest.main()
