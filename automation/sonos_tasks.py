from random import choice
from soco.discovery import by_name, any_soco, discover
from soco.music_library import MusicLibrary
from contextlib import suppress
from time import sleep
import os

# to make sonos_tasks globally importable
# run:
# import site
# site.getusersitepackages()
# 
# create file in site-packages directory o-.py:
# import sys
# sys.path.extend(['/path/to/this/ditectory'])

def filter_vis_devices():
    return [x for x in all_devices if x.is_visible] 

all_devices = discover(5, True)
vis_devices = filter_vis_devices()

#  abstractions

def brown_noise():
    start_noise('Brown Noise')
def pink_noise():
    start_noise('Pink Noise')

def discover_weekly():
    play_from_faves('Discover Weekly')
def random_album():
    play_from_faves(" - ")
def random_radio():
    play_from_faves(" Radio")

def group_vol_set(val):
    adjust_volume('all', 'set', val)
def group_vol_up(inc=8):
    adjust_volume('all', 'up', inc)
def group_vol_down(inc=8):
    adjust_volume('all', 'down', inc)
def vol_up(inc=8):
    adjust_volume('preferred', 'up', inc)
def vol_down(inc=8):
    adjust_volume('preferred', 'down', inc)

def back_15():
    scrub(-15)
def back_30():
    scrub(-30)
def fwd_15():
    scrub(15)
def fwd_30():
    scrub(30)
    
def next_track():
    get_preferred_device().next()
def prev_track():
    get_preferred_device().previous()
    
def rear_movie(vol=47):
    audio_source('vonRear', 'optical', vol)
def rear_normal():
    make_stereo_pair("vonRear", "vonRear2")
def rear_inverted():
    make_stereo_pair("vonRear2", "vonRear")

# soundbyte options in ~/soundbytes
# chime warn success error deactivate
def play_soundbyte(name, device_name='vonRear'):
    device = get_spkr(device_name)
    remove_from_group(device_name) # will be a problem if device.is_coordinator
    orig_vol = device.volume
    device.volume = 80
    chime_uri = "http://vanpi.local:8000/" + name + ".mp3"
    device.play_uri(chime_uri)

    sleep(5)
    device.volume = orig_vol
    add_to_main_group(device_name)

# utilities

def make_stereo_pair(left_master_name, right_name):
    # import pdb; pdb.set_trace()
    
    slave = by_name(right_name)
    with suppress(Exception): slave.separate_stereo_pair()
    master = by_name(left_master_name)
    while not master:
        sleep(1)
        master = by_name(left_master_name)
    
    with suppress(Exception): master.create_stereo_pair(slave)
    vis_devices = filter_vis_devices();
    return master

def vol_eql_all(vol=50):
    [equal_vol(member, vol, True) for member in get_preferred_device().group]
    
def adjust_volume(speaker, direction, val=8):
    if speaker == 'all':
        [adjust(group, direction, val) for group in any_soco().all_groups]
    elif speaker == 'preferred':
        adjust(get_playing_device(), direction, val)
    else:
        adjust(get_spkr(speaker), direction, val)

def play(name=None):
    device = get_spkr(name) if name else get_preferred_device()
    device.play()
    return device

def pause(devices=vis_devices):
    [device.pause() for device in devices]
    
def stop(devices=vis_devices):
    [device.stop() for device in devices]

def mute(devices=vis_devices):
    for device in devices:
        device.mute = True
    
def start_noise(keyterm, vol=35):
    cooridnator = partymode(vol)
    item = get_matching_faves(keyterm, cooridnator)[0]
    print("noise to play:")
    print(item)
    play_item(cooridnator, item, 'REPEAT_ONE')
    crossfade_on(cooridnator)
    return cooridnator

def play_from_faves(keyterm, group_all=True):
    device = partymode() if group_all else get_preferred_device()
    matches = get_matching_faves(keyterm, device)
    matches = [x for x in matches if 'Noise' not in x.title]
    item = choice(matches) # choose random if multiple matches
    play_item(device, item.reference, 'NORMAL')
    # import pdb; pdb.set_trace()
    
    return device

def audio_source(name, source, vol=80):
    unjoin_all()
    device = get_spkr(name)

    if source == "optical":
        source_optical(device)
    elif source == "line":
        device.switch_to_line_in(get_spkr("vonFront"))
        device.play()
    
    mute()
    device.mute = False
    device.volume = vol
    return device

def scrub(seconds=-15):
    device = get_preferred_device()
    position = device.get_current_track_info()['position']
    new_seektime = add_time(position, int(seconds))
    device.seek(new_seektime)

def add_to_main_group(name):
    get_spkr(name).join(get_preferred_device())

def remove_from_group(name):
    get_spkr(name).unjoin()
def partymode(vol=None, device=None):
    device = device or get_preferred_device()
    
    if len(device.group.members) < len(all_devices):
        device.partymode() 
    vol = vol or device.volume
    print(vol)
    [equal_vol(member, vol, False) for member in device.group]
    return device 
    
def unjoin_all(devices=vis_devices):
    for device in devices:
        if device.group.coordinator.player_name == device.player_name:
            with suppress(Exception): device.stop()
        if is_group_member(device):
            print(device.player_name, "unjoining")
            device.unjoin()
    return devices
    
def standby_grouped(devices=all_devices, coord_name='vonFront'):
    # broken, kills vlc Rear_movie audio
    return True
    # for device in devices:
    #     if not device.is_visible:
    #         continue
    #     t_state = device.get_current_transport_info()['current_transport_state']
    #     print("tstate:")
    #     print(t_state)
    #     if t_state not in ['PAUSED_PLAYBACK', 'STOPPED']:
    #         return print("in use, no action taken")
    # 
    # device = next((x for x in devices if x.player_name == coord_name), None)
    # if len(device.group.members) == len(devices):
    #     return print("already grouped")
    # 
    # print("not in use, grouping")
    # return partymode(None, device)

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
    totalSecs = diff_secs
    timeParts = [int(s) for s in position.split(':')]
    totalSecs += (timeParts[0] * 60 + timeParts[1]) * 60 + timeParts[2]
    totalMin, sec = divmod(totalSecs, 60)
    hr, min = divmod(totalMin, 60)
    new_timestamp = "%d:%02d:%02d" % (hr, min, sec)
    return "0:00:00" if new_timestamp.startswith("-") else new_timestamp

def adjust(target, direction, amount):
    orig = target.volume
    diff = int(amount)
    if orig < 15:
        diff = diff/2
    
    if (direction == "mute"):
        target.mute = not target.mute
    elif direction == "up":
        target.volume = orig + diff + 2
    elif direction == "down":
        target.volume = orig - diff - 2
    elif direction == "set":
        target.volume = amount

def equal_vol(target, vol, preserve_mute):
    if not preserve_mute:
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

def get_matching_faves(keyterm, device=None):
    device = device or get_preferred_device()
    ml = MusicLibrary(device)
    faves = ml.get_sonos_favorites()
    return [x for x in faves if keyterm in x.title]

def direct(name): # only on macbook
    cmd = "osascript /Users/jacobr/dev/scripts/automation/sonosAudio.scpt " + name
    os.system(cmd)

def get_spkr(name):
    return by_name(name) or by_name(name + '2')

def get_playing_device(default_to_front=False, devices=vis_devices):
    # if the device is in a group, then it is the ONLY group ( <4 stereo-pairs )
    # otherwise return the active PLAYING or PAUSED device
    for device in devices:
        if is_group_member(device):
            print("returning group coord", device.group.coordinator.player_name)
            return device.group.coordinator 
    for device in devices:
        device.t_state = device.get_current_transport_info()['current_transport_state']
        if device.t_state == 'PLAYING':
            print("is playing", device.group.coordinator.player_name)
            return device.group.coordinator
    for device in devices:
        if device.t_state == 'PAUSED_PLAYBACK':
            print("is paused", device.group.coordinator.player_name)
            return device.group.coordinator
    if default_to_front:
        print("defaulting to vonFront")
        return next((x for x in devices if x.player_name == 'vonFront'), None)
    else:
        return None

def get_preferred_device(devices=vis_devices):
    return get_playing_device(True, devices)

def is_group_member(device):
    base_num = 2 if "vonRear" in device.player_name else 1
    return len(device.group.members) > base_num

def crossfade_on(device=None):
    (device or get_preferred_device()).cross_fade = True
def crossfade_off(device=None):
    (device or get_preferred_device()).cross_fade = False    
def num_devices():
    return len(all_devices)

# partymode()
# rear_normal()
# partymode()
# unjoin_all()
# print("attempting crossfade", crossfade())

# import pdb; pdb.set_trace()
# random_album()
# discover_weekly()
