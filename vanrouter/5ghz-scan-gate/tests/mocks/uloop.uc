'use strict';

let state = global.DAEMON_TEST;
let now_ms = 0;
let next_id = 1;
let timers = [];

function trace(message)
{
	print(`TRACE uloop ${message}\n`);
}

function next_due(limit)
{
	let selected = null;
	for (let entry in timers) {
		if (!entry.active || entry.due > limit)
			continue;
		if (!selected || entry.due < selected.due ||
		    entry.due == selected.due && entry.id < selected.id)
			selected = entry;
	}
	return selected;
}

function drain(limit)
{
	while (true) {
		let entry = next_due(limit);
		if (!entry)
			break;
		now_ms = entry.due;
		entry.active = false;
		entry.callback();
	}
	now_ms = limit;
}

export function init()
{
	trace("init");
	return 0;
};

export function timer(delay, callback)
{
	delay = int(delay);
	trace(`timer ${delay}`);
	let entry = {
		id: next_id++,
		due: now_ms + delay,
		active: true,
		callback,
	};
	push(timers, entry);
	return {
		cancel: () => { entry.active = false; },
		remaining: () => entry.active
			? (entry.due > now_ms ? entry.due - now_ms : 0)
			: 0,
	};
};

export function signal(name, callback)
{
	trace(`signal ${name}`);
	return { delete: () => true };
};

export function end()
{
	trace("end");
};

export function run()
{
	trace("run");
	/* Reconcile, settle DISCONNECT, and verify the fallback AP. */
	drain(1200);
	let startup = state.api.status.call({});
	print(`RESULT startup ${sprintf("%J", startup)}\n`);

	/* Exercise the published API and all three 500 ms release attempts. */
	let accepted = state.api.resume_stock.call({});
	print(`RESULT resume_accept ${sprintf("%J", accepted)}\n`);
	drain(3000);
	let released = state.api.status.call({});
	print(`RESULT release ${sprintf("%J", released)}\n`);
};
