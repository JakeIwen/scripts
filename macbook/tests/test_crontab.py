"""Every repo script the tracked crontab runs exists and is executable.

Cron reports a missing script only to local mail, so a moved script must fail here instead.
"""
import os
from pathlib import Path
import shlex
from string import Template
import tempfile
import unittest
from unittest.mock import patch

REPO=Path(__file__).resolve().parents[2]
CRONTAB=REPO/'macbook'/'crontab'
LIVE_CHECKOUT='/Users/jacobr/dev/scripts/'
REDIRECTS={'>','>>','<','2>','2>>'}


def jobs():
    env,commands={},[]
    for line in CRONTAB.read_text().splitlines():
        line=line.strip()
        if line.startswith('#off '): line=line[len('#off '):].lstrip()
        elif not line or line.startswith('#'): continue
        if not line: continue
        first=line.split(None,1)[0]
        if '=' in first and not first[0].isdigit() and first[0] not in '*@':
            name,value=line.split('=',1);env[name.strip()]=value.strip().strip('"\'');continue
        command=line.split(None,1)[1] if line.startswith('@') else line.split(None,5)[5]
        commands.append(Template(command).safe_substitute(env))
    return commands


def repo_paths(command):
    """(path, is_executable_position) for each checkout path that is not a redirect target."""
    tokens=shlex.split(command);found=[];skip=False
    for index,token in enumerate(tokens):
        if skip: skip=False;continue
        if token in REDIRECTS: skip=True;continue
        if token[:1] in '<>' or token.startswith('2>'): continue
        if token.startswith(LIVE_CHECKOUT): found.append((REPO/token[len(LIVE_CHECKOUT):],index==0))
    return found


class CrontabTests(unittest.TestCase):
    def test_crontab_has_jobs(self):
        self.assertGreater(len(jobs()),0)

    def test_paused_jobs_are_still_parsed(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture=Path(directory)/'crontab'
            fixture.write_text('#off 0 2 * * * /Users/jacobr/dev/scripts/macbook/paused.sh\n')
            with patch(f'{__name__}.CRONTAB',fixture):
                self.assertEqual(jobs(),['/Users/jacobr/dev/scripts/macbook/paused.sh'])

    def test_every_repo_path_exists(self):
        for command in jobs():
            for path,_ in repo_paths(command):
                with self.subTest(path=str(path)): self.assertTrue(path.exists(),f'{path} named in {CRONTAB} is missing')

    def test_directly_run_scripts_are_executable(self):
        for command in jobs():
            for path,executable in repo_paths(command):
                if executable:
                    with self.subTest(path=str(path)): self.assertTrue(os.access(path,os.X_OK),f'{path} is not executable')


if __name__=='__main__':
    unittest.main()
