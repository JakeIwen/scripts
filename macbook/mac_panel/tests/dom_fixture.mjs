// Small DOM boundary: retain real event dispatch, confirmation handlers, fetch and section state.
export class Element extends EventTarget {
  constructor(tag = 'div') {
    super();
    this.tagName = tag.toUpperCase();
    this.childNodes = [];
    this.dataset = {};
    this.classes = new Set();
    this.classList = {add: name => this.classes.add(name), remove: name => this.classes.delete(name), contains: name => this.classes.has(name)};
    this.style = {setProperty: (key, value) => { this[key] = value; }};
    this.offsetWidth = 80;
    this.isConnected = true;
  }
  replaceChildren(...children) { this.childNodes = children; }
  append(...children) { this.childNodes.push(...children); }
  set textContent(value) { this.childNodes = [String(value)]; }
  get textContent() { return this.childNodes.map(child => child.textContent ?? child).join(''); }
  querySelectorAll() { return []; }
  querySelector() { return null; }
  contains(element) { return this.childNodes.includes(element); }
  closest() { return null; }
  focus() { document.activeElement = this; }
  click() { if (!this.disabled) this.dispatchEvent(new Event('click')); }
}

const roots = new Map();
export let searchTarget = null;
export const setSearchTarget = target => { searchTarget = target; };
globalThis.CSS = {escape: text => text};
globalThis.matchMedia = () => ({});
globalThis.document = {
  activeElement: null,
  createElement: tag => new Element(tag),
  createTextNode: text => String(text),
  getElementById: id => roots.get(id),
  querySelector(selector) {
    if (selector.startsWith('meta')) return {content: 'fixture-token'};
    if (selector.includes('[data-key=')) return searchTarget;
    const [id, part] = selector.split(' ');
    return roots.get(id.slice(1))?.parts[part] ?? null;
  },
};
export function section(id, field) {
  const root = new Element();
  root.parts = {'.notice': new Element(), '.meta': new Element(), '.content': new Element()};
  root.querySelectorAll = () => field ? [field] : [];
  root.childNodes = field ? [field] : [];
  roots.set(id, root);
  return root;
}
