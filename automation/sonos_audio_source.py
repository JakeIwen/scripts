import sys
import soco
from soco.discovery import by_name
# import pdb; pdb.set_trace()
# docs: https://readthedocs.org/projects/soco/downloads/pdf/stable/
device_name =  sys.argv[1]
audio_source = sys.argv[2]

def sonos_audio_source(name, source):
    device = by_name(name) or by_name(name + "2")
    print(device)

    if source == "optical":
        mid = by_name("vonMid")
        mid.switch_to_tv()
        if name == "vonMid":
            exit()
        mid.partymode()
        mid.group.mute = True
        
    elif source == "line":
        device.switch_to_line_in(by_name("vonFront"))
        
    device.mute = False
    device.volume = 90
    device.play()

sonos_audio_source(device_name, audio_source)