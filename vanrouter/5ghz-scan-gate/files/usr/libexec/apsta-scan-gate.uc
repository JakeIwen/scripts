#!/usr/bin/ucode

import * as libubus from "ubus";
import * as uloop from "uloop";
import * as libuci from "uci";
import { create as create_policy } from "apsta_scan_gate.policy";

const SERVICE = "apsta_scan_gate";
const EXPECTED_VERSION = "25.12.5";
const EXPECTED_BOARD = "linksys,e8450-ubi";
const EVENT_DELAY_MS = 1;
const MAX_EVENTS = 64;
const RELEASE_RETRY_MS = 500;
const RELEASE_LIMIT = 3;

let bus;
let config;
let identity;
let policy;
let subscriber;
let object_listener;
let api_object;
let event_timer;
let poll_timer;
let release_timer;
let release_generation = 0;
let signal_handlers = [];
let events = [];
let subscribed_iface;
let subscriptions_dirty = true;
let paused = false;
let shutting_down = false;
let last_error;
let last_wpa_state;
let last_wpa_frequency;
let last_ap_status;
let last_ap_frequency;
let stock_resumed;

let reconcile;
let queue_event;
let perform_reload;
let perform_shutdown;
let release_stock;

function truthy(value)
{
	return value === true || value == 1 || value == "1" || value == "true";
}

function bounded_int(value, fallback, minimum, maximum)
{
	let number = int(value ?? fallback);
	if (number < minimum)
		return minimum;
	if (number > maximum)
		return maximum;
	return number;
}

function list_has(value, wanted)
{
	if (type(value) == "array") {
		for (let item in value)
			if (item == wanted)
				return true;
		return false;
	}

	return value == wanted;
}

function scan_list_present(value)
{
	if (value == null)
		return false;
	if (type(value) == "array")
		return length(value) > 0;
	return value != "";
}

function read_config()
{
	let section;

	try {
		section = libuci.cursor().get_all("apsta-scan-gate", "main");
	}
	catch (e) {
		return { ok: false, error: "could not read the service UCI section" };
	}

	if (type(section) != "object")
		return { ok: false, error: "service UCI section 'main' is missing" };

	let value = {
		enabled: truthy(section.enabled),
		radio: section.radio,
		station_section: section.station_section,
		station_network: section.station_network,
		ap_section: section.ap_section,
		retry_interval: bounded_int(section.retry_interval, 60, 15, 3600),
		scan_timeout: bounded_int(section.scan_timeout, 15, 5, 60),
		poll_interval: bounded_int(section.poll_interval, 5, 2, 30),
	};

	for (let name in [ "radio", "station_section", "station_network", "ap_section" ])
		if (type(value[name]) != "string" || value[name] == "")
			return { ok: false, error: `invalid service option '${name}'` };

	return { ok: true, value };
}

function ubus_call(object, method, message)
{
	let value;

	try {
		value = bus.call(object, method, message);
	}
	catch (e) {
		return { ok: false, error: `${object}.${method} raised an exception` };
	}

	/* A null payload may be success; consume the connection error immediately. */
	let error = bus.error();
	if (error != null)
		return { ok: false, error: `${object}.${method} was not accepted` };

	return { ok: true, value };
}

function check_platform()
{
	let result = ubus_call("system", "board", {});
	if (!result.ok || type(result.value) != "object")
		return { ok: false, error: "could not verify the OpenWrt platform" };

	if (result.value.board_name != EXPECTED_BOARD)
		return { ok: false, error: "service is pinned to the Linksys E8450 UBI board" };

	if (result.value.release?.version != EXPECTED_VERSION)
		return { ok: false, error: `service is pinned to OpenWrt ${EXPECTED_VERSION}` };

	return { ok: true };
}

function nondfs_channel(channel)
{
	return channel in [ 36, 40, 44, 48, 149, 153, 157, 161, 165 ];
}

function resolve_identity()
{
	let result = ubus_call("network.wireless", "status", { device: config.radio });
	if (!result.ok || type(result.value) != "object")
		return { ok: false, error: "wireless runtime status is unavailable" };

	let radio = result.value[config.radio];
	if (type(radio) != "object" || !radio.up || radio.pending)
		return { ok: false, error: "configured 5 GHz radio is not stably up" };
	if (type(radio.config) != "object" || radio.config.band != "5g")
		return { ok: false, error: "configured radio is not a 5 GHz radio" };
	if (scan_list_present(radio.config.scan_list))
		return { ok: false, error: "radio scan_list must be absent for full-band retries" };

	let channel = int(radio.config.channel);
	if (!nondfs_channel(channel))
		return { ok: false, error: "fallback channel must be fixed and non-DFS" };

	let sta_count = 0;
	let station;
	let access_point;
	for (let iface in radio.interfaces ?? []) {
		let icfg = iface?.config;
		if (type(icfg) != "object" || truthy(icfg.disabled))
			continue;

		if (icfg.mode == "sta") {
			sta_count++;
			if (iface.section == config.station_section &&
			    list_has(icfg.network, config.station_network))
				station = iface;
		}
		else if (icfg.mode == "ap" && iface.section == config.ap_section) {
			access_point = iface;
		}
	}

	if (sta_count != 1)
		return { ok: false, error: "radio must have exactly one active station interface" };
	if (!station || !station.ifname)
		return { ok: false, error: "configured station interface is not active" };
	if (truthy(station.config.mlo) ||
	    station.config.mld != null && station.config.mld !== false && station.config.mld != "")
		return { ok: false, error: "MLO station interfaces are not supported" };
	if (!access_point || !access_point.ifname)
		return { ok: false, error: "configured fallback AP interface is not active" };

	let ap = ubus_call(`hostapd.${access_point.ifname}`, "get_status", {});
	if (!ap.ok || type(ap.value) != "object")
		return { ok: false, error: "fallback AP runtime object is unavailable" };

	let phy = ap.value.phy ?? radio.config.phy;
	if (type(phy) != "string" || phy == "")
		return { ok: false, error: "wireless PHY identity is unavailable" };

	return {
		ok: true,
		value: {
			radio: config.radio,
			station_section: config.station_section,
			station_ifname: station.ifname,
			ap_section: config.ap_section,
			ap_ifname: access_point.ifname,
			phy,
			fallback_frequency: 5000 + channel * 5,
		},
	};
}

function same_identity(left, right)
{
	if (!left || !right)
		return false;

	for (let name in [ "radio", "station_section", "station_ifname", "ap_section",
	                         "ap_ifname", "phy", "fallback_frequency" ])
		if (left[name] != right[name])
			return false;

	return true;
}

function wpa_state()
{
	if (!identity)
		return null;

	/* bss_info exists on the deployed controller and carries wpa_state. */
	let result = ubus_call("wpa_supplicant", "bss_info",
		{ iface: identity.station_ifname });
	let state = result.ok ? result.value?.wpa_state : null;
	let frequency = result.ok ? result.value?.freq : null;

	/* OpenWrt 25.12.5 also provides iface_status; retain it as a fallback. */
	if (type(state) != "string") {
		result = ubus_call("wpa_supplicant", "iface_status",
			{ name: identity.station_ifname });
		state = result.ok ? result.value?.state : null;
		frequency = result.ok ? result.value?.frequency : null;
	}

	last_wpa_frequency = frequency != null ? int(frequency) : null;
	return type(state) == "string" ? state : null;
}

function control(command)
{
	if (!identity)
		return false;

	let result = ubus_call(`wpa_supplicant.${identity.station_ifname}`,
		"control", { command });
	if (!result.ok || result.value?.result != "OK") {
		last_error = `supplicant command '${command}' was not accepted`;
		return false;
	}

	return true;
}

function set_ap(up, frequency)
{
	if (!identity)
		return false;

	let message = {
		phy: identity.phy,
		radio: -1,
		up: !!up,
		csa: false,
	};
	if (up)
		message.frequency = int(frequency);

	let result = ubus_call("hostapd", "apsta_state", message);
	if (!result.ok) {
		last_error = up ? "fallback AP start failed" : "fallback AP stop failed";
		return false;
	}

	last_ap_status = up ? "starting" : "stopping";
	last_ap_frequency = up ? int(frequency) : null;
	return true;
}

function refresh_ap_status()
{
	if (!identity)
		return null;

	let result = ubus_call(`hostapd.${identity.ap_ifname}`, "get_status", {});
	if (!result.ok || type(result.value) != "object") {
		last_ap_status = "unavailable";
		last_ap_frequency = null;
		return null;
	}

	last_ap_status = result.value.status;
	last_ap_frequency = result.value.freq != null ? int(result.value.freq) : null;
	return { status: last_ap_status, frequency: last_ap_frequency };
}

function verify_ap(frequency)
{
	let status = refresh_ap_status();
	return status?.status == "ENABLED" && status.frequency == int(frequency);
}

function make_adapter()
{
	return {
		timer: (milliseconds, callback) => uloop.timer(milliseconds, callback),
		get_state: () => wpa_state(),
		get_frequency: () => last_wpa_frequency,
		set_ap,
		verify_ap,
		control,
		log: (level, message) => {
			if (level == "error")
				last_error = message;
			warn(`${SERVICE}[${level}]: ${message}\n`);
		},
		phase: (next) => {
			if (!subscriptions_dirty && next in [ "connected", "parked", "inactive" ])
				last_error = null;
			if (next in [ "connected", "inactive", "disabled" ]) {
				last_ap_status = "stock-managed";
				last_ap_frequency = null;
			}
			print(`${SERVICE}: phase=${next}\n`);
		},
	};
}

function unsubscribe(path)
{
	if (!subscriber || !path)
		return;

	try {
		subscriber.unsubscribe(path);
		bus.error();
	}
	catch (e) {
		/* The object may already have disappeared. */
	}
}

function subscribe(path)
{
	if (!subscriber || !path)
		return false;

	try {
		subscriber.subscribe(path);
		return bus.error() == null;
	}
	catch (e) {
		return false;
	}
}

function refresh_subscriptions()
{
	unsubscribe("wpa_supplicant");
	unsubscribe(subscribed_iface);
	subscribed_iface = null;

	let root_ok = subscribe("wpa_supplicant");
	let iface_ok = identity && subscribe(`wpa_supplicant.${identity.station_ifname}`);
	if (iface_ok)
		subscribed_iface = `wpa_supplicant.${identity.station_ifname}`;
	subscriptions_dirty = !root_ok || identity && !iface_ok;
	if (subscriptions_dirty)
		last_error = "supplicant event subscription is not ready";
	else if (last_error == "supplicant event subscription is not ready")
		last_error = null;
}

function arm_poll()
{
	if (poll_timer)
		poll_timer.cancel();
	if (shutting_down || !config?.enabled)
		return;

	poll_timer = uloop.timer(config.poll_interval * 1000, () => {
		poll_timer = null;
		reconcile();
		arm_poll();
	});
}

release_stock = function(callback)
{
	let token = ++release_generation;
	let attempts = 0;
	let attempt;

	paused = true;
	stock_resumed = null;
	if (release_timer)
		release_timer.cancel();
	release_timer = null;

	attempt = function()
	{
		if (token != release_generation)
			return;

		attempts++;
		let ok = !policy || policy.release();
		last_wpa_state = wpa_state();
		if (ok) {
			stock_resumed = true;
			release_timer = null;
			if (type(callback) == "function")
				callback(true);
			return;
		}

		if (attempts < RELEASE_LIMIT) {
			release_timer = uloop.timer(RELEASE_RETRY_MS, attempt);
			return;
		}

		stock_resumed = false;
		release_timer = null;
		last_error = "stock reconnect was not acknowledged after three attempts";
		warn(`${SERVICE}[error]: ${last_error}\n`);
		if (type(callback) == "function")
			callback(false);
	};

	attempt();
};

reconcile = function()
{
	if (shutting_down || paused || !config?.enabled)
		return;

	let resolved = resolve_identity();
	if (!resolved.ok) {
		let resolution_error = resolved.error;
		last_error = resolution_error;
		last_wpa_state = null;
		if (policy) {
			release_stock((ok) => {
				if (!ok)
					return;
				policy = null;
				identity = null;
				paused = false;
				last_error = resolution_error;
				refresh_subscriptions();
			});
		}
		else {
			identity = null;
			refresh_subscriptions();
		}
		return;
	}

	if (!same_identity(identity, resolved.value)) {
		if (policy) {
			let replacement = resolved.value;
			release_stock((ok) => {
				if (!ok)
					return;
				policy = null;
				identity = replacement;
				paused = false;
				stock_resumed = null;
				refresh_subscriptions();
				reconcile();
			});
			return;
		}
		identity = resolved.value;
		stock_resumed = null;
		refresh_subscriptions();
	}
	else if (subscriptions_dirty) {
		refresh_subscriptions();
	}

	let state = wpa_state();
	if (!state) {
		let state_error = "station runtime state is unavailable";
		last_error = state_error;
		if (policy) {
			release_stock((ok) => {
				if (!ok)
					return;
				policy = null;
				paused = false;
				last_error = state_error;
			});
		}
		return;
	}

	let previous_state = last_wpa_state;
	last_wpa_state = state;
	if (!policy) {
		stock_resumed = null;
		policy = create_policy(make_adapter(), {
			retry_interval: config.retry_interval,
			scan_timeout: config.scan_timeout,
			fallback_frequency: identity.fallback_frequency,
		});
		last_error = null;
		policy.start(state);
	}
	else if (state != previous_state) {
		policy.handle_state(state);
	}
	refresh_ap_status();
	let phase = policy.status().phase;
	if (phase in [ "connected", "parked" ])
		policy.audit();
};

function service_status()
{
	let pstatus = policy ? policy.status() : null;
	return {
		version: 1,
		enabled: !!config?.enabled,
		paused,
		phase: pstatus?.phase ?? (shutting_down ? "stopping" : "unavailable"),
		radio: identity?.radio ?? config?.radio,
		station_section: identity?.station_section ?? config?.station_section,
		station_ifname: identity?.station_ifname,
		ap_section: identity?.ap_section ?? config?.ap_section,
		ap_ifname: identity?.ap_ifname,
		fallback_frequency: identity?.fallback_frequency,
		wpa_state: last_wpa_state,
		wpa_frequency: last_wpa_frequency,
		ap_status: last_ap_status,
		ap_frequency: last_ap_frequency,
		last_error,
		stock_resumed,
		policy: pstatus,
	};
}

function resume_stock()
{
	release_stock();
}

perform_reload = function()
{
	let loaded = read_config();
	release_stock((ok) => {
		if (!ok)
			return;

		policy = null;
		identity = null;
		last_wpa_state = null;
		last_wpa_frequency = null;
		last_ap_status = null;
		last_ap_frequency = null;
		if (!loaded.ok) {
			paused = true;
			last_error = loaded.error;
			return;
		}

		config = loaded.value;
		paused = !config.enabled;
		stock_resumed = null;
		refresh_subscriptions();

		if (!paused) {
			last_error = null;
			reconcile();
		}
		arm_poll();
	});
};

perform_shutdown = function()
{
	if (shutting_down)
		return;

	shutting_down = true;
	paused = true;
	if (poll_timer)
		poll_timer.cancel();
	poll_timer = null;
	release_stock((ok) => {
		policy = null;
		uloop.end();
	});
};

function process_events()
{
	event_timer = null;
	while (length(events) > 0) {
		let event = shift(events);

		if (event.kind == "reconcile") {
			reconcile();
			continue;
		}
		if (event.kind == "reload") {
			perform_reload();
			continue;
		}
		if (event.kind == "resume") {
			resume_stock();
			continue;
		}
		if (event.kind == "shutdown") {
			perform_shutdown();
			return;
		}
		if (event.kind == "retry") {
			if (policy && !paused)
				policy.retry_now();
			continue;
		}

		if (!policy || paused || !identity)
			continue;

		/* Notifications are hints. Query authoritative state outside callback. */
		let state = wpa_state();
		if (!state)
			continue;
		let previous_state = last_wpa_state;
		last_wpa_state = state;

		if (event.kind == "state") {
			if (state != previous_state)
				policy.handle_state(state);
		}
		else if (event.kind == "control") {
			if (state != previous_state)
				policy.handle_state(state);
			if (state in [ "COMPLETED", "INACTIVE", "INTERFACE_DISABLED" ])
				continue;
			policy.handle_ctrl_event(event.value);
		}
	}
}

queue_event = function(kind, value)
{
	if (shutting_down && kind != "shutdown")
		return;

	if (length(events) >= MAX_EVENTS) {
		events = [ { kind: "reconcile" } ];
	}
	else {
		push(events, { kind, value });
	}

	if (!event_timer)
		event_timer = uloop.timer(EVENT_DELAY_MS, process_events);
};

function subscriber_notify(message)
{
	let source = message?.info?.object?.path;
	let data = message?.data;

	if (source == "wpa_supplicant" && message.type == "iface.state" &&
	    data?.name == identity?.station_ifname && type(data.state) == "string")
		queue_event("state", data.state);
	else if (source == `wpa_supplicant.${identity?.station_ifname}` &&
	         message.type == "ctrl-event" && type(data?.event) == "string")
		queue_event("control", data.event);

	return 0;
}

function subscriber_removed(object_id)
{
	subscriptions_dirty = true;
	queue_event("reconcile");
}

function object_added(event, message)
{
	let path = message?.path;
	if (path == "wpa_supplicant" || path == `wpa_supplicant.${identity?.station_ifname}` ||
	    path == "hostapd" || path == `hostapd.${identity?.ap_ifname}`) {
		subscriptions_dirty = true;
		queue_event("reconcile");
	}
}

let loaded = read_config();
if (!loaded.ok) {
	warn(`${SERVICE}: ${loaded.error}\n`);
	exit(1);
}
config = loaded.value;
if (!config.enabled)
	exit(0);

uloop.init();
bus = libubus.connect();
if (!bus) {
	warn(`${SERVICE}: could not connect to ubus\n`);
	exit(1);
}

let platform = check_platform();
if (!platform.ok) {
	warn(`${SERVICE}: ${platform.error}\n`);
	exit(1);
}

subscriber = bus.subscriber(subscriber_notify, subscriber_removed);
object_listener = bus.listener("ubus.object.add", object_added);
api_object = bus.publish(SERVICE, {
	status: {
		args: {},
		call: (request) => service_status(),
	},
	retry: {
		args: {},
		call: (request) => {
			queue_event("retry");
			return { accepted: !!policy && !paused };
		},
	},
	reload: {
		args: {},
		call: (request) => {
			queue_event("reload");
			return { accepted: true };
		},
	},
	resume_stock: {
		args: {},
		call: (request) => {
			queue_event("resume");
			return { accepted: true };
		},
	},
	shutdown: {
		args: {},
		call: (request) => {
			queue_event("shutdown");
			return { accepted: true };
		},
	},
});

push(signal_handlers, uloop.signal("SIGTERM", () => queue_event("shutdown")));
push(signal_handlers, uloop.signal("SIGINT", () => queue_event("shutdown")));
push(signal_handlers, uloop.signal("SIGHUP", () => queue_event("reload")));

queue_event("reconcile");
arm_poll();
uloop.run();
