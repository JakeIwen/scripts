"""When the Finder paste key fires: its BTT trigger condition and truth table.

The condition is an NSPredicate over BTT's variables; text_editing_conditions.evaluate
checks it with Foundation, the engine BTT itself uses.
"""
from __future__ import annotations

import json
from typing import NamedTuple

from text_editing_conditions import evaluate

FINDER = 'com.apple.finder'

# The trigger is global (BTT's API cannot file it under Finder), so the app check
# keeps every other app's Cmd-V untouched. It comes first: NSPredicate stops at a
# false AND, so a lazily read focused-element dump is only fetched in Finder.
# BTT's dump marks settable attributes "(W)": a
# settable selection is editable text (rename, search, Go to Folder, Get Info
# fields), where Cmd-V passes through to Finder's own paste. BTT offers no condition
# variable for the pasteboard's types, so the runtime sorts files, images and text.
CONDITION = ('BTTActiveAppBundleIdentifier == ' + json.dumps(FINDER) + ' AND NOT '
             "(focused_element_details MATCHES '(?s).*AXSelectedText [(]W[)]:  \".*')")


class ConditionCase(NamedTuple):
    name: str
    app: str
    details: str
    fires: bool


def _focused(path, attributes):
    return ('<AXApplication: "Finder">\n' + path + '\n\nAttributes:\n'
            + ''.join('   ' + line + '\n' for line in attributes))


# Dump shapes follow BTT's live focused_element_details (as in text_editing_conditions);
# the desktop's AXGroup focus is what Finder reports with no window open.
FILE_LIST = _focused(' <AXWindow: "Downloads">\n  <AXSplitGroup>\n   <AXScrollArea>\n    <AXOutline>',
                     ['AXFocused (W):  "1"', 'AXRole:  "AXOutline"', 'AXSelectedRows (W):  "<array of size 1>"'])
DESKTOP = _focused(' <AXScrollArea: "Desktop">\n  <AXGroup>', ['AXRole:  "AXGroup"', 'AXFocused:  "1"'])
RENAME = _focused(' <AXWindow: "Downloads">\n  <AXSplitGroup>\n   <AXScrollArea>\n    <AXOutline>\n     <AXTextField>',
                  ['AXSelectedText (W):  "Screenshot"', 'AXSelectedTextRange (W):  "pos=0 len=10"',
                   'AXRole:  "AXTextField"', 'AXValue (W):  "Screenshot.png"'])
SEARCH = _focused(' <AXWindow: "Downloads">\n  <AXToolbar>\n   <AXGroup>\n    <AXTextField>',
                  ['AXSelectedText (W):  ""', 'AXSelectedTextRange (W):  "pos=0 len=0"',
                   'AXRole:  "AXTextField"', 'AXSubrole:  "AXSearchField"'])
GO_TO_FOLDER = _focused(' <AXWindow: "Go to Folder">\n  <AXTextField>',
                        ['AXSelectedText (W):  "~/Downloads"', 'AXSelectedTextRange (W):  "pos=0 len=11"',
                         'AXRole:  "AXTextField"', 'AXValue (W):  "~/Downloads"'])
READ_ONLY = _focused(' <AXWindow: "Screenshot.png Info">\n  <AXStaticText>',
                     ['AXSelectedText:  ""', 'AXRole:  "AXStaticText"', 'AXValue:  "PNG image"'])

CASES = (
    ConditionCase('file list', FINDER, FILE_LIST, True),
    ConditionCase('desktop', FINDER, DESKTOP, True),
    # Read-only text is not a paste target; the runtime presses Paste in Get Info itself.
    ConditionCase('read-only text', FINDER, READ_ONLY, True),
    ConditionCase('nothing focused', FINDER, '', True),
    ConditionCase('rename field', FINDER, RENAME, False),
    ConditionCase('search field', FINDER, SEARCH, False),
    ConditionCase('Go to Folder', FINDER, GO_TO_FOLDER, False),
    ConditionCase('another app', 'com.apple.TextEdit', FILE_LIST, False),
    ConditionCase('no app', '', '', False),
)


def truth_table_errors():
    variables = [{'BTTActiveAppBundleIdentifier': case.app, 'focused_element_details': case.details}
                 for case in CASES]
    return ['condition %s on: %s' % ('fires' if fired else 'misses', case.name)
            for case, fired in zip(CASES, evaluate(CONDITION, variables)['results']) if fired != case.fires]
