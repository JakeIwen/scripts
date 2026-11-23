"""Route eligibility, per-invocation decisions and the Bash hook's structured response."""
import time

from dep_guard_cache import Cache
from dep_guard_commands import parse_commands
from dep_guard_deadline import DEADLINE_NOTE
from dep_guard_network import Network
from dep_guard_osv import cached_advisories, lookup_advisories
from dep_guard_schedule import PackageChecks
from dep_guard_sources import public_source
from dep_guard_verdict import Downloads, verdict

MAX_NAMES = 20


def select_requests(command, cwd, env, home, budget):
    requests, notes = {}, set()
    for inv in parse_commands(command, cwd, env=env, home=home):
        if not budget.remaining():
            notes.add(DEADLINE_NOTE)
            break
        if inv.local:
            continue  # Running a local executable is not a package lookup.
        key = (inv.ecosystem, inv.name)
        if len(requests) >= MAX_NAMES and key not in requests:
            notes.add('not checked: too many package names (limit 20)')
            continue
        if not public_source(inv, env, home):
            notes.add('not checked: private/offline index or indeterminate configuration')
            continue
        requests.setdefault(key, []).append(inv)
    return requests, sorted(notes)


def hook_result(blocks, notes):
    if blocks:
        text = 'Dependency install blocked:\n' + '\n'.join(dict.fromkeys(blocks + notes))
        return {'hookEventName': 'PreToolUse', 'permissionDecision': 'deny', 'permissionDecisionReason': text}
    if notes:
        return {'hookEventName': 'PreToolUse', 'additionalContext': '\n'.join(dict.fromkeys(notes))}
    return None


def decision(requests, checks, notes):
    blocks, notes = [], list(notes)
    now = time.time()
    for key, invs in requests.items():
        for inv in invs:
            facts = checks.facts[key][inv.override]
            deny, warn = verdict(inv, facts.existence, facts.stats or Downloads(), facts.meta,
                                 checks.advisories.get(key), now)
            label = '%s%s: ' % (inv.name, '@' + inv.pin if inv.pin else '')
            blocks.extend(label + d.replace('=<name>', '=' + inv.name) for d in deny)
            notes.extend(label + w for w in warn)
    return hook_result(blocks, notes)


def check_command(command, cwd, env, home, transport, budget):
    requests, notes = select_requests(command, cwd, env, home, budget)
    if not requests or not budget.remaining():
        return hook_result([], notes if budget.remaining() else notes + [DEADLINE_NOTE])
    cache = Cache(home, env=env)
    if not budget.remaining():
        return hook_result([], notes + [DEADLINE_NOTE])
    # Give the coordinator time to publish and persist completed checks after a
    # socket times out. The enclosing deadline also covers both local operations.
    remaining = budget.remaining()
    net = Network(cache, transport=transport, trace=env.get('DEP_INSTALL_GUARD_TRACE'),
                  seconds=remaining - min(0.05, remaining / 10))
    checks = PackageChecks(requests, net, cache, cached_advisories, lookup_advisories)
    budget.publish(decision(requests, checks, notes))
    if not checks.run(lambda: budget.publish(decision(requests, checks, notes))):
        notes.append(DEADLINE_NOTE)
    if net.cooldown_hosts:
        notes.append('Network check skipped during cooldown: ' + ', '.join(net.cooldown_hosts))
    result = decision(requests, checks, notes)
    budget.publish(result)
    try:
        cache.save(max_wait=min(0.5, budget.remaining()))
    except OSError:
        pass  # Persistence must never alter a decision already made.
    return result
