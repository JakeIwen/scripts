"""When the editing keys fire: their BTT trigger conditions and truth table.

The conditions are NSPredicate strings over BTT's variables; text_editing_conditions.js
evaluates them with Foundation, the engine BTT itself uses.
"""
from __future__ import annotations

from functools import lru_cache
import json
from pathlib import Path
import subprocess
from typing import NamedTuple

EVALUATOR = Path(__file__).with_suffix('.js')

# Code editors already wrap selections and cut lines; terminals treat keys as input.
EXCLUDED_APPS = (
    'com.microsoft.VSCode', 'com.microsoft.VSCodeInsiders', 'com.todesktop.230313mzl4w4u92',
    'com.exafunction.windsurf', 'dev.zed.Zed', 'dev.zed.Zed-Preview', 'com.sublimetext.4',
    'com.sublimetext.3', 'com.apple.dt.Xcode', 'com.barebones.bbedit', 'com.panic.Nova',
    'com.coteditor.CotEditor', 'org.vim.MacVim', 'org.gnu.Emacs',
    'com.apple.Terminal', 'com.googlecode.iterm2', 'com.mitchellh.ghostty', 'dev.warp.Warp-Stable',
    'net.kovidgoyal.kitty', 'org.alacritty', 'com.github.wez.wezterm')
EXCLUDED_PREFIXES = ('com.jetbrains.',)


BUILTIN_VARIABLES = ('BTTActiveAppBundleIdentifier', 'focused_element_details')
LINE_CLIPBOARD_VARIABLE = 'jacobr_editing_keys_line_clipboard'
LINE_CLIPBOARD_OFF, LINE_CLIPBOARD_ON = '0', '1'
# Every persistent variable a condition reads, with the value the installer
# initializes it to: an unbound variable can swallow the key instead of passing it through.
INITIAL_VARIABLES = {LINE_CLIPBOARD_VARIABLE: LINE_CLIPBOARD_OFF}


def app_condition():
    apps = ', '.join(json.dumps(app) for app in EXCLUDED_APPS)
    prefixes = ' OR '.join('BTTActiveAppBundleIdentifier BEGINSWITH ' + json.dumps(p) for p in EXCLUDED_PREFIXES)
    return 'NOT (BTTActiveAppBundleIdentifier IN {' + apps + '} OR ' + prefixes + ')'


def details_condition(length_pattern, *, text_area=False):
    # BTT's focused-element dump marks settable attributes "(W)": read-only text
    # (terminal output, web pages) and apps without a text tree never match.
    role = '(?=.*AXRole:  "AXTextArea")' if text_area else ''
    return ("focused_element_details MATCHES '(?s)" + role + "(?=.*AXSelectedText [(]W[)]:  \").*"
            "AXSelectedTextRange [(]W[)]:  \"pos=[0-9]+ len=" + length_pattern + "\".*'")


def selection_condition(length_pattern):
    return app_condition() + ' AND ' + details_condition(length_pattern)


WRAP_CONDITION = selection_condition('[1-9][0-9]*')
CARET_CONDITION = selection_condition('0')
# Cheapest gates first: the excluded-app check and the tag variable are plain
# comparisons, so the costly focused-element regex only runs for a tagged paste
# in an allowed app.
PASTE_CONDITION = (app_condition() + ' AND ' + LINE_CLIPBOARD_VARIABLE + ' == ' + json.dumps(LINE_CLIPBOARD_ON)
                   + ' AND ' + details_condition('0', text_area=True))


class ConditionCase(NamedTuple):
    name: str
    app: str
    details: str
    wrap: bool
    caret: bool
    tagged: bool = False
    paste: bool = False


def _focused(selected_text, selected_range):
    return ('<AXApplication: "App">\n <AXWindow: "Window">\n  <AXTextArea>\n\nAttributes:\n'
            '   AXFocused:  "1"\n   ' + selected_text + '\n   ' + selected_range +
            '\n   AXRole:  "AXTextArea"\n   AXValue (W):  "alpha beta"\n')


SELECTION = _focused('AXSelectedText (W):  "beta"', 'AXSelectedTextRange (W):  "pos=6 len=4"')
CARET = _focused('AXSelectedText (W):  ""', 'AXSelectedTextRange (W):  "pos=8 len=0"')
# focused_element_details shapes copied from live dumps (TextEdit, Chrome, Signal, iTerm).
CASES = (
    ConditionCase('selection', 'com.apple.TextEdit', SELECTION, wrap=True, caret=False),
    ConditionCase('caret', 'com.apple.TextEdit', CARET, wrap=False, caret=True),
    ConditionCase('tagged AXTextArea caret', 'com.apple.TextEdit', CARET, False, True, True, True),
    ConditionCase('tagged AXTextField caret', 'com.apple.TextEdit',
                  CARET.replace('AXTextArea', 'AXTextField'), False, True, True, False),
    ConditionCase('tagged menu-less search caret', 'com.runningwithcrayons.Alfred',
                  CARET.replace('AXTextArea', 'AXTextField'), False, True, True, False),
    ConditionCase('tagged selection', 'com.apple.TextEdit', SELECTION, True, False, True, False),
    ConditionCase('tagged code editor', 'com.microsoft.VSCode', CARET, False, False, True, False),
    ConditionCase('tagged terminal', 'com.googlecode.iterm2', CARET, False, False, True, False),
    ConditionCase('selection starting with a quote', 'com.google.Chrome',
                  _focused('AXSelectedText (W):  ""hi""', 'AXSelectedTextRange (W):  "pos=3 len=4"'), True, False),
    ConditionCase('multi-line selection', 'org.mozilla.firefox',
                  _focused('AXSelectedText (W):  "a\nb"', 'AXSelectedTextRange (W):  "pos=0 len=13"'), True, False),
    ConditionCase('read-only text', 'com.apple.mail',
                  _focused('AXSelectedText:  ""', 'AXSelectedTextRange (W):  "pos=8 len=0"'), False, False),
    ConditionCase('no text tree yet', 'org.whispersystems.signal-desktop',
                  '<AXApplication: "Signal">\n <AXWindow: "Signal">\n\nAttributes:\n   AXRole:  "AXWindow"\n',
                  False, False),
    ConditionCase('code editor selection', 'com.microsoft.VSCode', SELECTION, False, False),
    ConditionCase('JetBrains caret', 'com.jetbrains.pycharm', CARET, False, False),
    ConditionCase('terminal caret', 'com.googlecode.iterm2', CARET, False, False),
    ConditionCase('nothing focused', '', '', False, False),
)


def evaluate(predicate, variables):
    """NSPredicate's own serialization of `predicate` and its result for each variable set."""
    result = subprocess.run(['/usr/bin/osascript', '-l', 'JavaScript', str(EVALUATOR),
                             json.dumps({'predicate': predicate, 'cases': variables})],
                            capture_output=True, text=True, timeout=30)
    if result.returncode:
        raise RuntimeError('Condition could not be evaluated: ' + (result.stderr.strip() or predicate))
    return json.loads(result.stdout)


@lru_cache(maxsize=None)
def canonical(predicate):
    """BTT re-serializes a stored condition; compare conditions in NSPredicate's format."""
    return evaluate(predicate, [])['format']


def variables_used(predicate):
    """The variable names `predicate` reads, as NSPredicate parses it."""
    return sorted(evaluate(predicate, [])['keys'])


def truth_table_errors():
    variables = [{'BTTActiveAppBundleIdentifier': case.app, 'focused_element_details': case.details,
                  LINE_CLIPBOARD_VARIABLE: LINE_CLIPBOARD_ON if case.tagged else LINE_CLIPBOARD_OFF}
                 for case in CASES]
    errors = []
    for key, predicate in (('wrap', WRAP_CONDITION), ('caret', CARET_CONDITION), ('paste', PASTE_CONDITION)):
        for case, fired in zip(CASES, evaluate(predicate, variables)['results']):
            if fired != getattr(case, key):
                errors.append('%s condition %s on: %s' % (key, 'fires' if fired else 'misses', case.name))
    return errors
