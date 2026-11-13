// Move every iTerm2 window onto one display and tile them side by side, each full height,
// together filling the display's whole width (menu bar and Dock excluded).
//
//   osascript -l JavaScript iterm_tile.js external   # the connected monitor
//   osascript -l JavaScript iterm_tile.js builtin    # the MacBook's own display
//   ... iterm_tile.js external --dry-run              # print the plan, move nothing
//
// Windows already on that display are re-tiled too. They keep their left-to-right order,
// and windows arriving from another display join to their right in their own order.
// Hotkey and minimized windows are left alone; iTerm is never launched. With several
// external monitors, `external` picks the largest. BTT's Media > Mac submenu runs this
// (install_mac_menu.py); the pure geometry below is tested in Node.
'use strict';

const TARGETS = ['external', 'builtin'];

// screens: [{name, builtin, frame, visible}] in Cocoa coordinates (origin at the bottom
// left of the primary screen, y up); the primary screen comes first, as in NSScreen.screens.
function chooseScreen(screens, target) {
    if (!TARGETS.includes(target)) throw new Error('Target must be external or builtin, not ' + target);
    const wanted = screens.filter(screen => screen.builtin === (target === 'builtin'));
    const area = screen => screen.frame.width * screen.frame.height;
    return wanted.reduce((best, screen) => (!best || area(screen) > area(best) ? screen : best), null);
}

// AppleScript window bounds use the top left of the primary screen as origin, y down.
function topLeft(rect, primaryHeight) {
    return {x: rect.x, y: primaryHeight - (rect.y + rect.height), width: rect.width, height: rect.height};
}

// count columns across rect (already top-left based); the last one takes the rounding remainder.
function columns(rect, count) {
    const width = Math.floor(rect.width / count);
    return Array.from({length: count}, (_, index) => ({
        x: Math.round(rect.x) + index * width, y: Math.round(rect.y),
        width: index === count - 1 ? Math.round(rect.width) - index * width : width,
        height: Math.round(rect.height),
    }));
}

function contains(rect, point) {
    return point.x >= rect.x && point.x < rect.x + rect.width && point.y >= rect.y && point.y < rect.y + rect.height;
}

// windows: [{id, bounds}] (top-left based). Those whose centre is on the target display
// come first, then the rest; each group left to right, ties by id so reruns are stable.
function tileOrder(windows, targetRect) {
    const centre = bounds => ({x: bounds.x + bounds.width / 2, y: bounds.y + bounds.height / 2});
    const rank = win => (contains(targetRect, centre(win.bounds)) ? 0 : 1);
    return windows.slice().sort((a, b) => rank(a) - rank(b) || a.bounds.x - b.bounds.x || a.id - b.id);
}

function plan(screens, windows, target) {
    const screen = chooseScreen(screens, target);
    if (!screen) return {screen: null, moves: []};
    const primaryHeight = screens[0].frame.height;
    const area = topLeft(screen.visible, primaryHeight);
    const ordered = tileOrder(windows, topLeft(screen.frame, primaryHeight));
    const slots = columns(area, Math.max(ordered.length, 1));
    return {screen, moves: ordered.map((win, index) => ({id: win.id, bounds: slots[index]}))};
}

// Place every window; when some refuse (full screen), re-plan without them so the rest
// still fill the width. place(id, bounds) returns whether the window landed there.
function tile(screens, windows, target, place) {
    let candidates = windows;
    const stuck = [];
    for (;;) {
        const {moves} = plan(screens, candidates, target);
        const refused = moves.filter(move => !place(move.id, move.bounds)).map(move => move.id);
        if (!refused.length) return {placed: candidates.map(win => win.id), stuck};
        stuck.push(...refused);
        candidates = candidates.filter(win => !refused.includes(win.id));
    }
}

function readScreens() {
    ObjC.import('AppKit');
    ObjC.import('CoreGraphics');
    const rect = value => ({x: value.origin.x, y: value.origin.y, width: value.size.width, height: value.size.height});
    const all = $.NSScreen.screens;
    const screens = [];
    for (let index = 0; index < all.count; index++) {
        const screen = all.objectAtIndex(index);
        const display = ObjC.unwrap(screen.deviceDescription.objectForKey('NSScreenNumber'));
        screens.push({name: ObjC.unwrap(screen.localizedName), builtin: Boolean($.CGDisplayIsBuiltin(display)),
            frame: rect(screen.frame), visible: rect(screen.visibleFrame)});
    }
    return screens;
}

function near(a, b) {
    return ['x', 'y', 'width', 'height'].every(key => Math.abs(a[key] - b[key]) <= 24);
}

function notify(message) {
    const app = Application.currentApplication();
    app.includeStandardAdditions = true;
    app.displayNotification(message, {withTitle: 'iTerm tile'});
}

function run(argv) {
    const target = argv[0];
    if (!TARGETS.includes(target)) throw new Error('usage: iterm_tile.js external|builtin');
    const iterm = Application('com.googlecode.iterm2');
    if (!iterm.running()) return 'iTerm is not running.';
    const windows = iterm.windows().filter(win => !win.isHotkeyWindow() && !win.miniaturized())
        .map(win => ({id: win.id(), bounds: win.bounds(), ref: win}));
    const screens = readScreens();
    const screen = chooseScreen(screens, target);
    if (!screen) {
        const message = target === 'external' ? 'No external monitor is connected.' : 'No built-in display found.';
        notify(message);
        return message;
    }
    const dryRun = argv[1] === '--dry-run';
    const planned = [];
    const refs = new Map(windows.map(win => [win.id, win.ref]));
    const move = (id, bounds) => {
        const win = refs.get(id);
        try {
            win.bounds = bounds;
            // A move between displays of different sizes can be clamped against the old
            // display on the first try; the second lands it.
            if (!near(win.bounds(), bounds)) win.bounds = bounds;
            return near(win.bounds(), bounds);
        } catch (error) {
            return false; // a native full-screen window refuses new bounds
        }
    };
    const record = (id, bounds) => planned.push([id, bounds]) > 0;
    const {placed, stuck} = tile(screens, windows, target, dryRun ? record : move);
    if (dryRun) return JSON.stringify({screen: screen.name, moves: planned});
    const summary = 'Tiled ' + placed.length + ' iTerm window(s) on ' + screen.name +
        (stuck.length ? '; ' + stuck.length + ' could not be placed (full screen?)' : '') + '.';
    if (stuck.length) notify(summary);
    return summary;
}

if (typeof module !== 'undefined') module.exports = {chooseScreen, topLeft, columns, tileOrder, plan, tile};
