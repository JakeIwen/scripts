"""Coordinate package checks without letting one unavailable source erase another's evidence."""
import time
from threading import Thread

from dep_guard_cache import Cache
from dep_guard_commands import parse_commands
from dep_guard_lookup import existence_facts, reputation_facts
from dep_guard_network import DEADLINE_SECONDS, Network
from dep_guard_osv import lookup_advisories
from dep_guard_sources import public_source
from dep_guard_verdict import Downloads, Existence, Metadata, verdict

MAX_NAMES = 20


def select_requests(command, cwd, env, home, deadline):
    requests, notes = {}, set()
    for inv in parse_commands(command, cwd):
        if time.monotonic() >= deadline:
            notes.add('not checked: install check deadline reached')
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


def advisory_results(out, keys, net, cache, pins):
    try:
        out.update(lookup_advisories(keys, net, cache, pins=pins))
    except Exception:
        # A registry verdict still applies when a separate advisory source fails.
        pass


def combined_evidence(requests, net, cache):
    overrides = {k for k, invs in requests.items() if any(i.override for i in invs)}
    presence = existence_facts(requests, overrides, net, cache)
    public = [k for k in requests if presence.get(k) is Existence.FOUND]
    advisories, stats, metadata = {}, {}, {}
    if public:
        pins = {k: {i.pin for i in requests[k] if i.pin} for k in public}
        # Independent destinations start together. Only Network runs transport
        # workers; its bounded map lets coordinators finish at the shared deadline.
        osv = Thread(target=advisory_results, args=(advisories, public, net, cache, pins), daemon=True)
        osv.start()
        try:
            stats, metadata = reputation_facts(public, overrides, net, cache)
        except Exception:
            pass
        osv.join()
    return presence, stats, metadata, advisories


def hook_result(blocks, notes):
    if blocks:
        text = 'Dependency install blocked:\n' + '\n'.join(dict.fromkeys(blocks + notes))
        return {'hookEventName': 'PreToolUse', 'permissionDecision': 'deny', 'permissionDecisionReason': text}
    if notes:
        return {'hookEventName': 'PreToolUse', 'additionalContext': '\n'.join(dict.fromkeys(notes))}
    return None


def check_command(command, cwd, env, home, transport):
    deadline = time.monotonic() + DEADLINE_SECONDS
    requests, notes = select_requests(command, cwd, env, home, deadline)
    if not requests:
        return hook_result([], notes)
    cache = Cache(home)
    net = Network(cache, transport=transport, trace=env.get('DEP_INSTALL_GUARD_TRACE'),
                  seconds=max(0, deadline - time.monotonic()))
    presence, stats, meta, advisories = combined_evidence(requests, net, cache)
    blocks = []
    now = time.time()
    for key, invs in requests.items():
        for inv in invs:
            deny, warn = verdict(inv, presence.get(key, Existence.UNKNOWN), stats.get(key, Downloads()),
                                 meta.get(key, Metadata()), advisories.get(key), now)
            label = '%s%s: ' % (inv.name, '@' + inv.pin if inv.pin else '')
            blocks.extend(label + d.replace('=<name>', '=' + inv.name) for d in deny)
            notes.extend(label + w for w in warn)
    if net.cooldown_hosts:
        notes.append('Network check skipped during cooldown: ' + ', '.join(net.cooldown_hosts))
    result = hook_result(blocks, notes)
    try:
        cache.save(max_wait=min(0.5, net.remaining()))
    except OSError:
        pass  # Persistence must never alter a decision already made.
    return result
