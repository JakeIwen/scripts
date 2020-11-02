import sys
import soco
from soco.discovery import by_name
# import pdb; pdb.set_trace()
# docs: https://readthedocs.org/projects/soco/downloads/pdf/stable/
device_name =  sys.argv[1]
audio_source = sys.argv[2]

device = by_name(device_name) or by_name(device_name + "2")
print(device)

if audio_source == "optical":
    mid = by_name("vonMid")
    mid.switch_to_tv()
    if device_name == "vonMid":
        exit()
    
    mid.partymode()
    mid.group.mute = True
    device.mute = False
    device.volume = 90
    
elif audio_source == "line":
    device.switch_to_line_in(by_name("vonFront"))
    device.mute = False
    device.volume = 80 

