"""Real codex in a PTY, inline (no alternate screen) mode."""
import unittest

from shared.codex_autocontinue.tests.real_codex_cases import CODEX, SKIP_REASON, RealCodexCases


@unittest.skipUnless(CODEX, SKIP_REASON)
class RealCodexTests(RealCodexCases, unittest.TestCase):
    alternate_screen = False
