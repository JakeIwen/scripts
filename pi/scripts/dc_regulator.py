import time
import sys
import RPi.GPIO as GPIO
import obd

GPIO.setmode(GPIO.BOARD)
GPIO.setup(12, GPIO.OUT)

obd_link = obd.Async("/dev/rfcomm0")
pwm = GPIO.PWM(12, 113)  # channel=12 frequency=113Hz
start_time = int(time.time())
dc = 0
v_ecu = 0
v_min = 13.8
v_max = 14.2
last_response_timestamp = None

if len(sys.argv) == 2:
    v_min = sys.argv[1]
    v_max = sys.argv[2]

def print_time():
    time_now = time.time()
    print(f"time elapsed: {time_now - start_time}s")

def set_duty(change=0):
    global dc
    dc = dc + change
    dc = min(dc, 90)
    dc = max(0, dc)
    print(f"voltage: {v_ecu}, dc: {dc}, change: {change}")
    pwm.ChangeDutyCycle(dc)

def decrease_duty_cycle():
    v_diff = v_ecu - v_max
    dc_change = -50 * v_diff
    set_duty(dc_change)

def increase_duty_cycle():
    v_diff = v_min - v_ecu
    dc_change = 50 * v_diff
    set_duty(dc_change)

def time_since_update():
    return time.time() - last_response_timestamp
    
def ensure_voltage_in_range():
    if not v_ecu or v_ecu > 15 or v_ecu < 10:
        pwm.ChangeDutyCycle(0)
        print(f"Error v_ecu out of range V=={v_ecu}")
        return False
    else:
        return True
    
def regulate_cycle():
    if not ensure_voltage_in_range():
        pass
    elif v_ecu > v_max:
        decrease_duty_cycle()
    elif v_ecu < v_min:
        increase_duty_cycle()

def ensure_failsafes():
    ensure_voltage_in_range()
    last_update = time_since_update()
    print(f"last update: {last_update}")
    if last_update > 0.4:
        pwm.ChangeDutyCycle(0)
        print(f"no update for {last_update}s, killed duty cycle")

def new_voltage(r):
    global v_ecu
    global last_response_timestamp
    v_ecu = r.value.magnitude
    last_response_timestamp = r.time
    print(f"updated voltage: {v_ecu}v")
    regulate_cycle()

def end_session():
    pwm.stop()
    obd_link.stop()
    GPIO.cleanup()

def init():
    obd_link.watch(obd.commands.CONTROL_MODULE_VOLTAGE, callback=new_voltage)
    obd_link.start()
    pwm.start(dc)
    while not last_response_timestamp: # wait for initial reading
        time.sleep(0.1)
    
    try:
        while 1:
            ensure_failsafes()
            time.sleep(0.1)
    except KeyboardInterrupt:
        pass
    except:
        pwm.ChangeDutyCycle(0)
        print("Unexpected error, killing duty cycle:", sys.exc_info()[0])
    
    end_session()

init()