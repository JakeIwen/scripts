'use strict';

let state = global.DAEMON_TEST;

function trace(message)
{
	print(`TRACE ubus ${message}\n`);
}

function wireless_status()
{
	return {
		radio1: {
			up: true,
			pending: false,
			config: {
				band: "5g",
				channel: "149",
				phy: "phy1",
			},
			interfaces: [
				{
					section: "wifinet4",
					ifname: "wl1-sta0",
					config: {
						mode: "sta",
						network: [ "clientwan" ],
						key: "must-never-be-logged",
					},
				},
				{
					section: "dendelion_5g",
					ifname: "wl1-ap0",
					config: {
						mode: "ap",
						network: [ "lan" ],
						key: "must-never-be-logged",
					},
				},
			],
		},
	};
}

function connection()
{
	let last_error = null;

	function call(object, method, message)
	{
		trace(`call ${object} ${method} ${sprintf("%J", message)}`);
		last_error = null;

		if (object == "system" && method == "board")
			return {
				board_name: "linksys,e8450-ubi",
				release: { version: "25.12.5" },
			};

		if (object == "network.wireless" && method == "status")
			return wireless_status();

		if (object == "hostapd.wl1-ap0" && method == "get_status")
			return {
				phy: "phy1",
				status: state.ap_up ? "ENABLED" : "DISABLED",
				freq: state.ap_up ? state.ap_frequency : null,
			};

		if (object == "wpa_supplicant" && method == "bss_info")
			return { wpa_state: state.wpa_state };

		if (object == "wpa_supplicant" && method == "iface_status")
			return { state: state.wpa_state };

		if (object == "hostapd" && method == "apsta_state") {
			state.ap_up = !!message.up;
			state.ap_frequency = message.up ? int(message.frequency) : null;
			return null;
		}

		if (object == "wpa_supplicant.wl1-sta0" && method == "control") {
			if (message.command == "DISCONNECT") {
				state.wpa_state = "DISCONNECTED";
				return { result: "OK" };
			}
			if (message.command == "RECONNECT") {
				state.reconnect_attempts = int(state.reconnect_attempts ?? 0) + 1;
				return { result: "FAIL" };
			}
			return { result: "OK" };
		}

		last_error = "mock method not found";
		return null;
	}

	function subscriber(notify, removed)
	{
		trace("subscriber");
		return {
			subscribe: (path) => {
				trace(`subscribe ${path}`);
				last_error = null;
				return true;
			},
			unsubscribe: (path) => {
				trace(`unsubscribe ${path}`);
				last_error = null;
				return true;
			},
		};
	}

	function listener(event, callback)
	{
		trace(`listener ${event}`);
		return { remove: () => true };
	}

	function publish(name, methods)
	{
		trace(`publish ${name}`);
		state.api = methods;
		return { remove: () => true };
	}

	return {
		call,
		error: () => {
			let error = last_error;
			last_error = null;
			return error;
		},
		subscriber,
		listener,
		publish,
		disconnect: () => true,
	};
}

export function connect()
{
	trace("connect");
	return connection();
};
