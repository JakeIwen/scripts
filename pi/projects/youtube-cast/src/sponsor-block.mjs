import { createHash } from 'node:crypto';
import { config } from './config.mjs';

export async function fetchSegments(videoId, { fetcher = fetch,
  categories = config.sponsorBlockCategories, signal, log = console } = {}) {
  const digest = createHash('sha256').update(videoId).digest('hex');
  const url = new URL(`${config.sponsorBlockUrl}/${digest.slice(0, 4)}`);
  url.searchParams.set('categories', JSON.stringify(categories));
  const timeout = AbortSignal.timeout(5000);
  try {
    const response = await fetcher(url, {
      signal: signal ? AbortSignal.any([signal, timeout]) : timeout,
    });
    if (response.status === 404) return [];
    if (!response.ok) throw new Error('SponsorBlock request failed');
    const records = await response.json();
    if (!Array.isArray(records)) throw new Error('Invalid SponsorBlock response');
    const record = records.find(item => item?.videoID === videoId);
    if (!Array.isArray(record?.segments)) return [];
    return record.segments.filter(item => categories.includes(item.category)
      && (!item.actionType || item.actionType === 'skip')
      && Array.isArray(item.segment) && item.segment.length === 2
      && item.segment.every(Number.isFinite)
      && item.segment[0] >= 0 && item.segment[1] > item.segment[0])
      .map(item => item.segment).sort((a, b) => a[0] - b[0]);
  } catch {
    if (!signal?.aborted) log.warn('SponsorBlock unavailable; continuing without skips');
    return [];
  }
}

// Merge overlapping segments at seek time, without skipping gaps or repeat seeks.
export function skipTarget(position, segments) {
  let target = position;
  for (const [start, end] of segments) {
    if (target >= start && target < end) target = end;
  }
  return target > position ? target : null;
}

export class SponsorSkipper {
  constructor(segments = []) {
    this.segments = segments;
    this.pendingTarget = null;
  }

  async tick(position, seek) {
    if (this.pendingTarget !== null) return false;
    const target = skipTarget(position, this.segments);
    if (target === null) return false;
    this.pendingTarget = target;
    try { return await seek(target); }
    finally { this.pendingTarget = null; }
  }
}
