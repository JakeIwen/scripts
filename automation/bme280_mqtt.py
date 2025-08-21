# /home/pi/scripts/python-automation/bme280_mqtt.py
import time, json, socket
import paho.mqtt.client as mqtt
import board
import adafruit_bitbangio as bitbangio
from adafruit_bme280.basic import Adafruit_BME280_I2C  # <-- per your layout

DEVICE_ID = "vanpi_bme280_1"             # change per sensor
FRIENDLY  = "BME280 (Pi)"       # name in HA
BROKER    = "127.0.0.1"                  # or your HA IP
PORT      = 1883
USERNAME  = None                         # set if your broker requires it
PASSWORD  = None

# Topics
state_topic = f"vanpi/sensors/{DEVICE_ID}/state"
avail_topic = f"vanpi/sensors/{DEVICE_ID}/availability"

# Software I2C on GPIO24=SCL (pin 18) / GPIO23=SDA (pin 16)
i2c = bitbangio.I2C(board.D24, board.D23, frequency=50_000)
bme = Adafruit_BME280_I2C(i2c, address=0x76)  # 0x77 if SDO high

# MQTT client
client = mqtt.Client(client_id=f"{DEVICE_ID}-{socket.gethostname()}")
if USERNAME and PASSWORD:
    client.username_pw_set(USERNAME, PASSWORD)
client.will_set(avail_topic, "offline", retain=True)
client.connect(BROKER, PORT, keepalive=60)
client.loop_start()

# ---- Home Assistant MQTT Discovery ----
base = f"homeassistant/sensor/{DEVICE_ID}"
device_obj = {
    "identifiers": [DEVICE_ID],
    "manufacturer": "BME280",
    "model": "Env Sensor",
    "name": FRIENDLY,
}
discovery = [
    {
        "obj_id": "temperature",
        "name": f"{FRIENDLY} Temperature",
        "dev_class": "temperature",
        "unit": "°C",
        "value_template": "{{ value_json.temperature }}",
        "precision": 1,
    },
    {
        "obj_id": "humidity",
        "name": f"{FRIENDLY} Humidity",
        "dev_class": "humidity",
        "unit": "%",
        "value_template": "{{ value_json.humidity }}",
        "precision": 1,
    },
    {
        "obj_id": "pressure",
        "name": f"{FRIENDLY} Pressure",
        "dev_class": "pressure",
        "unit": "hPa",
        "value_template": "{{ value_json.pressure }}",
        "precision": 1,
    },
]

for d in discovery:
    cfg_topic = f"{base}/{d['obj_id']}/config"
    payload = {
        "name": d["name"],
        "unique_id": f"{DEVICE_ID}_{d['obj_id']}",
        "state_topic": state_topic,
        "availability_topic": avail_topic,
        "value_template": d["value_template"],
        "device_class": d["dev_class"],
        "state_class": "measurement",
        "unit_of_measurement": d["unit"],
        "suggested_display_precision": d["precision"],
        "device": device_obj,
    }
    client.publish(cfg_topic, json.dumps(payload), retain=True)

client.publish(avail_topic, "online", retain=True)

# ---- Main loop: publish readings ----
try:
    while True:
        data = {
            "temperature": round(bme.temperature, 1),
            "humidity": round(bme.humidity, 1),
            "pressure": round(bme.pressure, 1),
        }
        client.publish(state_topic, json.dumps(data), retain=True)
        time.sleep(10)  # adjust cadence as you like
except KeyboardInterrupt:
    pass
finally:
    client.publish(avail_topic, "offline", retain=True)
    client.loop_stop()
    client.disconnect()
