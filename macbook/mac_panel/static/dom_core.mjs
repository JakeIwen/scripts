// No style attributes or inline <style>; dynamic geometry uses CSS custom properties set from JS.
// The lowest DOM layer: element factory, the two-click confirm and the plain button. Imports nothing.
export const PLACEHOLDER = '—';
const ARM_SECONDS = 8;
const armers = new WeakMap();

/** Idempotently prepare a registered confirmation without dispatching an action click. */
export function arm(target) {
  armers.get(target)?.();
}

export function node(tag, text, className) {
  const result = document.createElement(tag);
  if (text !== undefined && text !== null) result.textContent = String(text);
  if (className) result.className = className;
  return result;
}

/** The one confirm wording: a verb phrase after "Confirm: ". */
export const confirmText = phrase => `Confirm: ${phrase}`;

/**
 * Makes `target` (a button) two-click: the first click swaps `label`'s content for `text` and stops
 * there; the second click passes through to later listeners. Escape, blur or 8 s disarm it. Call it
 * before adding the click handler, since it relies on running first.
 */
export function armed(target, text, label = target) {
  let isArmed = false;
  let timer = null;
  let original = [];
  const disarm = () => {
    if (!isArmed) return;
    isArmed = false;
    clearTimeout(timer);
    label.replaceChildren(...original);
    target.classList.remove('armed');
  };
  const prepare = () => {
    if (isArmed || target.disabled) return;
    isArmed = true;
    original = [...label.childNodes];
    target.style.setProperty('--confirm-width', `${target.offsetWidth}px`);
    label.replaceChildren(text);
    target.classList.add('armed');
    timer = setTimeout(disarm, ARM_SECONDS * 1000);
  };
  armers.set(target, prepare);
  target.addEventListener('click', event => {
    if (isArmed) {
      disarm();
      return;
    }
    event.stopImmediatePropagation();
    prepare();
  });
  target.addEventListener('keydown', event => { if (event.key === 'Escape') disarm(); });
  target.addEventListener('blur', disarm);
}

export function button(text, onClick, options = {}) {
  const {confirm, key, className, disabled = false, title} = options;
  const result = node('button', text, className);
  result.type = 'button';
  if (key) result.dataset.key = key;
  if (title) result.title = title;
  result.disabled = disabled;
  if (confirm) armed(result, confirm);
  result.addEventListener('click', async () => {
    result.disabled = true;
    try { await onClick(); } finally { result.disabled = disabled; }
  });
  return result;
}
