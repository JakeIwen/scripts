// DOM helpers every chart renderer shares: SVG/HTML element factories, measured-width redraws and the
// one-tooltip-per-chart pattern. Colours come from classes in charts.css, never from here.
import {clamp} from './chart_scale.mjs';

const SVG_NS = 'http://www.w3.org/2000/svg';
const DEFAULT_WIDTH = 320;

export function svgNode(tag, attributes = {}, className) {
  const element = document.createElementNS(SVG_NS, tag);
  Object.entries(attributes).forEach(([name, value]) => element.setAttribute(name, value));
  if (className) element.setAttribute('class', className);
  return element;
}

export function html(tag, className, text) {
  const element = document.createElement(tag);
  if (className) element.className = className;
  if (text !== undefined) element.textContent = text;
  return element;
}

// Redraws at the measured width; the first draw uses a default so a detached chart still has content.
export function responsive(wrap, draw) {
  let last = 0;
  const run = width => {
    const rounded = Math.round(width);
    if (rounded < 40 || rounded === last) return;
    last = rounded;
    draw(rounded);
  };
  run(DEFAULT_WIDTH);
  if (typeof ResizeObserver !== 'undefined') new ResizeObserver(([entry]) => run(entry.contentRect.width)).observe(wrap);
}

const activeTips = new Set();
let tipDismissal = false;

// One tooltip per chart: value first, then the time or detail; hover for a mouse, click or tap for touch.
export function tooltip(wrap) {
  if (!tipDismissal) {
    tipDismissal = true;
    document.addEventListener('pointerdown', event => {
      activeTips.forEach(tip => { if (!tip.wrap.contains(event.target)) tip.hide(); });
    });
  }
  const box = html('div', 'chart-tip');
  box.hidden = true;
  box.setAttribute('role', 'status');
  wrap.append(box);
  const tip = {
    wrap,
    show(lines, x) {
      box.replaceChildren(...lines.map((line, index) => html('div', index ? 'chart-tip-detail' : 'chart-tip-value', line)));
      box.hidden = false;
      box.style.setProperty('--tip-left', `${clamp(x - box.offsetWidth / 2, 0, Math.max(0, wrap.clientWidth - box.offsetWidth))}px`);
      activeTips.add(tip);
    },
    hide() {
      box.hidden = true;
      activeTips.delete(tip);
    },
  };
  return tip;
}

// Mouse hover shows and leaving hides; touch and click show and stay until a tap elsewhere.
export function bindTip(target, tip, content) {
  const show = event => tip.show(content(), event.offsetX ?? 0);
  target.addEventListener('pointerenter', show);
  target.addEventListener('pointermove', show);
  target.addEventListener('pointerdown', show);
  target.addEventListener('pointerleave', event => { if (event.pointerType === 'mouse') tip.hide(); });
}

