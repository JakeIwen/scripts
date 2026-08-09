'use strict';

import { create } from "apsta_scan_gate.policy";

let current_test = "initialization";
let test_count = 0;

function fail(message)
{
	warn(`service policy test failed [${current_test}]: ${message}\n`);
	exit(1);
}

function assert_true(value, message)
{
	if (!value)
		fail(message);
}

function assert_equal(actual, expected, message)
{
	if (actual != expected)
		fail(`${message}: got ${sprintf("%J", actual)}, expected ${sprintf("%J", expected)}`);
}

function run_test(name, callback)
{
	current_test = name;
	callback();
	test_count++;
	printf(`ok %d - %s\n`, test_count, name);
}

function make_clock()
{
	let now_ms = 0;
	let next_id = 1;
	let entries = [];

	function timer(delay, callback)
	{
		delay = int(delay);
		if (delay < 0)
			fail(`negative timer delay ${delay}`);

		let entry = {
			id: next_id++,
			due: now_ms + delay,
			active: true,
			callback,
		};
		let handle = {
			cancel: () => { entry.active = false; },
			remaining: () => entry.active
				? (entry.due > now_ms ? entry.due - now_ms : 0)
				: 0,
		};
		entry.handle = handle;
		push(entries, entry);
		return handle;
	}

	function next_due(limit)
	{
		let selected = null;
		for (let entry in entries) {
			if (!entry.active || entry.due > limit)
				continue;
			if (!selected || entry.due < selected.due ||
			    entry.due == selected.due && entry.id < selected.id)
				selected = entry;
		}
		return selected;
	}

	function advance(delay)
	{
		delay = int(delay);
		if (delay < 0)
			fail(`cannot move fake clock backwards by ${delay}`);

		let target = now_ms + delay;
		while (true) {
			let entry = next_due(target);
			if (!entry)
				break;

			now_ms = entry.due;
			entry.active = false;
			entry.callback();
		}
		now_ms = target;
	}

	function active_count()
	{
		let count = 0;
		for (let entry in entries)
			if (entry.active)
				count++;
		return count;
	}

	function latest_handle()
	{
		return length(entries) ? entries[length(entries) - 1].handle : null;
	}

	function invoke_even_if_cancelled(handle)
	{
		for (let entry in entries) {
			if (entry.handle != handle)
				continue;
			entry.callback();
			return true;
		}
		return false;
	}

	return {
		timer,
		advance,
		active_count,
		latest_handle,
		invoke_even_if_cancelled,
		now: () => now_ms,
	};
}

function make_environment(initial_state)
{
	let clock = make_clock();
	let current_state = initial_state ?? "DISCONNECTED";
	let current_frequency = current_state == "COMPLETED" ? 5200 : null;
	let actions = [];
	let phases = [];
	let logs = [];
	let ap = { up: false, frequency: null };
	let ap_results = { up: [], down: [] };
	let verify_results = [];
	let control_results = {};
	let apply_control_state = true;

	function record(action)
	{
		action.at = clock.now();
		push(actions, action);
	}

	function take(queue, fallback)
	{
		return length(queue) ? shift(queue) : fallback;
	}

	let adapter = {
		timer: clock.timer,
		get_state: () => current_state,
		get_frequency: () => current_frequency,
		set_ap: (up, frequency) => {
			record({ kind: "set_ap", up, frequency });
			let result = take(up ? ap_results.up : ap_results.down, true);
			if (result) {
				ap.up = up;
				ap.frequency = up ? frequency : null;
			}
			return result;
		},
		verify_ap: (frequency) => {
			record({ kind: "verify_ap", frequency });
			return take(verify_results,
				ap.up && ap.frequency == frequency);
		},
		control: (command) => {
			record({ kind: "control", command });
			let queue = control_results[command] ?? [];
			let result = take(queue, true);
			if (result && apply_control_state && command == "DISCONNECT")
				current_state = "DISCONNECTED";
			else if (result && apply_control_state && command == "RECONNECT")
				current_state = "SCANNING";
			return result;
		},
		log: (level, message) => {
			push(logs, { at: clock.now(), level, message });
		},
		phase: (phase) => {
			push(phases, { at: clock.now(), phase });
		},
	};

	return {
		clock,
		adapter,
		actions,
		phases,
		logs,
		ap,
		set_state: (state) => { current_state = state; },
		get_state: () => current_state,
		set_frequency: (frequency) => { current_frequency = frequency; },
		set_control_state_effects: (enabled) => { apply_control_state = enabled; },
		queue_ap: (up, result) => {
			push(up ? ap_results.up : ap_results.down, result);
		},
		queue_verify: (result) => { push(verify_results, result); },
		queue_control: (command, result) => {
			control_results[command] ??= [];
			push(control_results[command], result);
		},
	};
}

function make_gate(env, overrides)
{
	return create(env.adapter, {
		retry_interval: 60,
		scan_timeout: 15,
		fallback_frequency: 5745,
		...(overrides ?? {}),
	});
}

function action_count(env, kind, value)
{
	let count = 0;
	for (let action in env.actions) {
		if (action.kind != kind)
			continue;
		if (value != null && kind == "set_ap" && action.up != value)
			continue;
		if (value != null && kind == "control" && action.command != value)
			continue;
		count++;
	}
	return count;
}

function last_action(env)
{
	return length(env.actions) ? env.actions[length(env.actions) - 1] : null;
}

function assert_no_timers(gate, message)
{
	assert_equal(length(gate.status().timers), 0, message);
}

function park_from_disconnected(env, gate)
{
	assert_true(gate.start("DISCONNECTED"), "startup park was rejected");
	env.clock.advance(500);
	env.clock.advance(500);
	assert_equal(gate.status().phase, "parked", "startup did not reach parked");
}

run_test("option defaults and clamps", () => {
	let env = make_environment();
	let gate = create(env.adapter, { fallback_frequency: 5745 });
	let status = gate.status();
	assert_equal(status.retry_interval, 60, "default retry interval");
	assert_equal(status.scan_timeout, 15, "default scan timeout");
	assert_equal(status.fallback_frequency, 5745, "fallback frequency");

	gate = make_gate(env, { retry_interval: 1, scan_timeout: 1 });
	status = gate.status();
	assert_equal(status.retry_interval, 15, "minimum retry clamp");
	assert_equal(status.scan_timeout, 5, "minimum timeout clamp");

	gate = make_gate(env, { retry_interval: 99999, scan_timeout: 999 });
	status = gate.status();
	assert_equal(status.retry_interval, 3600, "maximum retry clamp");
	assert_equal(status.scan_timeout, 60, "maximum timeout clamp");
});

run_test("stable startup states are nondisruptive", () => {
	for (let state, expected in {
		COMPLETED: "connected",
		INACTIVE: "inactive",
		INTERFACE_DISABLED: "disabled",
	}) {
		let env = make_environment(state);
		let gate = make_gate(env);
		assert_true(gate.start(state), `start rejected ${state}`);
		assert_equal(gate.status().phase, expected, `${state} phase`);
		assert_equal(length(env.actions), 0, `${state} caused a radio mutation`);
		assert_no_timers(gate, `${state} left timers armed`);
	}
});

run_test("stale scan events cannot override completed state", () => {
	for (let event in [
		"CTRL-EVENT-SCAN-STARTED reason=background",
		"CTRL-EVENT-SCAN-RESULTS id=0",
	]) {
		let env = make_environment("COMPLETED");
		let gate = make_gate(env);
		gate.start("COMPLETED");
		assert_true(gate.handle_ctrl_event(event), `${event} was rejected`);
		assert_equal(gate.status().phase, "connected",
			`${event} overrode completed phase`);
		assert_equal(length(env.actions), 0,
			`${event} caused an AP or station mutation`);
		assert_no_timers(gate, `${event} armed a timer while completed`);
	}
});

run_test("stale scan-start cannot open a window while inactive", () => {
	let env = make_environment("INACTIVE");
	let gate = make_gate(env);
	gate.start("INACTIVE");
	assert_true(gate.handle_ctrl_event("CTRL-EVENT-SCAN-STARTED reason=background"),
		"inactive scan-start event was rejected");
	assert_equal(gate.status().phase, "inactive",
		"inactive scan-start opened a connecting window");
	assert_equal(length(env.actions), 0,
		"inactive scan-start caused an AP or station mutation");
	assert_no_timers(gate, "inactive scan-start armed a timer");
});

run_test("startup park verifies AP before scheduling retry", () => {
	let env = make_environment("DISCONNECTED");
	let gate = make_gate(env);
	assert_true(gate.start("DISCONNECTED"), "disconnected startup was rejected");
	assert_equal(gate.status().phase, "parking", "initial parking phase");
	assert_equal(env.actions[0].kind, "set_ap", "first action kind");
	assert_equal(env.actions[0].up, false, "first action did not stop AP");
	assert_equal(env.actions[1].command, "DISCONNECT", "DISCONNECT ordering");
	assert_equal(gate.status().timers.settle, 500, "settle timer");

	env.clock.advance(499);
	assert_equal(action_count(env, "set_ap", true), 0,
		"fallback AP started before disconnect settle");
	env.clock.advance(1);
	assert_equal(gate.status().phase, "verifying", "AP verification phase");
	let action = last_action(env);
	assert_true(action.kind == "set_ap" && action.up && action.frequency == 5745,
		"fallback AP start parameters");
	assert_equal(gate.status().timers.verify, 500, "AP verification timer");

	env.clock.advance(499);
	assert_equal(action_count(env, "verify_ap"), 0, "AP verified too early");
	env.clock.advance(1);
	assert_equal(gate.status().phase, "parked", "verified park phase");
	assert_equal(action_count(env, "verify_ap"), 1, "AP verification count");
	assert_equal(gate.status().timers.retry, 60000, "retry timer");
	assert_true(env.ap.up && env.ap.frequency == 5745, "fallback AP state");
});

run_test("retry boundary and command ordering are deterministic", () => {
	let env = make_environment("DISCONNECTED");
	let gate = make_gate(env);
	park_from_disconnected(env, gate);
	let before = length(env.actions);

	env.clock.advance(59999);
	assert_equal(length(env.actions), before, "retry fired before deadline");
	env.clock.advance(1);
	assert_equal(gate.status().phase, "connecting", "retry phase");
	assert_equal(env.actions[before].kind, "set_ap", "retry first action kind");
	assert_equal(env.actions[before].up, false, "retry did not stop AP first");
	assert_equal(env.actions[before + 1].command, "BSS_FLUSH 0", "BSS flush order");
	assert_equal(env.actions[before + 2].command, "RECONNECT", "reconnect order");
	assert_equal(gate.status().timers.watchdog, 15000, "retry watchdog");

	env.clock.advance(100);
	assert_true(gate.handle_state("SCANNING"), "duplicate scanning state rejected");
	assert_true(gate.handle_ctrl_event("CTRL-EVENT-SCAN-STARTED"),
		"duplicate scan event rejected");
	assert_equal(gate.status().timers.watchdog, 14900,
		"duplicate events reset the hard watchdog");
	assert_equal(env.clock.active_count(), 1, "duplicate events created a timer");
});

run_test("scan-results grace yields to association and completion", () => {
	let env = make_environment("SCANNING");
	let gate = make_gate(env);
	assert_true(gate.start("SCANNING"), "scan startup rejected");
	assert_true(gate.handle_ctrl_event("CTRL-EVENT-SCAN-RESULTS"),
		"scan results rejected");
	assert_equal(gate.status().timers.grace, 1000, "result grace timer");

	env.clock.advance(999);
	env.set_state("ASSOCIATING");
	assert_true(gate.handle_state("ASSOCIATING"), "association state rejected");
	assert_true(gate.status().timers.grace == null, "association left grace armed");
	env.set_state("COMPLETED");
	assert_true(gate.handle_state("COMPLETED"), "completion rejected");
	assert_equal(gate.status().phase, "connected", "completion phase");
	assert_no_timers(gate, "completion left timers armed");
	assert_equal(action_count(env, "control", "DISCONNECT"), 0,
		"successful association was disconnected");
});

run_test("late scan results cannot re-arm grace after association starts", () => {
	for (let state in [ "AUTHENTICATING", "ASSOCIATING" ]) {
		let env = make_environment("SCANNING");
		let gate = make_gate(env);
		gate.start("SCANNING");
		env.set_state(state);
		assert_true(gate.handle_state(state), `${state} state was rejected`);
		assert_true(gate.handle_ctrl_event("CTRL-EVENT-SCAN-RESULTS id=0"),
			`${state} late scan-results event was rejected`);
		assert_true(gate.status().timers.grace == null,
			`${state} late scan-results event re-armed grace`);
		env.clock.advance(1000);
		assert_equal(gate.status().phase, "connecting",
			`${state} late scan results parked an active association`);
		assert_equal(action_count(env, "control", "DISCONNECT"), 0,
			`${state} late scan results disconnected an active association`);
	}
});

run_test("late no-network event cannot override association state", () => {
	let env = make_environment("SCANNING");
	let gate = make_gate(env);
	gate.start("SCANNING");
	assert_true(gate.handle_ctrl_event("CTRL-EVENT-SCAN-RESULTS"),
		"initial scan-results event was rejected");
	assert_equal(gate.status().timers.grace, 1000, "initial result grace");

	env.set_state("ASSOCIATING");
	assert_true(gate.handle_state("ASSOCIATING"), "association state was rejected");
	assert_true(gate.status().timers.grace == null,
		"association did not cancel pre-existing result grace");
	assert_true(gate.handle_ctrl_event("CTRL-EVENT-NETWORK-NOT-FOUND id=0"),
		"late no-network event was rejected");
	assert_true(gate.status().timers.grace == null,
		"late no-network event re-armed immediate park");
	env.clock.advance(1000);
	assert_equal(gate.status().phase, "connecting",
		"late no-network event parked an active association");
	assert_equal(action_count(env, "control", "DISCONNECT"), 0,
		"late no-network event disconnected an active association");
});

run_test("authoritative association state defeats scan-results without state event", () => {
	let env = make_environment("SCANNING");
	let gate = make_gate(env);
	gate.start("SCANNING");

	/* Model polling observing AUTHENTICATING before iface.state is delivered. */
	env.set_state("AUTHENTICATING");
	assert_true(gate.handle_ctrl_event("CTRL-EVENT-SCAN-RESULTS id=0"),
		"scan-results event was rejected");
	assert_equal(gate.status().phase, "connecting",
		"authoritative authentication state changed phase");
	assert_true(gate.status().timers.grace == null,
		"scan results armed grace despite authoritative authentication");
	env.clock.advance(1000);
	assert_equal(action_count(env, "control", "DISCONNECT"), 0,
		"scan results disconnected authoritative authentication");
});

run_test("network-not-found parks after one fake millisecond", () => {
	let env = make_environment("SCANNING");
	let gate = make_gate(env);
	gate.start("SCANNING");
	gate.handle_ctrl_event("CTRL-EVENT-NETWORK-NOT-FOUND");
	assert_equal(gate.status().timers.grace, 1, "immediate park timer");
	env.clock.advance(0);
	assert_equal(action_count(env, "control", "DISCONNECT"), 0,
		"park ran in the event callback");
	env.clock.advance(1);
	assert_equal(gate.status().phase, "parking", "network-not-found phase");
	assert_equal(action_count(env, "control", "DISCONNECT"), 1,
		"network-not-found disconnect count");
});

run_test("watchdog is a hard scan bound", () => {
	let env = make_environment("SCANNING");
	let gate = make_gate(env);
	gate.start("SCANNING");
	env.clock.advance(14999);
	assert_equal(action_count(env, "control", "DISCONNECT"), 0,
		"watchdog fired early");
	env.clock.advance(1);
	assert_equal(gate.status().phase, "parking", "watchdog parking phase");
	assert_equal(action_count(env, "control", "DISCONNECT"), 1,
		"watchdog did not issue disconnect");
	assert_equal(gate.status().park_reason, "scan watchdog expired",
		"watchdog park reason");
});

run_test("late completion during disconnect settle wins", () => {
	let env = make_environment("SCANNING");
	let gate = make_gate(env);
	gate.start("SCANNING");
	gate.handle_ctrl_event("CTRL-EVENT-NETWORK-NOT-FOUND");
	env.clock.advance(1);
	env.set_state("COMPLETED");
	env.clock.advance(500);
	assert_equal(gate.status().phase, "connected", "late completion phase");
	assert_equal(action_count(env, "set_ap", true), 0,
		"fallback AP started after completion");
	assert_no_timers(gate, "late completion left timers armed");
});

run_test("three failed disconnects fault without phantom AP", () => {
	let env = make_environment("SCANNING");
	for (let i = 0; i < 3; i++)
		env.queue_control("DISCONNECT", false);
	let gate = make_gate(env);
	gate.start("SCANNING");
	gate.handle_ctrl_event("CTRL-EVENT-NETWORK-NOT-FOUND");
	env.clock.advance(1);
	for (let i = 0; i < 5; i++)
		env.clock.advance(500);

	let status = gate.status();
	assert_equal(status.phase, "fault", "disconnect failure phase");
	assert_equal(status.disconnect_attempts, 3, "disconnect attempt limit");
	assert_equal(action_count(env, "control", "DISCONNECT"), 3,
		"DISCONNECT call count");
	assert_equal(action_count(env, "set_ap", true), 0,
		"failed disconnect advertised fallback AP");
	assert_equal(action_count(env, "control", "RECONNECT"), 1,
		"fault did not release stock reconnect");
	assert_true(status.last_error != null, "fault lacks diagnostic");
	assert_no_timers(gate, "fault left timers armed");
});

run_test("AP stop rejection faults and cancels watchdog", () => {
	let env = make_environment("SCANNING");
	env.queue_ap(false, false);
	let gate = make_gate(env);
	assert_true(!gate.start("SCANNING"), "rejected AP stop reported success");
	assert_equal(gate.status().phase, "fault", "AP stop failure phase");
	assert_equal(action_count(env, "control", "RECONNECT"), 1,
		"AP stop fault did not resume stock");
	assert_no_timers(gate, "AP stop fault left watchdog armed");
});

run_test("AP start and AP verification failures fail closed", () => {
	let env = make_environment("DISCONNECTED");
	env.queue_ap(true, false);
	let gate = make_gate(env);
	gate.start("DISCONNECTED");
	env.clock.advance(500);
	assert_equal(gate.status().phase, "fault", "AP start failure phase");
	assert_equal(action_count(env, "verify_ap"), 0,
		"rejected AP start was verified");
	assert_equal(action_count(env, "control", "RECONNECT"), 1,
		"AP start fault did not resume stock");

	env = make_environment("DISCONNECTED");
	env.queue_verify(false);
	gate = make_gate(env);
	gate.start("DISCONNECTED");
	env.clock.advance(1000);
	assert_equal(gate.status().phase, "fault", "AP verify failure phase");
	assert_true(!env.ap.up, "unverified fallback AP was not stopped");
	assert_equal(action_count(env, "control", "RECONNECT"), 1,
		"AP verify fault did not resume stock");
});

run_test("station activity during AP verification removes fallback AP", () => {
	let env = make_environment("DISCONNECTED");
	let gate = make_gate(env);
	gate.start("DISCONNECTED");
	env.clock.advance(500);
	assert_true(env.ap.up, "fallback AP was not started for verification");
	env.set_state("SCANNING");
	env.clock.advance(500);
	assert_true(!env.ap.up, "active scan left fallback AP up");
	assert_equal(gate.status().phase, "disconnect-retry",
		"station activity did not return to disconnect verification");
	assert_equal(gate.status().timers.settle, 500, "disconnect retry timer");
});

run_test("failed BSS flush and reconnect return to parking", () => {
	for (let command in [ "BSS_FLUSH 0", "RECONNECT" ]) {
		let env = make_environment("DISCONNECTED");
		let gate = make_gate(env);
		park_from_disconnected(env, gate);
		env.queue_control(command, false);
		assert_true(!gate.retry_now(), `${command} failure reported success`);
		assert_equal(gate.status().phase, "parking", `${command} failure phase`);
		assert_true(gate.status().timers.watchdog == null,
			`${command} failure left watchdog armed`);
		assert_equal(gate.status().timers.settle, 500,
			`${command} failure did not begin safe park`);
		assert_true(!env.ap.up, `${command} failure left fallback AP up while parking`);
	}
});

run_test("detach invalidates stale timer callbacks", () => {
	let env = make_environment("SCANNING");
	let gate = make_gate(env);
	gate.start("SCANNING");
	let stale = env.clock.latest_handle();
	gate.detach();
	let before = length(env.actions);
	assert_true(env.clock.invoke_even_if_cancelled(stale), "stale timer was unavailable");
	assert_equal(length(env.actions), before, "stale timer mutated detached gate");
	assert_equal(gate.status().phase, "detached", "detach phase");
	assert_no_timers(gate, "detach left timers armed");
});

run_test("release preserves fallback AP and returns ownership to stock", () => {
	let env = make_environment("DISCONNECTED");
	let gate = make_gate(env);
	park_from_disconnected(env, gate);
	let ap_down_before = action_count(env, "set_ap", false);
	env.queue_control("RECONNECT", false);
	assert_true(!gate.release(), "rejected stock reconnect reported success");
	assert_equal(gate.status().phase, "stopped", "release phase");
	assert_true(env.ap.up && env.ap.frequency == 5745,
		"release removed recovery AP after reconnect rejection");
	assert_equal(action_count(env, "set_ap", false), ap_down_before,
		"release stopped fallback AP before reconnect");
	assert_no_timers(gate, "release left timers armed");
});

run_test("release reconnects an intentionally parked inactive station", () => {
	let env = make_environment("DISCONNECTED");
	let gate = make_gate(env);
	park_from_disconnected(env, gate);
	env.set_state("INACTIVE");
	assert_true(gate.handle_state("INACTIVE"), "parked INACTIVE state was rejected");
	assert_equal(gate.status().phase, "parked",
		"parked INACTIVE state discarded intentional park ownership");
	assert_true(gate.release(), "parked INACTIVE release failed");
	assert_equal(action_count(env, "control", "RECONNECT"), 1,
		"parked INACTIVE release omitted RECONNECT");
	assert_equal(gate.status().phase, "stopped", "parked INACTIVE release phase");
	assert_no_timers(gate, "parked INACTIVE release left retry armed");
});

run_test("connected AP audit repairs at the station frequency", () => {
	let env = make_environment("COMPLETED");
	env.set_frequency(5200);
	let gate = make_gate(env);
	gate.start("COMPLETED");
	let before = length(env.actions);

	assert_true(!gate.audit(), "missing connected AP reported healthy");
	assert_equal(gate.status().phase, "connected-repair", "connected repair phase");
	assert_true(env.actions[before].kind == "verify_ap" &&
		env.actions[before].frequency == 5200,
		"connected repair did not verify station frequency first");
	assert_true(env.actions[before + 1].kind == "set_ap" &&
		env.actions[before + 1].up && env.actions[before + 1].frequency == 5200,
		"connected repair did not start AP at station frequency second");
	assert_equal(gate.status().timers.verify, 500, "connected repair verify timer");
	assert_equal(action_count(env, "control", "RECONNECT"), 0,
		"connected repair issued reconnect before verification");

	env.clock.advance(499);
	assert_equal(length(env.actions), before + 2, "connected repair verified too early");
	env.clock.advance(1);
	assert_equal(gate.status().phase, "connected", "connected repair success phase");
	assert_true(env.actions[before + 2].kind == "verify_ap" &&
		env.actions[before + 2].frequency == 5200,
		"connected repair did not verify repaired AP");
	assert_equal(action_count(env, "control", "RECONNECT"), 0,
		"successful connected repair resumed stock");
	assert_no_timers(gate, "successful connected repair left verify timer");
});

run_test("failed connected AP repair stops AP before resuming stock", () => {
	let env = make_environment("COMPLETED");
	env.set_frequency(5200);
	env.queue_verify(false);
	env.queue_verify(false);
	let gate = make_gate(env);
	gate.start("COMPLETED");
	let before = length(env.actions);

	assert_true(!gate.audit(), "missing connected AP reported healthy");
	env.clock.advance(500);
	let status = gate.status();
	assert_equal(status.phase, "fault", "connected repair failure phase");
	assert_equal(status.last_error,
		"connected AP repair did not verify at the station frequency",
		"connected repair failure diagnostic");
	assert_true(env.actions[before].kind == "verify_ap" &&
		env.actions[before].frequency == 5200,
		"failed repair action 1 was not initial verification");
	assert_true(env.actions[before + 1].kind == "set_ap" &&
		env.actions[before + 1].up && env.actions[before + 1].frequency == 5200,
		"failed repair action 2 was not AP start");
	assert_true(env.actions[before + 2].kind == "verify_ap" &&
		env.actions[before + 2].frequency == 5200,
		"failed repair action 3 was not delayed verification");
	assert_true(env.actions[before + 3].kind == "set_ap" &&
		!env.actions[before + 3].up,
		"failed repair action 4 did not stop unverified AP");
	assert_true(env.actions[before + 4].kind == "control" &&
		env.actions[before + 4].command == "RECONNECT",
		"failed repair action 5 did not resume stock");
	assert_true(!env.ap.up, "failed connected repair left AP advertised up");
	assert_no_timers(gate, "failed connected repair left timer armed");
});

run_test("parked AP loss audit faults and resumes stock without retry", () => {
	let env = make_environment("DISCONNECTED");
	let gate = make_gate(env);
	park_from_disconnected(env, gate);
	env.ap.up = false;
	assert_true(!gate.audit(), "missing parked AP passed audit");
	let status = gate.status();
	assert_equal(status.phase, "fault", "parked AP loss audit phase");
	assert_equal(status.last_error,
		"fallback AP disappeared while the station was parked",
		"parked AP loss diagnostic");
	assert_equal(action_count(env, "control", "RECONNECT"), 1,
		"parked AP loss did not resume stock");
	assert_equal(action_count(env, "verify_ap"), 2,
		"parked AP loss audit verification count");
	assert_no_timers(gate, "parked AP loss retained retry timer");
	assert_true(!gate.retry_now(), "faulted AP-loss policy accepted retry");
});

run_test("control events accept the real prefix form", () => {
	let env = make_environment("SCANNING");
	let gate = make_gate(env);
	gate.start("SCANNING");
	assert_true(gate.handle_ctrl_event("CTRL-EVENT-SCAN-RESULTS id=0"),
		"scan-results event with payload was rejected");
	assert_equal(gate.status().timers.grace, 1000,
		"payload-bearing scan results did not arm grace");
	assert_true(gate.handle_ctrl_event("CTRL-EVENT-NETWORK-NOT-FOUND id=0"),
		"network-not-found event with payload was rejected");
	assert_equal(gate.status().timers.grace, 1,
		"payload-bearing network-not-found did not shorten grace");
});

run_test("accepted disconnect still requires authoritative parked state", () => {
	let env = make_environment("SCANNING");
	env.set_control_state_effects(false);
	let gate = make_gate(env);
	gate.start("SCANNING");
	gate.handle_ctrl_event("CTRL-EVENT-NETWORK-NOT-FOUND");
	env.clock.advance(1);
	for (let i = 0; i < 5; i++)
		env.clock.advance(500);

	assert_equal(action_count(env, "control", "DISCONNECT"), 3,
		"accepted-but-unverified disconnect retry count");
	assert_equal(gate.status().phase, "fault",
		"accepted-but-unverified disconnect did not fault");
	assert_equal(action_count(env, "set_ap", true), 0,
		"unverified parked state advertised fallback AP");
});

run_test("runtime disabled and inactive states cancel connecting window", () => {
	for (let state, expected in {
		INACTIVE: "inactive",
		INTERFACE_DISABLED: "disabled",
	}) {
		let env = make_environment("SCANNING");
		let gate = make_gate(env);
		gate.start("SCANNING");
		let watchdog = env.clock.latest_handle();
		assert_true(gate.handle_state(state), `${state} transition rejected`);
		assert_equal(gate.status().phase, expected, `${state} runtime phase`);
		assert_no_timers(gate, `${state} runtime transition left timers`);
		let before = length(env.actions);
		env.clock.invoke_even_if_cancelled(watchdog);
		assert_equal(length(env.actions), before,
			`${state} stale watchdog mutated policy`);
	}
});

run_test("late scan-start is inert while station is being or remains parked", () => {
	for (let target in [ "parking", "verifying", "parked" ]) {
		let env = make_environment("DISCONNECTED");
		let gate = make_gate(env);
		gate.start("DISCONNECTED");
		if (target == "verifying")
			env.clock.advance(500);
		else if (target == "parked")
			env.clock.advance(1000);

		assert_equal(gate.status().phase, target, `${target} setup phase`);
		let ap_down_before = action_count(env, "set_ap", false);
		assert_true(gate.handle_ctrl_event("CTRL-EVENT-SCAN-STARTED reason=stale"),
			`${target} stale scan-start was rejected`);
		assert_equal(gate.status().phase, target,
			`${target} stale scan-start changed phase`);
		assert_equal(action_count(env, "set_ap", false), ap_down_before,
			`${target} stale scan-start stopped AP again`);
		assert_true(gate.status().timers.watchdog == null,
			`${target} stale scan-start armed watchdog`);
	}
});

run_test("manual retry cancels scheduled retry generation", () => {
	let env = make_environment("DISCONNECTED");
	let gate = make_gate(env);
	park_from_disconnected(env, gate);
	let scheduled_retry = env.clock.latest_handle();
	assert_true(gate.retry_now(), "manual retry was rejected");
	let before = length(env.actions);
	assert_true(env.clock.invoke_even_if_cancelled(scheduled_retry),
		"cancelled scheduled retry was unavailable");
	assert_equal(length(env.actions), before,
		"cancelled scheduled retry duplicated a reconnect");
	assert_equal(action_count(env, "control", "RECONNECT"), 1,
		"manual retry reconnect count");
});

run_test("fault can recover only through authoritative completion", () => {
	let env = make_environment("SCANNING");
	env.queue_ap(false, false);
	let gate = make_gate(env);
	gate.start("SCANNING");
	let before = length(env.actions);
	assert_true(!gate.handle_state("SCANNING"), "fault handled another scan");
	assert_true(!gate.handle_ctrl_event("CTRL-EVENT-SCAN-STARTED"),
		"fault handled another ctrl event");
	assert_true(!gate.retry_now(), "fault accepted manual retry");
	assert_equal(length(env.actions), before, "fault allowed a radio mutation");

	env.set_state("COMPLETED");
	assert_true(gate.handle_state("COMPLETED"), "fault rejected completion");
	assert_equal(gate.status().phase, "connected", "fault completion phase");
	assert_true(gate.status().last_error == null,
		"completion did not clear fault diagnostic");
});

run_test("release behavior follows authoritative state", () => {
	for (let state in [ "COMPLETED", "INACTIVE", "INTERFACE_DISABLED" ]) {
		let env = make_environment(state);
		let gate = make_gate(env);
		gate.start(state);
		assert_true(gate.release(), `${state} release failed`);
		assert_equal(action_count(env, "control", "RECONNECT"), 0,
			`${state} release issued reconnect`);
		assert_equal(gate.status().phase, "stopped", `${state} release phase`);
	}

	let env = make_environment("SCANNING");
	let gate = make_gate(env);
	gate.start("SCANNING");
	assert_true(gate.release(), "scanning release did not reconnect");
	assert_equal(action_count(env, "control", "RECONNECT"), 1,
		"scanning release reconnect count");
	assert_no_timers(gate, "scanning release left watchdog armed");
});

printf(`service policy cases: %d passed\n`, test_count);
