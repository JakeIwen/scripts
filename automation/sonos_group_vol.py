import sys
import soco
from soco.discovery import by_name, any_soco

device_name = sys.argv[1]
direction = sys.argv[2]
inc = int(sys.argv[3])

def adjust_volume(target):
    if direction == "mute":
        target.mute = not target.mute
    else:
        target.mute = False
    
    if direction == "up":
        target.volume += inc
    elif direction == "down":
        target.volume -= inc
    
# import pdb; pdb.set_trace()

if device_name == 'all': 
    for group in any_soco().all_groups:
        adjust_volume(group)
    print(group.volume)
else:
    device = by_name(device_name) or by_name(device_name + "2")
    adjust_volume(device)
    print(device.volume)
    
