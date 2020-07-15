import sys
import soco
from soco.discovery import by_name

speaker = sys.argv[1]
direction = sys.argv[2]
inc = int(sys.argv[3])

def adjust_volume(target):
    if direction == "up":
        target.volume += inc
    elif direction == "down":
        target.volume -= inc
    elif direction == "mute":
        target.mute = not target.mute

# import pdb; pdb.set_trace()

if speaker == 'all': 
    any_device = by_name("vanFront") or by_name("vonRear") or by_name("vonMid")
    for group in any_device.all_groups:
        adjust_volume(group)
    print group.volume

else:
    device = by_name(speaker)
    adjust_volume(device)
    print device.volume
    
