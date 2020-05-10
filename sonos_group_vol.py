import sys
import soco
from soco.discovery import by_name

direction = sys.argv[1]
inc = int(sys.argv[2])

any_device = by_name("vanFront") or by_name("vonRear") or by_name("vonMid")

for group in any_device.all_groups:
    if direction == "up":
        group.volume += inc
    elif direction == "down":
        group.volume -= inc
    
    