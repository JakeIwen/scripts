import sys
import soco
from soco.discovery import by_name, any_soco, discover
from soco.data_structures import DidlItem, DidlResource
from soco.music_library import MusicLibrary
from soco.compat import quote_url


def adjust(target):
    if direction == "up":
        target.volume += inc
    elif direction == "down":
        target.volume -= inc
    elif direction == "mute":
        target.mute = not target.mute

def adjust_volume(speaker, direction, inc):
    if speaker == 'all': 
        for group in any_soco().all_groups:
            adjust(group) 
        print (group.volume)

    else:
        device = by_name(speaker)
        adjust(device)
        print (device.volume )

def set_partymode_vol(vol=30):
    any_soco().partymode()
    for device in discover():
        device.mute = False
        device.volume = vol
 
def unjoin_all():
    for group in any_soco().all_groups:
        for member in group:
            try:
                member.unjoin()
            except:
                continue
        
def stop_all():
    unjoin_all()
    
def partymode():
    any_soco().partymode()
    
def start_noise():
    unjoin_all()
    set_partymode_vol(10)
    device = any_soco()
    ml = MusicLibrary(device)
    track = ml.get_sonos_favorites()[0]
    device.clear_queue()
    device.add_to_queue(track)
    device.play_from_queue(0)
    device.play_mode = 'REPEAT_ONE'

# import pdb; pdb.set_trace()