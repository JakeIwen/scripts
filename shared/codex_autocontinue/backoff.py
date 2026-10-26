"""The capacity-retry backoff formula; attempt numbers start at one."""


def delay(attempt, settings, rng):
    """Return a linearly growing delay with symmetric fractional jitter."""
    if attempt < 1:
        raise ValueError("attempt must be at least one")
    # Linear, not exponential (Jacob's choice): 30 s, 60 s, 90 s ... with the defaults.
    # Jitter keeps sessions that failed together from retrying in lockstep.
    nominal = settings["base_seconds"] + settings["step_seconds"] * (attempt - 1)
    return nominal * (1 + rng.uniform(-settings["jitter"], settings["jitter"]))
