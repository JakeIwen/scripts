import soco
# r1 = soco.core.SoCo("192.168.6.193")
# r2 = soco.core.SoCo("192.168.6.200")
r1 = soco.discovery.by_name("vonRear2")

try:
    r1.separate_stereo_pair()
except Exception:
    pass
r2 = soco.discovery.by_name("vonRear")
r2.create_stereo_pair(r1)

