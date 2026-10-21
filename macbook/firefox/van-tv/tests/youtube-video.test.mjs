import assert from 'node:assert/strict';
import test from 'node:test';
import { isVideoId, parseStartTime, parseYouTubeVideo, youtubeMenuPatterns } from '../youtube-video.mjs';

const id = 'dQw4w9WgXcQ';

test('all supported URL shapes preserve the ID and decode timestamp forms', () => {
  const cases = [
    [`https://www.youtube.com/watch?v=${id}&list=PLtest&t=75`, 75],
    [`http://youtube.com/watch?feature=share&v=${id}&t=1h2m3s`, 3723],
    [`https://m.youtube.com/watch?v=${id}#t=2m`, 120],
    [`https://youtu.be/${id}?si=tracking&t=1m30s`, 90],
    [`https://www.youtu.be/${id}/?t=4.5`, 4.5],
    [`https://www.youtube.com/shorts/${id}?feature=share`, 0],
    [`https://www.youtube.com/live/${id}?t=90s`, 90],
    [`https://www.youtube.com/embed/${id}?start=13`, 13],
    [`https://www.youtube.com/embed/${id}/?start=13&t=4`, 4],
  ];
  for (const [url, position] of cases) assert.deepEqual(parseYouTubeVideo(url), { videoId: id, position });
});

test('rejects lookalike hosts, unrelated pages, credentials and malformed video IDs', () => {
  const urls = [
    '', null, 'not a URL', id,
    `https://www.youtube.com.evil.test/watch?v=${id}`,
    `https://evil.test/watch?v=${id}`,
    `https://www.youtube.com@evil.test/watch?v=${id}`,
    `https://user:secret@www.youtube.com/watch?v=${id}`,
    `ftp://www.youtube.com/watch?v=${id}`,
    `https://www.youtube.com:444/watch?v=${id}`,
    `https://www.youtube.com/playlist?list=${id}`,
    `https://youtu.be/${id}/extra`, `https://www.youtube.com/shorts/${id}/extra`,
    `https://www.youtube.com/watch?v=${id}a`,
    'https://www.youtube.com/watch?v=short', 'https://www.youtube.com/watch',
    'https://www.youtube.com/watch?v=abcdefghij%2F',
  ];
  for (const url of urls) assert.equal(parseYouTubeVideo(url), null, String(url));
  assert.equal(isVideoId('a_-12345678'), true);
  assert.equal(isVideoId(undefined), false);
});

test('timestamp parsing is nonnegative and finite with invalid inputs starting at zero', () => {
  for (const value of ['', '-1', 'Infinity', '1e3', 'abc', '1:30', '2s1m', 'h', null, '9'.repeat(400)]) {
    assert.equal(parseStartTime(value), 0, String(value));
  }
  assert.equal(parseStartTime('2H3M4.5S'), 7384.5);
  assert.equal(parseStartTime('90s'), 90);
  assert.equal(parseStartTime('0'), 0);
  assert.deepEqual(parseYouTubeVideo(`https://youtu.be/${id}?t=-1`), { videoId: id, position: 0 });
});

test('context menu patterns include the supported video routes and watch pages only', () => {
  const patterns = youtubeMenuPatterns();
  assert.ok(patterns.link.includes('*://youtu.be/*'));
  for (const path of ['watch?*', 'shorts/*', 'live/*', 'embed/*']) {
    assert.ok(patterns.link.includes(`*://www.youtube.com/${path}`));
  }
  assert.deepEqual(patterns.page, [
    '*://www.youtube.com/watch?*', '*://youtube.com/watch?*', '*://m.youtube.com/watch?*',
  ]);
});
