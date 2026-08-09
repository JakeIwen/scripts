'use strict';

function trace(message)
{
	print(`TRACE uci ${message}\n`);
}

export function cursor()
{
	trace("cursor");
	return {
		get_all: (config, section) => {
			trace(`get_all ${config} ${section}`);
			let scenario = global.DAEMON_TEST?.scenario;
			return {
				enabled: scenario == "disabled" ? "0" : "1",
				radio: "radio1",
				station_section: "wifinet4",
				station_network: "clientwan",
				ap_section: "dendelion_5g",
				retry_interval: "60",
				scan_timeout: "15",
				poll_interval: "5",
			};
		},
	};
};
