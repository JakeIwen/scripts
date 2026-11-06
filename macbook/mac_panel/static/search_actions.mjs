// What a search result's action button does: go to the row and get its button ready, never press it for you.
import {goTo} from './shell.mjs';

const WAIT_MS = 3000;
const POLL_MS = 100;
const RETRY_MS = 400;

/**
 * Opens `section` at `anchorId`, then focuses the control with data-key `key`. With `arm` (for a button that
 * asks for confirmation) it also makes the first click, which only arms "Confirm: ..."; the second click, the
 * one that acts, stays the reader's.
 */
export function stageAction(section, anchorId, key, {arm = false} = {}) {
  goTo(section, anchorId);
  const deadline = Date.now() + WAIT_MS;
  const attempt = () => {
    const target = document.querySelector(`#${CSS.escape(section)} [data-key="${CSS.escape(key)}"]`);
    if (target && !target.closest('[hidden]') && !target.disabled) {
      target.focus({preventScroll: true});
      if (arm) target.click();
      // A refresh that began before the click can replace the button; try once more if it vanished.
      if (arm) setTimeout(() => { if (!target.isConnected && Date.now() < deadline) attempt(); }, RETRY_MS);
    } else if (Date.now() < deadline) {
      setTimeout(attempt, POLL_MS);
    }
  };
  setTimeout(attempt, POLL_MS);
}
