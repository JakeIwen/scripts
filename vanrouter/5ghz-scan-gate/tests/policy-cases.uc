function test_reset()
{
	test_timers = [];
	test_ubus_calls = [];
	test_ctrl_calls = [];
	test_stock_notifies = [];
	test_logs = [];
	test_last_ubus_error = null;
	test_fail_ap_down = false;
	test_fail_ap_up = false;
	test_iface_state = "DISCONNECTED";
	test_iface_frequency = 5745;
	test_ctrl_results = {};

	let config = {
		mode: "sta",
		mlo: false,
		apsta_scan_gate: true,
		apsta_retry_interval: 60,
		apsta_scan_timeout: 15,
		apsta_fallback_frequency: 5745,
	};
	wpas.data.iface_phy = { sta0: "phy1" };
	wpas.data.config = {
		phy1: {
			data: {
				sta0: { config },
			},
		},
	};
	wpas.data.apsta_scan_gate = {};
	wpas.interfaces = { sta0: test_iface };

	return config;
}

function test_fire(timer)
{
	test_assert(timer != null, "expected a timer");
	test_assert(!timer.cancelled, "timer was already cancelled");
	timer.cancelled = true;
	timer.callback();
}

function test_active_timer_count()
{
	let count = 0;
	for (let timer in test_timers)
		if (!timer.cancelled)
			count++;
	return count;
}

function test_ap_call_count(up)
{
	let count = 0;
	for (let call in test_ubus_calls)
		if (call.object == "hostapd" && call.method == "apsta_state" &&
		    call.msg.up == up)
			count++;
	return count;
}

function test_last_ap_call()
{
	for (let i = length(test_ubus_calls) - 1; i >= 0; i--) {
		let call = test_ubus_calls[i];
		if (call.object == "hostapd" && call.method == "apsta_state")
			return call;
	}
	return null;
}

function test_ctrl_count(command)
{
	let count = 0;
	for (let value in test_ctrl_calls)
		if (value == command)
			count++;
	return count;
}

/* Feature-off and invalid-topology paths stay on stock behavior. */
let config = test_reset();
config.apsta_scan_gate = false;
test_assert(scan_gate_get("sta0", test_iface) == null, "disabled gate became active");
if (!scan_gate_handle_state("sta0", test_iface, "SCANNING"))
	iface_hostapd_notify("sta0", test_iface, "SCANNING");
test_assert(length(test_stock_notifies) == 1, "disabled gate did not dispatch stock state");

config = test_reset();
config.mlo = true;
test_assert(scan_gate_get("sta0", test_iface) == null, "MLO gate became active");
config = test_reset();
delete config.apsta_fallback_frequency;
test_assert(scan_gate_get("sta0", test_iface) == null, "gate accepted no fallback frequency");
config = test_reset();
wpas.data.config.phy1.data.sta1 = { config: { mode: "sta", mlo: false } };
test_assert(scan_gate_get("sta0", test_iface) == null, "gate accepted a second legacy STA");
config = test_reset();
wpas.data.config.phy1.data.sta1 = { config: { mode: "sta", mlo: true } };
test_assert(scan_gate_get("sta0", test_iface) == null, "gate accepted a second MLO STA");

/* Runtime clamps defend the schema boundaries and defaults. */
config = test_reset();
config.apsta_retry_interval = 1;
config.apsta_scan_timeout = 1;
let gate = scan_gate_get("sta0", test_iface);
test_assert(gate.retry_ms == 15000 && gate.timeout_ms == 5000, "minimum bounds failed");
config = test_reset();
config.apsta_retry_interval = 99999;
config.apsta_scan_timeout = 999;
gate = scan_gate_get("sta0", test_iface);
test_assert(gate.retry_ms == 3600000 && gate.timeout_ms == 60000, "maximum bounds failed");
config = test_reset();
delete config.apsta_retry_interval;
delete config.apsta_scan_timeout;
gate = scan_gate_get("sta0", test_iface);
test_assert(gate.retry_ms == 60000 && gate.timeout_ms == 15000, "runtime defaults failed");

/* Duplicate scan state/events keep one bounded connecting window. */
test_reset();
test_assert(scan_gate_handle_state("sta0", test_iface, "DISCONNECTED"),
	"gate did not handle disconnected state");
gate = wpas.data.apsta_scan_gate.sta0;
let first_watchdog = gate.watchdog;
test_assert(gate.phase == "connecting", "disconnected did not start connecting window");
test_assert(test_ap_call_count(false) == 1, "connecting did not stop the AP exactly once");
scan_gate_handle_state("sta0", test_iface, "SCANNING");
scan_gate_handle_ctrl_event("sta0", test_iface, "CTRL-EVENT-SCAN-STARTED");
test_assert(gate.watchdog == first_watchdog, "duplicate scan reset the watchdog");
test_assert(test_active_timer_count() == 1, "duplicate scan created another timer");

/* No network: DISCONNECT, verified settle, fallback AP, then a bounded retry. */
scan_gate_handle_ctrl_event("sta0", test_iface, "CTRL-EVENT-NETWORK-NOT-FOUND");
test_assert(gate.grace.ms == 1, "network-not-found did not schedule immediate park");
test_fire(gate.grace);
test_assert(test_ctrl_calls[length(test_ctrl_calls) - 1] == "DISCONNECT",
	"park did not issue DISCONNECT");
test_fire(gate.settle);
test_assert(gate.phase == "parked", "verified disconnect did not park");
let ap_call = test_last_ap_call();
test_assert(ap_call.msg.up && ap_call.msg.frequency == 5745,
	"park did not start the fallback AP at 5745 MHz");
test_assert(gate.retry.ms == 60000, "park did not arm configured retry interval");

test_fire(gate.retry);
test_assert(gate.phase == "connecting" && gate.watchdog != null,
	"retry did not start a bounded connecting window");
test_assert(test_ctrl_calls[length(test_ctrl_calls) - 2] == "BSS_FLUSH 0" &&
	    test_ctrl_calls[length(test_ctrl_calls) - 1] == "RECONNECT",
	"retry command ordering is wrong");
test_assert(!test_last_ap_call().msg.up, "retry did not stop AP before reconnect");

/* Association cancels scan grace; completion follows the live station channel. */
test_reset();
scan_gate_handle_state("sta0", test_iface, "DISCONNECTED");
gate = wpas.data.apsta_scan_gate.sta0;
scan_gate_handle_ctrl_event("sta0", test_iface, "CTRL-EVENT-SCAN-RESULTS");
let result_grace = gate.grace;
scan_gate_handle_state("sta0", test_iface, "ASSOCIATING");
test_assert(result_grace.cancelled && gate.grace == null,
	"association did not cancel scan-result grace");
test_iface_state = "COMPLETED";
test_iface_frequency = 5200;
scan_gate_handle_state("sta0", test_iface, "COMPLETED");
test_assert(gate.phase == "connected", "completion did not enter connected phase");
test_assert(gate.watchdog == null && gate.retry == null && gate.settle == null && gate.grace == null,
	"completion left a gate timer armed");
let stock = test_stock_notifies[length(test_stock_notifies) - 1];
test_assert(stock.state == "COMPLETED" && stock.frequency == 5200,
	"completion did not use the station's live frequency");

/* Three unverifiable disconnect attempts fail closed without a phantom AP. */
test_reset();
test_ctrl_results.DISCONNECT = "FAIL";
scan_gate_handle_state("sta0", test_iface, "DISCONNECTED");
gate = wpas.data.apsta_scan_gate.sta0;
scan_gate_handle_ctrl_event("sta0", test_iface, "CTRL-EVENT-NETWORK-NOT-FOUND");
test_fire(gate.grace);
for (let i = 0; i < 5; i++)
	test_fire(gate.settle);
test_assert(gate.phase == "fault", "disconnect verification did not fault after three attempts");
test_assert(test_ctrl_count("DISCONNECT") == 3, "disconnect retry count is not three");
test_assert(test_ap_call_count(true) == 0 && gate.retry == null,
	"failed disconnect advertised AP up or armed retry");

/* Both ubus directions are checked immediately and fail closed. */
test_reset();
test_fail_ap_down = true;
scan_gate_handle_state("sta0", test_iface, "DISCONNECTED");
gate = wpas.data.apsta_scan_gate.sta0;
test_assert(gate.phase == "fault" && gate.watchdog == null,
	"failed AP stop did not cancel watchdog and fault");
test_assert(test_ctrl_count("BSS_FLUSH 0") == 0 && test_ctrl_count("RECONNECT") == 0,
	"failed AP stop still allowed reconnect commands");

test_reset();
scan_gate_handle_state("sta0", test_iface, "DISCONNECTED");
gate = wpas.data.apsta_scan_gate.sta0;
scan_gate_handle_ctrl_event("sta0", test_iface, "CTRL-EVENT-NETWORK-NOT-FOUND");
test_fire(gate.grace);
test_fail_ap_up = true;
test_fire(gate.settle);
test_assert(gate.phase == "fault" && gate.retry == null,
	"failed fallback AP start did not fault without retry");

/* INACTIVE is an intentional no-network park; interface disable removes state. */
test_reset();
scan_gate_handle_state("sta0", test_iface, "DISCONNECTED");
gate = wpas.data.apsta_scan_gate.sta0;
first_watchdog = gate.watchdog;
scan_gate_handle_state("sta0", test_iface, "INACTIVE");
test_assert(gate.phase == "inactive" && first_watchdog.cancelled && gate.retry == null,
	"INACTIVE did not cancel attempts and leave retries off");
stock = test_stock_notifies[length(test_stock_notifies) - 1];
test_assert(stock.state == "INACTIVE", "INACTIVE did not restore stock AP-up handling");

test_reset();
scan_gate_handle_state("sta0", test_iface, "DISCONNECTED");
gate = wpas.data.apsta_scan_gate.sta0;
first_watchdog = gate.watchdog;
scan_gate_handle_state("sta0", test_iface, "INTERFACE_DISABLED");
test_assert(wpas.data.apsta_scan_gate.sta0 == null && first_watchdog.cancelled,
	"interface disable did not remove gate and cancel timer");

/* A callback retained from an old generation cannot mutate a replacement gate. */
test_reset();
scan_gate_handle_state("sta0", test_iface, "DISCONNECTED");
gate = wpas.data.apsta_scan_gate.sta0;
let stale_watchdog = gate.watchdog;
scan_gate_remove("sta0");
scan_gate_handle_state("sta0", test_iface, "DISCONNECTED");
let replacement = wpas.data.apsta_scan_gate.sta0;
stale_watchdog.callback();
test_assert(wpas.data.apsta_scan_gate.sta0 == replacement && replacement.phase == "connecting",
	"stale timer mutated the replacement gate");

config = wpas.data.config.phy1.data.sta0.config;
let replacement_watchdog = replacement.watchdog;
config.apsta_scan_gate = false;
test_assert(scan_gate_get("sta0", test_iface) == null && replacement_watchdog.cancelled,
	"invalidated config did not remove existing gate state");

print("scan-gate policy cases: passed\n");
