import assert from 'node:assert/strict';
import test from 'node:test';

import {gaugeGeometry, linePoints, nearestByX, pathData, rangeWindow, scheduleLayout, spanText, stateBands, timeToX, xToTime} from '../static/charts.mjs';

const WINDOW = {from: 1000, to: 2000, width: 100};

test('rangeWindow ends now and spans 24 h or 7 d', () => {
  assert.deepEqual(rangeWindow('24h', 100000), {from: 100000 - 86400, to: 100000});
  assert.equal(rangeWindow('7d', 1_000_000).to - rangeWindow('7d', 1_000_000).from, 7 * 86400);
  assert.throws(() => rangeWindow('1y', 0), RangeError);
});

test('timeToX and xToTime are inverse and clamp to the window', () => {
  assert.equal(timeToX(1500, WINDOW), 50);
  assert.equal(timeToX(500, WINDOW), 0);
  assert.equal(timeToX(9999, WINDOW), 100);
  assert.equal(xToTime(25, WINDOW), 1250);
});

test('stateBands holds each sample until the next and merges equal neighbours', () => {
  const bands = stateBands([
    {ts: 1000, level: 'ok', text: 'running'},
    {ts: 1200, level: 'ok', text: 'running'},
    {ts: 1500, level: 'error', text: 'exit 1'},
  ], WINDOW);
  assert.deepEqual(bands.map(band => [band.level, band.start, band.end, band.x, band.width]),
    [['ok', 1000, 1500, 0, 50], ['error', 1500, 2000, 50, 50]]);
});

test('stateBands shows missing history as no data, never as the previous state', () => {
  const bands = stateBands([{ts: 1300, level: 'ok'}, {ts: 1400, level: 'warn'}], {...WINDOW, maxGap: 100});
  assert.deepEqual(bands.map(band => [band.level, band.start, band.end]),
    [['nodata', 1000, 1300], ['ok', 1300, 1400], ['warn', 1400, 1500], ['nodata', 1500, 2000]]);
  assert.deepEqual(stateBands([], WINDOW).map(band => band.level), ['nodata']);
});

test('stateBands clips samples that start before the window and drops ones after it', () => {
  const bands = stateBands([{ts: 900, level: 'ok'}, {ts: 1600, level: 'warn'}, {ts: 2500, level: 'error'}], WINDOW);
  assert.deepEqual(bands.map(band => [band.level, band.start, band.end]), [['ok', 1000, 1600], ['warn', 1600, 2000]]);
});

test('linePoints scales into the padded box, skips non-finite values and breaks the line at gaps', () => {
  const plot = linePoints([
    {ts: 1000, value: 0}, {ts: 1100, value: 10}, {ts: 1200, value: null}, {ts: 1900, value: 5}, {ts: 3000, value: 99},
  ], {...WINDOW, height: 28, pad: 4, maxGap: 300});
  assert.equal(plot.points.length, 3);
  assert.deepEqual([plot.points[0].y, plot.points[1].y], [24, 4]);
  assert.equal(plot.segments.length, 2);
  assert.equal(pathData(plot.segments[0]), 'M0 24 L10 4');
  assert.equal(plot.min, 0);
  assert.equal(plot.max, 10);
});

test('linePoints survives flat and empty data', () => {
  const flat = linePoints([{ts: 1000, value: 5}, {ts: 1500, value: 5}], {...WINDOW, height: 28, pad: 4});
  assert.ok(flat.points.every(point => Number.isFinite(point.y)));
  assert.deepEqual(linePoints([], {...WINDOW, height: 28}).segments, []);
  assert.equal(linePoints([{ts: 1000, value: 5}], {...WINDOW, height: 28, min: 0, max: 10, pad: 0}).points[0].y, 14);
});

test('nearestByX picks the closest point', () => {
  const points = [{x: 0}, {x: 40}, {x: 90}];
  assert.equal(nearestByX(points, 50).x, 40);
  assert.equal(nearestByX(points, 80).x, 90);
  assert.equal(nearestByX([], 5), null);
});

test('gaugeGeometry clamps and grades the amount left against the spend line', () => {
  assert.deepEqual(gaugeGeometry({value: 60, max: 100, marker: 40}, 200), {fraction: 0.6, fill: 120, markerX: 80, level: 'ok'});
  assert.equal(gaugeGeometry({value: 35, max: 100, marker: 50}, 100).level, 'warn');
  assert.equal(gaugeGeometry({value: 10, max: 100}, 100).level, 'error');
  assert.equal(gaugeGeometry({value: 250, max: 100}, 100).fill, 100);
  assert.equal(gaugeGeometry({value: 5, max: 0}, 100).fraction, 0);
  assert.equal(gaugeGeometry({value: 50, max: 100}, 100).markerX, null);
});

test('scheduleLayout groups events into lanes, marks the future and places now', () => {
  const layout = scheduleLayout([
    {ts: 1800, lane: 'b'}, {ts: 1100, lane: 'a'}, {ts: 1600, lane: 'a'}, {ts: 5000, lane: 'a'},
  ], {...WINDOW, now: 1500});
  assert.deepEqual(layout.lanes.map(lane => [lane.lane, lane.ticks.map(tick => [tick.x, tick.future])]),
    [['a', [[10, false], [60, true]]], ['b', [[80, true]]]]);
  assert.equal(layout.nowX, 50);
  assert.equal(scheduleLayout([], {...WINDOW, now: 9999}).nowX, null);
  assert.deepEqual(scheduleLayout([], {...WINDOW, now: 1500, laneOrder: ['x']}).lanes, [{lane: 'x', ticks: []}]);
});

test('spanText picks a readable unit', () => {
  assert.deepEqual([30, 600, 7200, 259200].map(spanText), ['30 s', '10 min', '2 h', '3 d']);
});
