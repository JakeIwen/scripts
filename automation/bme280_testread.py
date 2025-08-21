import time
import board
import adafruit_bitbangio as bitbangio
from adafruit_bme280.basic import Adafruit_BME280_I2C

# Software I2C on GPIO24 (SCL, pin 18), GPIO23 (SDA, pin 16)
i2c = bitbangio.I2C(board.D24, board.D23, frequency=50_000)

# Initialize the sensor
bme280 = Adafruit_BME280_I2C(i2c, address=0x76)

while True:
    print(f"T: {bme280.temperature:.1f} °C  H: {bme280.humidity:.1f} %  P: {bme280.pressure:.1f} hPa")
    time.sleep(2)
