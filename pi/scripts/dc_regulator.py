import time
import sys
import RPi.GPIO as GPIO
import obd

GPIO.setmode(GPIO.BCM)
GPIO.setup(12, GPIO.OUT)

dc = 0 # duty cycle
v_ecu = 0
v_obd = 0
v_min = 13.4
v_max = 14
rpm = 0
rpm_increasing = True
voltage_increasing = False
MAX_FIELD = 100
last_response_at = None

obd_link = obd.Async()
pwm = GPIO.PWM(12, 113)  # channel=12 frequency=113Hz
start_time = int(time.time())

if len(sys.argv) == 2:
    v_min = sys.argv[1]
    v_max = sys.argv[2]

def print_time():
    time_now = time.time()
    print(f"time elapsed: {time_now - start_time}s")

def set_duty(change=0):
    global dc
    dc = dc + change
    dc = min(dc, MAX_FIELD)
    dc = max(0, dc)
    print(f"v_ecu: {v_ecu}, v_obd: {v_obd}, dc: {dc}, changed: {change}")
    pwm.ChangeDutyCycle(dc)

def decrease_duty_cycle():
    v_diff = v_ecu - v_max
    dc_change = -40 * v_diff
    set_duty(dc_change)

def increase_duty_cycle():
    v_diff = v_min - v_ecu
    dc_change = 5 * v_diff
    set_duty(dc_change)

def kill_duty_cycle():
    global dc
    dc = 0
    pwm.ChangeDutyCycle(dc)
    print("killed duty cycle")
    
def voltage_in_range():
    if not v_ecu or v_ecu > 14.6 or v_ecu < 10:
        kill_duty_cycle()
        print(f"Error v_ecu out of range v_ecu=={v_ecu}, v_obd=={v_obd}")
        return False
    else:
        return True

def comm_synced():
    last_update = time.time() - last_response_at
    if last_update > 0.4:
        kill_duty_cycle()
        print(f"no update for {last_update}s")

def ensure_failsafes():
    voltage_in_range()
    comm_synced()
    
def regulate_pwm_duty_cycle():
    if not voltage_in_range():
        return None
    elif v_ecu > v_max:
        decrease_duty_cycle()
    elif rpm_increasing and voltage_increasing:
        return None
    elif v_ecu < v_min:
        increase_duty_cycle()

def ecu_voltage_update(r):
    global v_ecu, last_response_at, voltage_increasing
    v_now = r.value.magnitude
    v_diff = v_now - v_ecu
    v_ecu, last_response_at, voltage_increasing = v_now, r.time, v_diff > 0
    
    if v_diff > 0.3:
        return kill_duty_cycle()
    elif voltage_increasing:
        ratio = v_diff / v_ecu
        print(f"normalizing duty by {-100 * ratio}% for voltage increase")
        set_duty(-100 * ratio)
    
    regulate_pwm_duty_cycle()
    
def rpm_update(r):
    global rpm
    global rpm_increasing
    new_rpm = r.value.magnitude
    if rpm > 600:
        ratio = (new_rpm - rpm) / rpm
        rpm_increasing = new_rpm > rpm
        if rpm_increasing:
            print(f"normalizing duty by {-100 * ratio}% for rpm increase")
            set_duty(-100 * ratio)
    rpm = new_rpm

def obd_voltage_update(r):
    global v_obd
    v_obd = r.value.magnitude

def end_session():
    pwm.stop()
    obd_link.stop()
    GPIO.cleanup()
    print("CLean session end.")

def init():
    # import pdb; pdb.set_trace()
    
    obd_link.watch(obd.commands.CONTROL_MODULE_VOLTAGE, callback=ecu_voltage_update)
    obd_link.watch(obd.commands.RPM, callback=rpm_update)
    obd_link.watch(obd.commands.ELM_VOLTAGE, callback=obd_voltage_update)
    
    obd_link.start()
    pwm.start(dc)
    while not last_response_at: # wait for initial reading
        time.sleep(0.1)
    
    try:
        while 1:
            ensure_failsafes()
            time.sleep(0.1)
    except:
        kill_duty_cycle()
        print("Unexpected error, killing duty cycle:", sys.exc_info()[0])
    
    end_session()

init()