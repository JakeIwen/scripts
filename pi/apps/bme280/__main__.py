"""Package entrypoint for the BME280 MQTT publisher."""

import runpy

from pi.package_runtime import record_running_release


record_running_release("/run/bme280-mqtt")
runpy.run_module("pi.apps.bme280.bme280_mqtt", run_name="__main__")
