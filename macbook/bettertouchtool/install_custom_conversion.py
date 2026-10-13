#!/usr/bin/env python3
"""Add only Custom conversion to the existing Performance Audio dropdown."""
import argparse
import json
import os
from pathlib import Path
import shlex
import subprocess

from btt_common import MEDIA_TOOLS, ROOT, MEDIA
from build_performance_audio_menu import uid, shell_action
from btt_touchbar_folder_to_floating_submenu import rtf_label


def definition(media_tools=None):
    media_tools = MEDIA_TOOLS if media_tools is None else Path(media_tools)
    resources = media_tools / 'build/Performance Audio.app/Contents/Resources'
    script = resources/'conversion_editor.py'
    if not script.is_file(): raise RuntimeError('Build the updated app first.')
    python = (resources/'python-path.txt').read_text().strip()
    if not Path(python).is_file(): raise RuntimeError('App Python runtime is missing.')
    parent = uid('tools-dropdown')
    button = uid('tools-dropdown-'+uid('item-custom'))
    command = shlex.join([python, str(script), '--config', str(media_tools/'scripts/performance_audio_presets.json'),
                          '--finder-selection', '--background'])
    return {'media': MEDIA, 'parent': parent, 'item': {
        'BTTUUID': button, 'BTTTriggerParentUUID': parent, 'BTTTriggerType': 773,
        'BTTTriggerClass': 'BTTTriggerTypeFloatingMenu', 'BTTEnabled': 1,
        'BTTMenuName': 'Custom conversion…', 'BTTMenuConfig': {
            'BTTMenuElementIdentifier': 'performance-custom', 'BTTMenuItemText': 'Custom conversion…',
            'BTTMenuAttributedText': rtf_label('Custom conversion…'),
            'BTTMenuItemMinWidth': 330, 'BTTMenuItemMaxWidth': 330,
            'BTTMenuItemMinHeight': 40, 'BTTMenuItemMaxHeight': 40,
            'BTTMenuItemVisibleWhileActive': 1, 'BTTMenuItemVisibleWhileInactive': 1},
        'BTTMenuItemActions': [shell_action(command, button)]}}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    os.umask(0o077)
    media_tools = MEDIA_TOOLS
    payload_data = definition(media_tools)
    build = ROOT/'macbook/build'
    build.mkdir(exist_ok=True)
    payload = build/'Custom Conversion Tools.json'
    payload.write_text(json.dumps(payload_data, indent=2))
    result = subprocess.run(['/usr/bin/osascript', '-l', 'JavaScript', str(Path(__file__).with_suffix('.js')),
        str(payload), str(Path(__file__).parent/'btt_common.js'), str(build),
        'apply' if args.apply else 'inspect'])
    raise SystemExit(result.returncode)


if __name__ == '__main__': main()
