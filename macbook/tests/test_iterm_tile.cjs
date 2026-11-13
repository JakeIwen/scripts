const {test} = require('node:test');
const assert = require('node:assert/strict');
const {chooseScreen, topLeft, columns, tileOrder, plan, tile} = require('../scripts/iterm_tile.js');

// The desk layout seen live: a 2560x1440 monitor as the primary screen, the MacBook below it.
const monitor = {name: 'Monitor', builtin: false, frame: {x: 0, y: 0, width: 2560, height: 1440},
    visible: {x: 0, y: 0, width: 2560, height: 1410}};
const laptop = {name: 'Built-in', builtin: true, frame: {x: 0, y: -1117, width: 1728, height: 1117},
    visible: {x: 0, y: -1117, width: 1728, height: 1085}};
const small = {name: 'Small', builtin: false, frame: {x: 2560, y: 0, width: 1920, height: 1080},
    visible: {x: 2560, y: 0, width: 1920, height: 1050}};

test('picks the built-in display or the largest external one, and nothing when it is missing', () => {
    assert.equal(chooseScreen([monitor, laptop, small], 'external'), monitor);
    assert.equal(chooseScreen([laptop, small, monitor], 'external'), monitor);
    assert.equal(chooseScreen([monitor, laptop], 'builtin'), laptop);
    assert.equal(chooseScreen([laptop], 'external'), null);
    assert.throws(() => chooseScreen([laptop], 'left'), /external or builtin/);
});

test('converts Cocoa rectangles to window bounds measured from the primary screen\'s top left', () => {
    assert.deepEqual(topLeft(monitor.visible, 1440), {x: 0, y: 30, width: 2560, height: 1410});
    assert.deepEqual(topLeft(laptop.visible, 1440), {x: 0, y: 1472, width: 1728, height: 1085});
});

test('columns fill the whole width at full height, the last one taking the remainder', () => {
    const slots = columns({x: 0, y: 30, width: 2560, height: 1410}, 3);
    assert.deepEqual(slots.map(slot => [slot.x, slot.width]), [[0, 853], [853, 853], [1706, 854]]);
    assert.ok(slots.every(slot => slot.y === 30 && slot.height === 1410));
    assert.equal(slots.at(-1).x + slots.at(-1).width, 2560);
});

test('windows already on the target keep their order and arrivals join on the right', () => {
    const target = {x: 0, y: 0, width: 2560, height: 1440};
    const windows = [
        {id: 1, bounds: {x: 100, y: 1500, width: 800, height: 900}},   // on the laptop, left
        {id: 2, bounds: {x: 1300, y: 30, width: 1200, height: 1400}},  // on the monitor, right
        {id: 3, bounds: {x: 0, y: 30, width: 1200, height: 1400}},     // on the monitor, left
        {id: 4, bounds: {x: 50, y: 1500, width: 800, height: 900}},    // on the laptop, further left
    ];
    assert.deepEqual(tileOrder(windows, target).map(win => win.id), [3, 2, 4, 1]);
});

test('plans every window into one column of the chosen display', () => {
    const windows = [{id: 7, bounds: {x: 1280, y: 30, width: 1280, height: 1410}},
        {id: 5, bounds: {x: 0, y: 30, width: 1280, height: 1410}}];
    const down = plan([monitor, laptop], windows, 'builtin');
    assert.equal(down.screen, laptop);
    assert.deepEqual(down.moves, [{id: 5, bounds: {x: 0, y: 1472, width: 864, height: 1085}},
        {id: 7, bounds: {x: 864, y: 1472, width: 864, height: 1085}}]);
    const up = plan([monitor, laptop], windows, 'external');
    assert.deepEqual(up.moves.map(move => [move.id, move.bounds.x, move.bounds.width]), [[5, 0, 1280], [7, 1280, 1280]]);
    assert.deepEqual(plan([laptop], windows, 'external'), {screen: null, moves: []});
    assert.deepEqual(plan([monitor], [], 'external').moves, []);
});

test('a window that refuses to move gives its column to the windows that can', () => {
    const windows = [{id: 1, bounds: {x: 0, y: 30, width: 2560, height: 1440}},   // full screen
        {id: 2, bounds: {x: 1300, y: 30, width: 600, height: 1400}}];
    const landed = new Map();
    const place = (id, bounds) => id !== 1 && Boolean(landed.set(id, bounds));
    assert.deepEqual(tile([monitor, laptop], windows, 'external', place), {placed: [2], stuck: [1]});
    assert.deepEqual(landed.get(2), {x: 0, y: 30, width: 2560, height: 1410});
    assert.deepEqual(tile([monitor], windows, 'external', () => false), {placed: [], stuck: [1, 2]});
});
