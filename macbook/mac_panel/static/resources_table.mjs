// The shared table with right-aligned number columns, so figures line up in one place for every Resources table.
import {table} from './panel_dom.mjs';

/** table() plus a `num` class on the header and cells of the given column indexes. */
export function numberTable(headers, rows, numeric) {
  const wrapper = table(headers, rows);
  wrapper.querySelectorAll('tr').forEach(row => {
    [...row.children ?? []].forEach((cell, index) => { if (numeric.includes(index)) cell.classList.add('num'); });
  });
  return wrapper;
}
