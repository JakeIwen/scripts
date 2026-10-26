"""The capacity-retry backoff formula; attempt numbers start at one."""


def delay(attempt, settings, rng):
    """Return a capped exponential delay plus positive-only fractional jitter."""
    if attempt < 1:
        raise ValueError("attempt must be at least one")
    # OpenAI rate-limit guidance: delays are minimums; add randomness and bound retries/time.
    # Unmerged openai/codex PR #31058 proposes 30s / 2m / 5m; settings keep policy editable.
    nominal = min(settings["max_seconds"],
                  settings["base_seconds"] * settings["factor"] ** (attempt - 1))
    return nominal * (1 + rng.uniform(0, settings["jitter"]))
