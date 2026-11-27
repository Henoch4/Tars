"""Keyfile-path resolution for scripts/meme_live_buy.py.

2026-10-27: the CLI was VPS-only (/etc/tars/book2-satellite.json hardcoded),
which made the tiny-live path unusable on non-VPS machines (local dev box).
A keypath override was added — --keypath / env MEME_KEYPAIR_PATH — and the
default MUST stay the VPS path so the satellite's own flow is untouched.

These tests are pure/offline: they exercise resolution only, never signing
or network. (Regression guards the "default preserved" invariant above.)
"""
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scripts.meme_live_buy import KEYPATH_DEFAULT, resolve_keypath


class TestResolveKeypath(unittest.TestCase):
    def test_no_input_defaults_to_vps_path(self):
        with mock.patch.dict(os.environ, {"MEME_KEYPAIR_PATH": ""}, clear=False):
            os.environ.pop("MEME_KEYPAIR_PATH", None)
            self.assertEqual(resolve_keypath(None), KEYPATH_DEFAULT)

    def test_env_override_wins_when_no_cli(self):
        with mock.patch.dict(os.environ, {"MEME_KEYPAIR_PATH": r"C:\tmp\kp.json"}):
            self.assertEqual(resolve_keypath(None), r"C:\tmp\kp.json")

    def test_cli_beats_env(self):
        with mock.patch.dict(os.environ, {"MEME_KEYPAIR_PATH": r"C:\tmp\kp.json"}):
            self.assertEqual(resolve_keypath(r"D:\other.json"), r"D:\other.json")

    def test_blank_env_string_usually_falls_back_to_default(self):
        with mock.patch.dict(os.environ, {"MEME_KEYPAIR_PATH": "   "}):
            self.assertEqual(resolve_keypath(None), KEYPATH_DEFAULT)


if __name__ == "__main__":
    unittest.main()
