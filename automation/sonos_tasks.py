from random import choice
from soco.discovery import by_name, any_soco, discover
from soco.music_library import MusicLibrary
from contextlib import suppress
from time import sleep

#  abstractions

def group_vol_up(inc=1):
    adjust_volume('all', 'up', inc)
def group_vol_down(inc=8):
    adjust_volume('all', 'down', inc)

def brown_noise():
    start_noise('Brown Noise')
def pink_noise():
    start_noise('Pink Noise')
    
def discover_weekly():
    play_from_faves('Discover Weekly', True, 42)
def random_album():
    play_from_faves(" - ")
def random_radio():
    play_from_faves(" Radio")

def rear_movie():
    audio_source('vonRear', 'optical')

def rear_normal():
    make_stereo_pair("vonRear", "vonRear2")
def rear_inverted():
    make_stereo_pair("vonRear2", "vonRear")
    
def back_15():
    scrub(-15)
def back_30():
    scrub(-30)

# utilities
def adjust_volume(speaker, direction, inc=8):
    if speaker == 'all': 
        [adjust(group, direction, inc) for group in any_soco().all_groups]
    else:
        adjust(get_spkr(speaker), direction, inc)

def play(name=None):
    device = get_spkr(name) if name else get_preferred_device()
    device.play()
    return device

def pause(devices = discover()):
    [device.pause() for device in devices]
    
def stop():
    [device.stop() for device in discover()]

def start_noise(keyterm):
    cooridnator = partymode(9)
    item = get_matching_faves(keyterm, cooridnator)[0]
    play_item(cooridnator, item, 'REPEAT_ONE')
    return cooridnator

def play_from_faves(keyterm, group_all=True, group_vol=None):
    device = partymode() if group_all else get_preferred_device()
    matches = get_matching_faves(keyterm, device)
    matches = [x for x in matches if 'Noise' not in x.title]
    item = choice(matches) # choose random if multiple matches
    play_item(device, item.reference, 'NORMAL')
    return device

def audio_source(name, source):
    unjoin_all()
    device = get_spkr(name)

    if source == "optical":
        source_optical(device)
    elif source == "line":
        device.switch_to_line_in(get_spkr("vonFront"))
        device.play()
    
    device.mute = False
    device.volume = 90
    return device

def scrub(seconds=-15):
    device = get_preferred_device()
    position = device.get_current_track_info()['position']
    new_seektime = add_time(position, int(seconds))
    device.seek(new_seektime)

def add_to_group(name):
    get_spkr(name).join(get_preferred_device())

def remove_from_group(name):
    get_spkr(name).unjoin()

def partymode(vol=None):
    device = get_preferred_device()
    if len(device.group.members) < 3:
        device.partymode()
    if vol:
        [set_vol(member, vol) for member in device.group]
    return device 
    
def unjoin_all(devices=discover()):
    for device in devices:
        if len(device.group.members) > 1:
            with suppress(Exception): member.unjoin()
    return devices

def test():
    cooridnator = partymode(9)
    ml = MusicLibrary(cooridnator)
    faves = ml.get_sonos_favorites()
    import pdb; pdb.set_trace()

#  helpers
def play_item(device, item, play_mode='NORMAL'):
    device.clear_queue()
    device.add_to_queue(item)
    device.play_from_queue(0)
    device.play_mode = play_mode

def add_time(position, diff_secs):
    timeList = [ '0:00:00', '0:00:15', '9:30:56' ]
    totalSecs = diff_secs
    timeParts = [int(s) for s in position.split(':')]
    totalSecs += (timeParts[0] * 60 + timeParts[1]) * 60 + timeParts[2]
    totalSecs, sec = divmod(totalSecs, 60)
    hr, min = divmod(totalSecs, 60)
    new_timestamp = "%d:%02d:%02d" % (hr, min, sec)
    return "0:00:00" if new_timestamp.startswith("-") else new_timestamp

def adjust(target, direction, inc):
    orig = target.volume
    diff = int(inc)
    if orig < 15:
        diff = diff/2
    if direction == "up":
        target.volume = orig + diff + 2
    elif direction == "down":
        target.volume = orig - diff - 2
    
    target.mute = (direction == "mute")

def set_vol(target, vol):
    target.mute = False
    target.volume = int(vol)
    
def source_optical(device):
    if device.player_name == "vonMid":
        device.switch_to_tv()
    else:
        mid = get_spkr("vonMid")
        mid.switch_to_tv()
        mid.partymode()
        mid.play()
        mid.group.mute = True
        
def make_stereo_pair(left_master_name, right_name):
    slave = by_name(right_name)
    with suppress(Exception): slave.separate_stereo_pair()
    master = by_name(left_master_name)
    while not master:
        sleep(1)
        master = by_name(left_master_name)
    with suppress(Exception): master.create_stereo_pair(slave)
    return master

def get_matching_faves(keyterm, device=None):
    if device == None:
        device = get_preferred_device()
    ml = MusicLibrary(device)
    faves = ml.get_sonos_favorites()
    return [x for x in faves if keyterm in x.title]

def get_spkr(name):
    return by_name(name) or by_name(name + '2') 

def get_playing_device(default_to_any=False):
    devices = discover()
    for device in devices:
        if len(device.group.members) > 1:
            return device.group.coordinator 
    for device in devices:
        device.t_state = device.get_current_transport_info()['current_transport_state']
        if device.t_state == 'PLAYING':
            return device.group.coordinator
    for device in devices:
        if device.t_state == 'PAUSED_PLAYBACK':
            return device.group.coordinator
    if default_to_any:
        return devices.pop()
        
def get_preferred_device():
    return get_playing_device(True)

# import pdb; pdb.set_trace()