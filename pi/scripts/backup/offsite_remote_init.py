"""Shared-folder (kind smb) setup: configure-smb writes remote.json, init-remote registers a label.

configure-smb is two processes so the DSM password is typed while no lock is
held (plan review R9). The launcher's unlocked route runs start_configure, which
checks the fields, reads the password and starts the stable launcher's
`configure-smb-run`; that route takes job-offsite and the snapshot's shared
legacy lock like every run, execs the frozen worker, and configure_smb reads
the password from the pipe. init-remote runs like backup, frozen with rclone.
It registers only the id restic decrypts, and never adopts an existing
repository unless asked with that id (plan review R1).
"""
import datetime as dt
import getpass
import json
import os
from pathlib import Path
import re
import subprocess
import sys

from offsite_config import (INSTALLED_RCLONE, LABEL_PATTERN, REMOTE_CONFIG, REPO_ID_PATTERN, REPO_PATH, SCHEMA,
                            SMB_PASSWORD_LENGTH, TRANSPORT_KINDS, MarkerRecord, SmbTransportConfig, TransportKind,
                            valid_priority, validate_transport)
from offsite_prepare import protected_labels
from offsite_remote import RemoteRepository, transport_config
from offsite_repository import CommandFailed, Interrupted, durable_json, no_core_dumps, private_directory, records
from offsite_repository import register
from offsite_repository import password as password_file  # configure_smb's `password` is the DSM one
from offsite_smb import check_rclone_version, obscure

# restic's words when the path already holds a repository: its backends' Create, then
# repository.Init. test_offsite_rclone_integration.py checks them against the pinned binaries.
ALREADY_INITIALIZED = ('config file already exists', 'repository master key and config already initialized')
# Creating a repository over SMB makes a few hundred directories on the share.
INIT_SECONDS = 600
SHARE_NOTE = 'Every --remote run for every label now goes to this shared folder.'
# Stands in for the password while the other fields are checked, before it is asked for.
PLACEHOLDER = 'x' * SMB_PASSWORD_LENGTH[0]
# One more than the longest valid line: a longer one stays too long, so validation refuses it.
LINE_LIMIT = SMB_PASSWORD_LENGTH[1] + 2
FIELDS = ('address', 'share', 'path', 'user', 'port')


def smb_record(*, address, share, path, user, port, password) -> SmbTransportConfig:
    """The kind smb remote.json record, validated as the Pi reads it; errors never quote the password."""
    record: SmbTransportConfig = {'schema': SCHEMA, 'kind': TransportKind.SMB, 'address': address, 'port': port,
                                  'share': share, 'path': path, 'user': user, 'password': password}
    return validate_transport(record, kinds=TRANSPORT_KINDS)


def fields(args):
    return {name: getattr(args, name) for name in FIELDS}


def piped_line(stdin):
    line = stdin.readline(LINE_LIMIT)
    return line[:-1] if line.endswith('\n') else line


def read_password(user, stdin=None, prompt=getpass.getpass):
    """The DSM password: typed at the controlling terminal, else one line on stdin (scripted use)."""
    stdin = sys.stdin if stdin is None else stdin
    try:
        if stdin.isatty():
            return prompt(f'DSM password for {user}: ')
        return piped_line(stdin)
    except (EOFError, KeyboardInterrupt):
        raise RuntimeError('No shared-folder password was entered; nothing changed') from None


def start_configure(cfg, args, *, launcher=None, popen=subprocess.Popen, stdin=None, prompt=getpass.getpass):
    """configure-smb: check the fields, read the password holding no lock, then start the admitted worker.

    Returns the worker's exit status. The password reaches it only on a pipe.
    """
    no_core_dumps()  # before the DSM password is read
    if cfg.code_root is not None:
        raise RuntimeError('configure-smb runs from the live launcher, never from a code snapshot')
    if os.geteuid() != cfg.owner:
        raise RuntimeError('Run offsite_backup.sh as root')
    values = fields(args)
    smb_record(**values, password=PLACEHOLDER)  # a mistyped field fails before the password is asked for
    secret = read_password(values['user'], stdin, prompt)
    smb_record(**values, password=secret)
    launcher = Path(__file__).resolve().with_name('offsite_backup.sh') if launcher is None else launcher
    child = popen([str(launcher), 'configure-smb-run', *(f'--{name}={value}' for name, value in values.items())],
                  stdin=subprocess.PIPE, text=True)
    try:
        child.communicate(secret + '\n')
    except KeyboardInterrupt:
        child.wait()  # The worker shares the terminal's process group, so it saw the interrupt too.
    return child.returncode if child.returncode >= 0 else 128 - child.returncode


def configure_from_pipe(cfg, args, *, stdin=None, run=subprocess.run):
    """configure-smb-run, the admitted worker: the password comes from configure-smb's pipe, never a terminal."""
    no_core_dumps()  # before the DSM password is read
    stdin = sys.stdin if stdin is None else stdin
    if stdin.isatty():
        raise RuntimeError('configure-smb-run reads its password from configure-smb; run configure-smb')
    return configure_smb(cfg, **fields(args), password=piped_line(stdin), run=run)


def restic_password(cfg):
    """This Pi's restic encryption password, or None before prepare-disk or init-remote creates it."""
    try:
        password_file(cfg)
    except (OSError, RuntimeError):
        return None  # RemoteRepository.configure() compares again, and refuses an unsafe file, at every run.
    return cfg.password.read_text().strip()


def configure_smb(cfg, *, address, share, path, user, port, password, run=subprocess.run):
    """Write remote.json for a DSM shared folder; returns a summary without the password.

    Runs with job-offsite held, so no run reads the record while it changes. The
    installed rclone, which every later remote run freezes, must be the pinned
    version and able to obscure this password; the obscured form is discarded.
    Rerunning replaces the file atomically.
    """
    record = smb_record(address=address, share=share, path=path, user=user, port=port, password=password)
    if restic_password(cfg) == password:
        raise ValueError('The shared-folder password must not be the restic encryption password')
    check_rclone_version(INSTALLED_RCLONE, run=run)
    obscure(INSTALLED_RCLONE, password, run=run)
    labels = sorted(records(cfg, optional=True)['repos'])
    private_directory(cfg.state, cfg.owner)
    durable_json(cfg.state / REMOTE_CONFIG, record)
    summary = {name: record[name] for name in ('kind', 'address', 'port', 'share', 'path', 'user')}
    return {**summary, 'labels': labels, 'note': SHARE_NOTE}


def checked_request(cfg, commands, label, priority, adopt):
    """Every init-remote refusal that needs no network, before the transport is touched; returns the records."""
    if not isinstance(label, str) or not re.fullmatch(LABEL_PATTERN, label):
        raise ValueError('Label must be 1-16 ASCII letters/digits/dot/underscore/hyphen')
    if not valid_priority(priority):
        raise ValueError('Priority must be an integer from 0 to 2147483647')
    if adopt is not None and (not isinstance(adopt, str) or not re.fullmatch(REPO_ID_PATTERN, adopt)):
        raise ValueError('--adopt takes a repository id: 64 lowercase hexadecimal characters')
    known = records(cfg, optional=True)
    if label in known['repos']:
        raise ValueError(f'{label} is already registered; nothing changed')
    # A remote label equal to a source disk's would make a run skip mounting that source.
    if label in protected_labels(commands):
        raise ValueError(f'{label} names a disk in disk_policy.sh; choose another label')
    try:
        kind = transport_config(cfg.state / REMOTE_CONFIG).get('kind', TransportKind.REST)
    except ValueError as exc:
        raise ValueError(f'init-remote needs a kind smb remote.json ({exc}); run configure-smb first') from None
    if kind != TransportKind.SMB:
        raise ValueError('init-remote needs a kind smb remote.json; a REST receiver repository is seeded from a drive')
    return known


def decrypted_id(repo):
    """The id in the repository's decrypted config, which every later run verifies."""
    try:
        value = json.loads(repo.restic('cat', 'config')).get('id')
    except (ValueError, AttributeError):
        raise RuntimeError('restic cat config did not print a repository config') from None
    if not isinstance(value, str) or not re.fullmatch(REPO_ID_PATTERN, value):
        raise RuntimeError('The repository config has no 64-hex id')
    return value


def existing_repository(repo):
    """The refusal for a path that already holds a repository; names its id when this Pi can open it."""
    try:
        found = decrypted_id(repo)
    except Interrupted:
        raise
    except RuntimeError:
        return ('A restic repository this Pi cannot open already exists at that path; nothing registered. '
                'Choose another --path with configure-smb')
    return (f'Existing repository id {found} at that path; nothing registered. '
            f'Rerun with --adopt {found} only if it is yours')


def create_repository(repo):
    try:
        repo.restic('init', timeout=INIT_SECONDS)
    except Interrupted:
        raise
    except CommandFailed as exc:
        # Raw output: the displayed message is redacted, and a password that equals
        # these words would otherwise mask them.
        if not any(words in exc.output for words in ALREADY_INITIALIZED):
            raise
        raise RuntimeError(existing_repository(repo)) from None


def init_remote(cfg, commands, *, label, priority, adopt=None):
    """Create the shared-folder repository (or, given its id as adopt, take it over) and register label.

    Returns a summary. Registration happens only after the decrypted id is known;
    a fresh, non-registering repository then verifies it as every later run does.
    """
    no_core_dumps()  # before remote.json's DSM password or the restic password is read
    known = checked_request(cfg, commands, label, priority, adopt)
    # prepare-disk's rule: the one encryption password is created only while no repository is known.
    password_file(cfg, create=not known['repos'])
    repo = RemoteRepository(cfg, commands, label, registering=True)
    repo.configure()
    if adopt is None:
        create_repository(repo)
    repo_id = decrypted_id(repo)
    if adopt is not None and repo_id != adopt:
        raise RuntimeError(f'The repository at that path has id {repo_id}, not {adopt}; nothing registered')
    # A mirrored seed keeps its drive's label; two labels for one repository would diverge.
    for other, registered in known['repos'].items():
        if registered['repo_id'] == repo_id:
            raise RuntimeError(f'Repository id {repo_id} is already registered as {other}; use that label with '
                               '--remote; nothing registered')
    marker: MarkerRecord = {'schema': SCHEMA, 'label': label, 'repo_id': repo_id, 'repo_path': REPO_PATH,
                            'priority': priority, 'created': dt.datetime.now(dt.timezone.utc).isoformat()}
    register(cfg, known, marker)
    try:
        RemoteRepository(cfg, commands, label).verify()
    except BaseException:
        print(f'offsite: {label} is registered as {repo_id}, but its confirming check did not pass; '
              'every later run checks the id again before writing', file=sys.stderr, flush=True)
        raise
    return {'label': label, 'repo_id': repo_id, 'adopted': adopt is not None}
