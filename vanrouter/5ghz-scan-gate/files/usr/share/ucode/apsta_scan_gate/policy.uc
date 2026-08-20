/*
 * Pure AP/STA scan-gate policy.
 *
 * This module deliberately has no ubus, uci or uloop imports.  The daemon
 * supplies a small adapter, which keeps the state machine executable under a
 * fake clock and makes every radio mutation observable in tests.
 */

const SETTLE_MS = 500;
const AP_VERIFY_MS = 500;
const RESULT_GRACE_MS = 1000;
const IMMEDIATE_MS = 1;
const DISCONNECT_LIMIT = 3;

function clamp_seconds(value, fallback, minimum, maximum)
{
	let seconds = int(value ?? fallback);

	if (seconds < minimum)
		return minimum;
	if (seconds > maximum)
		return maximum;

	return seconds;
}

function is_associating(state)
{
	return state in [
		"AUTHENTICATING", "ASSOCIATING", "ASSOCIATED",
		"4WAY_HANDSHAKE", "GROUP_HANDSHAKE"
	];
}

function is_parkable(state)
{
	return state in [ "DISCONNECTED", "INACTIVE", "INTERFACE_DISABLED" ];
}

function event_is(event, prefix)
{
	return type(event) == "string" && substr(event, 0, length(prefix)) == prefix;
}

export function create(adapter, options)
{
	let retry_ms = clamp_seconds(options?.retry_interval, 60, 15, 3600) * 1000;
	let timeout_ms = clamp_seconds(options?.scan_timeout, 15, 5, 60) * 1000;
	let fallback_frequency = int(options?.fallback_frequency);
	let phase = "idle";
	let generation = 1;
	let association_started = false;
	let disconnect_attempts = 0;
	let disconnect_ok = false;
	let last_error = null;
	let park_reason = null;
	let timers = {
		watchdog: null,
		settle: null,
		verify: null,
		retry: null,
		grace: null,
	};

	let begin_window;
	let request_park;
	let issue_disconnect;
	let retry_now;

	function log(level, message)
	{
		if (type(adapter?.log) == "function")
			adapter.log(level, message);
	}

	function set_phase(next)
	{
		if (phase == next)
			return;
		phase = next;
		if (type(adapter?.phase) == "function")
			adapter.phase(next);
	}

	function cancel_timer(name)
	{
		let timer = timers[name];
		if (!timer)
			return;

		timers[name] = null;
		timer.cancel();
	}

	function cancel_all()
	{
		generation++;
		for (let name in [ "watchdog", "settle", "verify", "retry", "grace" ])
			cancel_timer(name);
	}

	function schedule(name, delay, callback)
	{
		cancel_timer(name);
		let token = generation;
		let timer;

		timer = adapter.timer(delay, () => {
			if (token != generation || timers[name] != timer)
				return;

			timers[name] = null;
			callback();
		});
		timers[name] = timer;
		return timer;
	}

	function state()
	{
		return adapter.get_state();
	}

	function connected()
	{
		cancel_all();
		association_started = false;
		disconnect_attempts = 0;
		disconnect_ok = false;
		park_reason = null;
		last_error = null;
		set_phase("connected");
	}

	function fault(message)
	{
		cancel_all();
		last_error = message;
		set_phase("fault");
		log("error", message);

		/* Return ownership to stock OpenWrt rather than leaving STA suppressed. */
		if (!adapter.control("RECONNECT"))
			log("error", "stock reconnect was not accepted after a scan-gate fault");
	}

	function verify_park()
	{
		let current = state();
		if (current == "COMPLETED") {
			connected();
			return;
		}

		if (!is_parkable(current)) {
			/* Do not leave a fallback-channel AP up beside an active scan. */
			if (!adapter.set_ap(false, null)) {
				fault("could not stop the fallback AP after station state changed");
				return;
			}

			if (disconnect_attempts < DISCONNECT_LIMIT) {
				set_phase("disconnect-retry");
				schedule("settle", SETTLE_MS, issue_disconnect);
				return;
			}

			fault("station would not remain parked while verifying the fallback AP");
			return;
		}

		if (!adapter.verify_ap(fallback_frequency)) {
			/* Best effort only: the AP may be absent, or merely unverifiable. */
			adapter.set_ap(false, null);
			fault("fallback AP did not verify on the configured frequency");
			return;
		}

		set_phase("parked");
		schedule("retry", retry_ms, retry_now);
	}

	function settle_disconnect()
	{
		let current = state();
		if (current == "COMPLETED") {
			connected();
			return;
		}

		if (!disconnect_ok || !is_parkable(current)) {
			if (disconnect_attempts < DISCONNECT_LIMIT) {
				set_phase("disconnect-retry");
				schedule("settle", SETTLE_MS, issue_disconnect);
				return;
			}

			fault("station could not be safely parked after three attempts");
			return;
		}

		if (!adapter.set_ap(true, fallback_frequency)) {
			fault("fallback AP start was not accepted");
			return;
		}

		set_phase("verifying");
		schedule("verify", AP_VERIFY_MS, verify_park);
	}

	issue_disconnect = function()
	{
		if (phase in [ "fault", "stopped", "detached", "connected" ])
			return;

		if (state() == "COMPLETED") {
			connected();
			return;
		}

		set_phase("parking");
		disconnect_attempts++;
		disconnect_ok = adapter.control("DISCONNECT");
		schedule("settle", SETTLE_MS, settle_disconnect);
	};

	request_park = function(reason)
	{
		if (phase in [ "parking", "disconnect-retry", "verifying", "parked",
		               "fault", "stopped", "detached" ])
			return false;

		if (state() == "COMPLETED") {
			connected();
			return true;
		}

		cancel_all();
		park_reason = reason;
		association_started = false;
		disconnect_attempts = 0;
		disconnect_ok = false;

		/* Explicitly reinforce stock's AP-down action before DISCONNECT. */
		if (!adapter.set_ap(false, null)) {
			fault("AP stop was not accepted before parking the station");
			return false;
		}

		issue_disconnect();
		return true;
	};

	begin_window = function(reason)
	{
		if (phase in [ "fault", "stopped", "detached" ])
			return false;
		if (phase == "connecting")
			return true;

		cancel_all();
		park_reason = reason;
		association_started = false;
		disconnect_attempts = 0;
		disconnect_ok = false;
		set_phase("connecting");

		schedule("watchdog", timeout_ms, () => {
			if (state() == "COMPLETED")
				connected();
			else
				request_park("scan watchdog expired");
		});

		/* The hard bound exists before any operation can expose a scan window. */
		if (!adapter.set_ap(false, null)) {
			fault("AP stop was not accepted before opening a scan window");
			return false;
		}

		return true;
	};

	retry_now = function()
	{
		if (phase != "parked")
			return false;

		if (!begin_window("scheduled retry"))
			return false;

		if (!adapter.control("BSS_FLUSH 0")) {
			request_park("BSS flush was not accepted");
			return false;
		}

		if (!adapter.control("RECONNECT")) {
			request_park("reconnect was not accepted");
			return false;
		}

		return true;
	};

	function start(initial_state)
	{
		let current = initial_state ?? state();

		if (current == "COMPLETED") {
			connected();
			return true;
		}

		if (current == "INTERFACE_DISABLED") {
			cancel_all();
			set_phase("disabled");
			return true;
		}

		if (current == "INACTIVE") {
			cancel_all();
			set_phase("inactive");
			return true;
		}

		if (current == "DISCONNECTED")
			return request_park("startup reconciliation");

		if (!begin_window("startup reconciliation"))
			return false;

		if (is_associating(current))
			association_started = true;

		return true;
	}

	function handle_state(next)
	{
		if (next == "COMPLETED") {
			connected();
			return true;
		}

		if (phase in [ "fault", "stopped", "detached" ])
			return false;

		if (next == "INTERFACE_DISABLED") {
			cancel_all();
			set_phase("disabled");
			return true;
		}

		if (next == "INACTIVE") {
			if (!(phase in [ "parking", "disconnect-retry", "verifying", "parked" ])) {
				cancel_all();
				set_phase("inactive");
			}
			return true;
		}

		if (is_associating(next)) {
			if (!begin_window("station association"))
				return false;
			association_started = true;
			cancel_timer("grace");
			return true;
		}

		if (next == "SCANNING")
			return begin_window("supplicant scan");

		if (next == "DISCONNECTED") {
			if (phase in [ "parking", "disconnect-retry", "verifying", "parked" ])
				return true;

			if (!begin_window("station disconnected"))
				return false;
			if (association_started)
				schedule("grace", IMMEDIATE_MS,
					() => request_park("association ended"));
			return true;
		}

		return false;
	}

	function handle_ctrl_event(event)
	{
		if (phase in [ "fault", "stopped", "detached" ])
			return false;

		/* Background/stale scan events cannot override authoritative steady state. */
		let current = state();
		if (current == "COMPLETED") {
			connected();
			return true;
		}
		if (current in [ "INACTIVE", "INTERFACE_DISABLED" ]) {
			handle_state(current);
			return true;
		}
		if (is_associating(current)) {
			handle_state(current);
			return true;
		}
		if (is_parkable(current) &&
		    phase in [ "parking", "disconnect-retry", "verifying", "parked" ])
			return true;

		if (event_is(event, "CTRL-EVENT-SCAN-STARTED"))
			return begin_window("supplicant scan event");

		if (event_is(event, "CTRL-EVENT-SCAN-RESULTS")) {
			if (phase == "connecting" && !association_started)
				schedule("grace", RESULT_GRACE_MS,
					() => request_park("scan results produced no association"));
			return true;
		}

		if (event_is(event, "CTRL-EVENT-NETWORK-NOT-FOUND") ||
		    event_is(event, "CTRL-EVENT-SCAN-FAILED")) {
			if (!association_started &&
			    !(phase in [ "parking", "disconnect-retry", "verifying", "parked" ]))
				schedule("grace", IMMEDIATE_MS,
					() => request_park("supplicant reported no usable network"));
			return true;
		}

		return false;
	}

	function detach()
	{
		cancel_all();
		set_phase("detached");
	}

	function release()
	{
		let current = state();
		cancel_all();
		set_phase("stopped");

		if (current == "COMPLETED" || current == "INTERFACE_DISABLED" ||
		    current == "INACTIVE" && !disconnect_ok)
			return true;

		/* Keep a parked AP available if reconnect itself cannot be issued. */
		let accepted = adapter.control("RECONNECT");
		if (accepted)
			disconnect_ok = false;
		return accepted;
	}

	function audit()
	{
		if (phase == "connected") {
			let frequency = int(adapter.get_frequency());
			if (frequency > 0 && adapter.verify_ap(frequency))
				return true;

			if (frequency <= 0 || !adapter.set_ap(true, frequency)) {
				fault("connected AP could not be restored at the station frequency");
				return false;
			}

			set_phase("connected-repair");
			schedule("verify", AP_VERIFY_MS, () => {
				let current = state();
				if (current != "COMPLETED") {
					handle_state(current);
					return;
				}
				if (adapter.verify_ap(frequency)) {
					set_phase("connected");
					return;
				}

				adapter.set_ap(false, null);
				fault("connected AP repair did not verify at the station frequency");
			});
			return false;
		}

		if (phase != "parked")
			return true;

		if (adapter.verify_ap(fallback_frequency))
			return true;

		adapter.set_ap(false, null);
		fault("fallback AP disappeared while the station was parked");
		return false;
	}

	function status()
	{
		let active_timers = {};
		for (let name in [ "watchdog", "settle", "verify", "retry", "grace" ]) {
			let timer = timers[name];
			if (!timer)
				continue;
			active_timers[name] = type(timer.remaining) == "function"
				? timer.remaining() : true;
		}

		return {
			phase,
			retry_interval: retry_ms / 1000,
			scan_timeout: timeout_ms / 1000,
			fallback_frequency,
			disconnect_attempts,
			park_reason,
			last_error,
			timers: active_timers,
		};
	}

	return {
		start,
		handle_state,
		handle_ctrl_event,
		retry_now,
		audit,
		detach,
		release,
		status,
	};
};
