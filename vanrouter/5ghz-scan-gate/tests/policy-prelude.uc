let test_timers = [];
let test_ubus_calls = [];
let test_ctrl_calls = [];
let test_stock_notifies = [];
let test_logs = [];
let test_last_ubus_error = null;
let test_fail_ap_down = false;
let test_fail_ap_up = false;
let test_iface_state = "DISCONNECTED";
let test_iface_frequency = 5745;
let test_ctrl_results = {};
let scan_gate_remove;
let scan_gate_state;
let scan_gate_park;

function test_assert(condition, message)
{
	if (condition)
		return;

	warn(`scan-gate policy test failed: ${message}\n`);
	exit(1);
}

function test_timer(ms, callback)
{
	let timer = { ms, callback, cancelled: false };
	timer.cancel = () => { timer.cancelled = true; };
	push(test_timers, timer);
	return timer;
}

let uloop = { timer: test_timer };

let ubus = {
	call: (object, method, msg) => {
		push(test_ubus_calls, { object, method, msg });
		if (object == "hostapd" && method == "apsta_state" &&
		    ((msg.up && test_fail_ap_up) || (!msg.up && test_fail_ap_down)))
			test_last_ubus_error = "mock ubus failure";
		else
			test_last_ubus_error = null;
		return null;
	},
	error: () => {
		let error = test_last_ubus_error;
		test_last_ubus_error = null;
		return error;
	},
};

let test_iface = {
	status: () => ({
		state: test_iface_state,
		frequency: test_iface_frequency,
		sec_chan_offset: 1,
	}),
	ctrl: (command) => {
		push(test_ctrl_calls, command);
		let result = test_ctrl_results[command] ?? "OK";
		if (command == "DISCONNECT" && result == "OK")
			test_iface_state = "DISCONNECTED";
		else if (command == "RECONNECT" && result == "OK")
			test_iface_state = "SCANNING";
		return result;
	},
};

let wpas = {
	data: {
		iface_phy: {},
		config: {},
		apsta_scan_gate: {},
	},
	interfaces: {},
	printf: (message) => { push(test_logs, message); },
};

function iface_hostapd_notify(ifname, iface, state)
{
	let status = iface.status();
	push(test_stock_notifies, {
		ifname,
		state,
		frequency: status.frequency,
		sec_chan_offset: status.sec_chan_offset,
	});
}
