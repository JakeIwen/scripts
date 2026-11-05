"""A verified installed bootstrap for cohort tests, independent of their live tree.

Bootstrap installation/interruption itself is exercised through its public CLI
in test_backup_bootstrap.py; these fixtures start at its verified postcondition.
"""
import json
from pathlib import Path
import shutil

import backup_bootstrap_host as host
import backup_bootstrap_plan as plan

BACKUP = Path(__file__).resolve().parents[2] / 'scripts/backup'


def seed_bootstrap(root, live, env):
    env.update(VANPI_BOOTSTRAP_ROOT=str(live),
               VANPI_BACKUP_DEPLOY_BOOTSTRAP_DIR=str(root / 'independent-recovery'))
    bootstrap = host.Bootstrap(str(live))
    # Bootstrap reads the environment at its boundary, not a second root map.
    bootstrap.state = Path(env['VANPI_BACKUP_DEPLOY_STATE_DIR'])
    bootstrap.stable = Path(env['VANPI_BACKUP_DEPLOY_BOOTSTRAP_DIR'])
    bootstrap.ready = bootstrap.state / 'bootstrap-ready.json'
    bootstrap.maintenance = bootstrap.state / 'maintenance'
    bootstrap.state.mkdir(mode=0o755, exist_ok=True)
    bootstrap.stable.mkdir(mode=0o755)
    for name in plan.STABLE_FILES:
        shutil.copyfile(BACKUP / name, bootstrap.stable / name)
    records = []
    for name in plan.GUARDS:
        data = (live / 'home/pi/scripts/backup' / name).read_bytes()
        records.append({'name': name, 'before': plan.digest(data), 'after': plan.digest(data)})
    proposed = {'version': 1, 'revision': 'fixture', 'files': records,
                'checks': plan.guard_checks(BACKUP),
                'stable': {name: plan.digest((BACKUP / name).read_bytes()) for name in plan.STABLE_FILES}}
    bootstrap.ready.write_text(json.dumps(proposed))
    (bootstrap.stable / 'recover').write_bytes(bootstrap.recover_bytes())
    bootstrap.cron.parent.mkdir(parents=True, exist_ok=True)
    bootstrap.cron.write_bytes(bootstrap.cron_bytes())
    for service in plan.SERVICES:
        path = bootstrap.dropin(service)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(bootstrap.dropin_bytes())
    return bootstrap
