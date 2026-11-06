import assert from 'node:assert/strict';
import test from 'node:test';

import {estimateTextWidth, labelMargins, lineChartLayout, recordingStart, tooltipRows, valuesAt} from '../static/line_chart.mjs';
import {labelAnchor, niceTicks, spreadLabels, timeTicks} from '../static/chart_scale.mjs';
import {gaugeGeometry, linePoints, nearestByX, pathData, rangeWindow, scheduleLayout, spanText, stateBands, stateGap, timeToX, xToTime} from '../static/charts.mjs';

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


test('state gap follows retained spacing but still marks a real outage', () => {
  assert.equal(stateGap(), 300);
  assert.equal(stateGap(60), 300);
  assert.equal(stateGap(600), 1800);
  const samples = [{ts: 0, level: 'ok'}, {ts: 600, level: 'ok'}, {ts: 3000, level: 'ok'}];
  const bands = stateBands(samples, {from: 0, to: 3600, width: 320, maxGap: stateGap(600)});
  assert.deepEqual(bands.filter(band => band.level === 'nodata').map(band => [band.start, band.end]), [[2400, 3000]]);
});

test('niceTicks rounds outwards to 1, 2, 2.5 or 5 steps with at most the asked count', () => {
  assert.deepEqual(niceTicks(0, 100, 5).ticks, [0, 25, 50, 75, 100]);
  assert.deepEqual(niceTicks(0, 120, 5), {ticks: [0, 50, 100, 150], step: 50, min: 0, max: 150});
  assert.deepEqual(niceTicks(0, 7, 5).ticks, [0, 2, 4, 6, 8]);
  assert.deepEqual(niceTicks(0, 1, 5).ticks, [0, 0.25, 0.5, 0.75, 1]);
  assert.deepEqual(niceTicks(0, 0.4, 5).ticks, [0, 0.1, 0.2, 0.3, 0.4]);
  assert.deepEqual(niceTicks(40, 60, 5).ticks, [40, 45, 50, 55, 60]);
  assert.deepEqual(niceTicks(0, 120e6, 5).ticks, [0, 50e6, 100e6, 150e6]);
});

test('niceTicks widens a flat or empty range instead of dividing by zero', () => {
  const flat = niceTicks(3, 3, 5);
  assert.ok(flat.min < 3 && flat.max > 3);
  assert.ok(niceTicks(NaN, NaN, 5).ticks.length >= 2);
  assert.deepEqual(niceTicks(100, 0, 5).ticks, [0, 25, 50, 75, 100]);
});

const local = (...parts) => new Date(...parts).getTime() / 1000;

test('timeTicks puts a 24 h axis on aligned local hours and marks midnight', () => {
  const from = local(2026, 9, 6, 10, 30);
  const ticks = timeTicks(from, from + 86400, 640);
  assert.ok(ticks.length >= 6 && ticks.length <= 13);
  ticks.forEach(tick => {
    const date = new Date(tick.ts * 1000);
    assert.equal(date.getMinutes(), 0);
    assert.equal(date.getHours() % 2, 0);
    assert.ok(tick.ts >= from && tick.ts <= from + 86400);
    assert.equal(tick.major, date.getHours() === 0);
  });
  assert.equal(ticks.filter(tick => tick.major).length, 1);
  assert.ok(timeTicks(from, from + 86400, 240).length < ticks.length);
});

test('timeTicks puts a 7 d axis on local midnights, thinning when narrow', () => {
  const from = local(2026, 9, 6, 15);
  const wide = timeTicks(from, from + 7 * 86400, 700);
  assert.equal(wide.length, 7);
  assert.ok(wide.every(tick => new Date(tick.ts * 1000).getHours() === 0 && tick.major));
  assert.ok(timeTicks(from, from + 7 * 86400, 200).length < 7);
});

test('labelAnchor keeps edge labels inside the chart', () => {
  assert.deepEqual([0, 100, 200].map(x => labelAnchor(x, 200, 5)), ['start', 'middle', 'end']);
});

test('spreadLabels separates crowded end labels but leaves room alone', () => {
  assert.deepEqual(spreadLabels([10, 50], 12, 0, 100), [10, 50]);
  const spread = spreadLabels([40, 41, 42], 12, 0, 100);
  assert.ok(spread[1] - spread[0] >= 12 - 0.01 && spread[2] - spread[1] >= 12 - 0.01);
  assert.ok(Math.max(...spreadLabels([99, 100], 12, 0, 100)) <= 100);
});

const LINE = {from: 0, to: 1000, width: 400, height: 180, yMin: 0, yMax: 100, now: 1000};
const points = (...pairs) => pairs.map(([ts, value]) => ({ts, value}));

test('recordingStart reports a first sample that is later than the window start', () => {
  const late = [{points: points([600, 5], [800, 6])}];
  assert.equal(recordingStart(late, 0, 1000), 600);
  assert.equal(recordingStart([{points: points([3, 5])}], 0, 1000), null);
  assert.equal(recordingStart([{points: points([0, 1])}, {points: points([700, 1])}], 0, 1000), null);
  assert.equal(recordingStart(late, 0, 1000, 500), 500);
  assert.equal(recordingStart([{points: []}], 0, 1000), null);
});

test('lineChartLayout maps values onto the plot and leaves nothing before the first sample', () => {
  const layout = lineChartLayout({...LINE, series: [{key: 'a', label: 'A', points: points([600, 100], [800, 50], [1000, 0])}]});
  const {plot} = layout;
  assert.deepEqual(layout.yTicks.map(tick => tick.value), [0, 25, 50, 75, 100]);
  assert.equal(layout.yTicks[0].y, plot.top + plot.height);
  assert.equal(layout.yTicks[4].y, plot.top);
  const [first, middle, last] = layout.series[0].points;
  assert.deepEqual([first.x, first.y], [plot.width * 0.6, 0]);
  assert.deepEqual([middle.x, middle.y], [plot.width * 0.8, plot.height / 2]);
  assert.deepEqual([last.x, last.y], [plot.width, plot.height]);
  assert.equal(layout.since, 600);
  assert.equal(layout.sinceX, plot.width * 0.6);
  assert.equal(layout.series[0].endText, '0');
  assert.ok(plot.left + plot.width < LINE.width);
});

test('lineChartLayout breaks the line at gaps and keeps series with no data empty', () => {
  const layout = lineChartLayout({...LINE, maxGap: 100, series: [
    {key: 'a', label: 'A', points: points([0, 10], [50, 20], [600, 30])},
    {key: 'b', label: 'B', points: []},
  ]});
  assert.equal(layout.series[0].segments.length, 2);
  assert.equal(layout.series[1].last, null);
  assert.equal(layout.since, null);
});

test('lineChartLayout fits the axis to the data when no range is given', () => {
  const layout = lineChartLayout({from: 0, to: 100, width: 300, height: 160, series: [{key: 'a', label: 'A', points: points([0, 3], [100, 118])}]});
  assert.deepEqual(layout.yTicks.map(tick => tick.value), [0, 50, 100, 150]);
});

test('valuesAt picks each series nearest sample and ignores time before recording started', () => {
  const layout = lineChartLayout({...LINE, series: [
    {key: 'a', label: 'A', points: points([600, 10], [900, 20])},
    {key: 'b', label: 'B', points: points([700, 30])},
  ]});
  const x = layout.plot.width * 0.85;
  const [a, b] = valuesAt(layout, x);
  assert.equal(a.ts, 900);
  assert.equal(b.ts, 700);
  assert.equal(valuesAt(layout, 0).every(point => point === null), true);
});

test('valuesAt never pairs a value from a distant timestamp with another series hover time', () => {
  const layout = lineChartLayout({...LINE, maxGap: 100, series: [
    {key: 'a', label: 'A', points: points([0, 10], [1000, 20])},
    {key: 'b', label: 'B', points: points([600, 30])},
  ]});
  const x = layout.plot.width * 0.6;
  const [a, b] = valuesAt(layout, x);
  assert.equal(a, null);
  assert.equal(b.ts, 600);
  const tip = tooltipRows(layout, x);
  assert.deepEqual(tip.rows.map(row => [row.label, row.point?.value ?? null]), [['A', null], ['B', 30]]);
  assert.equal(tip.ts, 600);
});

test('tooltipRows with aligned series shows every value at the rounded hover time', () => {
  const layout = lineChartLayout({...LINE, series: [
    {key: 'a', label: 'A', points: points([0, 10], [300, 20], [600, 30])},
    {key: 'b', label: 'B', points: points([0, 1], [300, 2], [600, 3])},
  ]});
  const tip = tooltipRows(layout, layout.plot.width * 0.31);
  assert.equal(tip.ts, 300);
  assert.deepEqual(tip.rows.map(row => [row.point.value, row.sampleTs]), [[20, null], [2, null]]);
  assert.equal(tooltipRows(layout, layout.plot.width * 0.9), null, 'past the last sample plus half a spacing there is nothing to show');
});

test('labelMargins never drop below the conservative estimate and use real measurements when given', () => {
  const estimated = labelMargins(['0%', '100%'], ['82%']);
  assert.ok(estimated.left >= estimateTextWidth('100%') + 8, 'left holds the widest y label');
  assert.ok(estimated.right >= estimateTextWidth('82%') + 10, 'right holds the end label and its gap');
  assert.ok(estimateTextWidth('100%') >= Math.ceil(4 * 0.65 * 12));
  const measured = labelMargins(['0%', '100%'], ['82%'], {y: 30, end: 20});
  assert.equal(measured.left, 30 + 8 + 4);
  assert.deepEqual(labelMargins(['100%'], ['1%'], {y: 0, end: NaN}), labelMargins(['100%'], ['1%']));
  const layout = lineChartLayout({...LINE, yFormat: value => `${value}%`, series: [{key: 'a', label: 'A', points: points([0, 100], [1000, 82])}]});
  assert.equal(layout.plot.left, estimated.left);
  assert.ok(layout.plot.left + layout.plot.width + estimated.right <= LINE.width);
});
