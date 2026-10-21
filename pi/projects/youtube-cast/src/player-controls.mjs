import { Constants } from 'yt-cast-receiver';
import { PlayerStatus } from './mpv-player.mjs';

// Every action senders without a lounge session can send (the vantv command,
// the Firefox popup, the Pi's vlcmd/pp shell functions behind the BTT buttons).
export const CONTROL_ACTIONS = Object.freeze(['pause', 'resume', 'toggle', 'stop', 'next', 'previous', 'seek']);

// Returns `seek` arguments from a request body, or null when they are invalid:
// exactly one of an absolute `position` (>= 0) or a relative `offset`.
export function seekArguments({ position, offset }) {
  const hasPosition = position !== undefined;
  if (hasPosition === (offset !== undefined)) return null;
  if (hasPosition) return Number.isFinite(position) && position >= 0 ? { position } : null;
  return Number.isFinite(offset) ? { offset } : null;
}

function hasAutoplay(player) {
  return player.autoplayMode === Constants.AUTOPLAY_MODES.ENABLED && Boolean(player.queue.autoplay);
}

export function createPlayerControls(player) {
  return {
    pause: () => player.pause(),
    resume: () => player.resume(),
    toggle: () => {
      if (player.status === PlayerStatus.PLAYING) return player.pause();
      if (player.status === PlayerStatus.PAUSED) return player.resume();
      return false;
    },
    // Stop also clears the queue: the library replays a cast only when its
    // current video changes, so recasting the stopped video would do nothing.
    stop: async () => { await player.reset(); return true; },
    // The library stops playback when there's nothing to move to; a stray
    // Next (a direct cast has no queue) should leave the video playing, and
    // Previous restarts the video like a TV remote.
    next: () => (player.queue.hasNext || hasAutoplay(player) ? player.next() : false),
    previous: () => (player.queue.hasPrevious ? player.previous() : player.seek(0)),
    seek: ({ position, offset }) => {
      if (!player.video) return false;
      const end = player.duration > 0 ? player.duration : Infinity;
      const target = position ?? player.position + offset;
      return player.seek(Math.max(0, Math.min(end, target)));
    },
  };
}
