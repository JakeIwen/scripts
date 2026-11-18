// How the backend came by a session's name (`name_source` on a session group). Only the first two prove the
// name belongs to that process; the others are guesses and are shown as such. Absent on older payloads.
export const NameSource = Object.freeze({REGISTRY: 'registry', ROLLOUT_OPEN: 'rollout-open', ROLLOUT_TIME: 'rollout-time', FALLBACK: 'fallback'});
export const UNPROVEN_NAME_SOURCES = Object.freeze([NameSource.ROLLOUT_TIME, NameSource.FALLBACK]);
