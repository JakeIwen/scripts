// How the backend came by a session's name (`name_source` on a session group). Only Claude's own registry record proves
// a name belongs to that process; every other source (a Codex name is always a guess or a fallback) is shown as such.
export const NameSource = Object.freeze({REGISTRY: 'registry', ROLLOUT_TIME: 'rollout-time', FALLBACK: 'fallback'});
export const nameUnconfirmed = group => group.name_source !== NameSource.REGISTRY;
