"""Pure cloud admission: cost approvals never replace valid transport evidence."""
from .evidence import normalize
from .rules import EVIDENCE_TTL, dendelion_verdict, effective, permits


def cloud_permits(raw, config, policy, previous, now, consumer_id):
    try:
        evidence = normalize(raw, config.home_network)
        verdict = dendelion_verdict(evidence, config, now)
        if verdict.metered is None:
            return False, verdict.reason
        fresh = previous is not None and 0 <= now - previous.evaluated_at <= EVIDENCE_TTL
        state = effective(verdict, policy, now, previous.effective if fresh else None)
        result = permits(consumer_id, state, policy, now)
        return result.permitted, result.reason
    except (ValueError, TypeError, KeyError, AttributeError):
        return False, 'incomplete uplink evidence'
