"""Real codex in a PTY, full-screen (alternate screen) mode."""
import unittest

from shared.codex_autocontinue.tests.real_codex_cases import CODEX, SKIP_REASON, RealCodexCases


@unittest.skipUnless(CODEX, SKIP_REASON)
class FullScreenRealCodexTests(RealCodexCases, unittest.TestCase):
    alternate_screen = True
