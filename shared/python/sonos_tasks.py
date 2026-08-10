from random import choice
from soco.discovery import by_name, any_soco, discover
from soco.exceptions import SoCoUPnPException
from soco.music_library import MusicLibrary
from contextlib import suppress
from time import monotonic, sleep
from xml.etree import ElementTree
import os

REAR_PHYSICAL_LEFT_UID = "RINCON_7828CA20F21A01400"
REAR_PHYSICAL_RIGHT_UID = "RINCON_7828CA20F1DA01400"
STEREO_PAIR_TIMEOUT = 30
STEREO_PAIR_RETRYABLE_ERRORS = {"402", "1034"}

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
    return audio_source_device(get_rear_stereo_master(), 'optical', vol)
def rear_movie_resume():
    device = get_rear_stereo_master()
    return audio_source_device(device, 'optical', int(device.volume))
def rear_normal():
    return make_stereo_pair_by_uid(
        REAR_PHYSICAL_LEFT_UID,
        REAR_PHYSICAL_RIGHT_UID,
    )
def rear_inverted():
    return make_stereo_pair_by_uid(
        REAR_PHYSICAL_RIGHT_UID,
        REAR_PHYSICAL_LEFT_UID,
    )

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
    master = by_name(left_master_name)
    slave = by_name(right_name)
    if not master or not slave:
        raise RuntimeError(
            "Could not find both speakers: "
            + left_master_name
            + ", "
            + right_name
        )
    return make_stereo_pair_by_uid(master.uid, slave.uid)

def make_stereo_pair_by_uid(left_uid, right_uid, timeout=STEREO_PAIR_TIMEOUT):
    desired_map = stereo_channel_map(left_uid, right_uid)
    speakers = wait_for_speakers((left_uid, right_uid), timeout)
    current_maps = get_stereo_channel_maps(speakers.values())

    if desired_map in current_maps:
        return speakers[left_uid]

    existing_map = next(
        (
            channel_map
            for channel_map in current_maps
            if left_uid in channel_map and right_uid in channel_map
        ),
        None,
    )
    if existing_map:
        speakers[left_uid].separate_stereo_pair()
        speakers = wait_for_speakers(
            (left_uid, right_uid),
            timeout,
            require_visible=True,
        )
    elif not all(speaker.is_visible for speaker in speakers.values()):
        raise RuntimeError(
            "Rear speakers are bonded in an unexpected configuration; "
            "refusing to change it automatically"
        )

    return create_stereo_pair_with_retry(left_uid, right_uid, timeout)

def create_stereo_pair_with_retry(left_uid, right_uid, timeout):
    desired_map = stereo_channel_map(left_uid, right_uid)
    deadline = monotonic() + timeout
    last_error = None
    while monotonic() < deadline:
        speakers = discover_speakers_by_uid((left_uid, right_uid))
        if len(speakers) != 2:
            sleep(1)
            continue
        try:
            speakers[left_uid].create_stereo_pair(speakers[right_uid])
        except SoCoUPnPException as error:
            if str(error.error_code) not in STEREO_PAIR_RETRYABLE_ERRORS:
                raise
            # Some speakers reject AddBondedZones when contacted as the new
            # left member but accept the same ChannelMapSet via the other
            # member of the pair.
            try:
                speakers[right_uid].deviceProperties.AddBondedZones(
                    [("ChannelMapSet", desired_map)]
                )
            except SoCoUPnPException as alternate_error:
                if (
                    str(alternate_error.error_code)
                    not in STEREO_PAIR_RETRYABLE_ERRORS
                ):
                    raise
                last_error = alternate_error
                sleep(2)
                continue
        remaining = max(1, deadline - monotonic())
        return wait_for_stereo_pair(left_uid, right_uid, remaining)
    raise RuntimeError(
        "Timed out creating Sonos stereo pair after temporary UPnP errors"
    ) from last_error

def discover_speakers_by_uid(uids, timeout=3):
    wanted = set(uids)
    devices = discover(timeout, True) or set()
    return {device.uid: device for device in devices if device.uid in wanted}

def wait_for_speakers(uids, timeout, require_visible=False):
    deadline = monotonic() + timeout
    speakers = {}
    while monotonic() < deadline:
        speakers = discover_speakers_by_uid(uids)
        if all(uid in speakers for uid in uids):
            if not require_visible or all(
                speakers[uid].is_visible for uid in uids
            ):
                return speakers
        sleep(1)
    state = "visible " if require_visible else ""
    missing = [
        uid
        for uid in uids
        if uid not in speakers or (require_visible and not speakers[uid].is_visible)
    ]
    raise RuntimeError(
        "Timed out waiting for "
        + state
        + "speakers; missing UIDs: "
        + ", ".join(missing)
    )

def stereo_channel_map(left_uid, right_uid):
    return left_uid + ":LF,LF;" + right_uid + ":RF,RF"

def get_stereo_channel_maps(devices):
    last_error = None
    for device in devices:
        try:
            state = device.zoneGroupTopology.GetZoneGroupState()["ZoneGroupState"]
            root = ElementTree.fromstring(state)
            return {
                member.attrib["ChannelMapSet"]
                for member in root.iter("ZoneGroupMember")
                if member.attrib.get("ChannelMapSet")
            }
        except Exception as error:
            last_error = error
    raise RuntimeError("Could not read Sonos stereo-pair topology") from last_error

def wait_for_stereo_pair(left_uid, right_uid, timeout):
    desired_map = stereo_channel_map(left_uid, right_uid)
    deadline = monotonic() + timeout
    while monotonic() < deadline:
        speakers = discover_speakers_by_uid((left_uid, right_uid))
        if len(speakers) == 2:
            if desired_map in get_stereo_channel_maps(speakers.values()):
                return speakers[left_uid]
        sleep(1)
    raise RuntimeError(
        "Timed out waiting for Sonos stereo pair: " + desired_map
    )

def get_rear_stereo_master():
    rear_uids = (REAR_PHYSICAL_LEFT_UID, REAR_PHYSICAL_RIGHT_UID)
    speakers = wait_for_speakers(rear_uids, STEREO_PAIR_TIMEOUT)
    visible = [speaker for speaker in speakers.values() if speaker.is_visible]
    if len(visible) != 1:
        raise RuntimeError(
            "Rear speakers are not currently configured as one stereo pair"
        )
    return visible[0]

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
    return audio_source_device(get_spkr(name), source, vol)

def audio_source_device(device, source, vol=80):
    unjoin_all()

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
    cmd = "osascript /Users/jacobr/dev/scripts/macbook/applescript/sonosAudio.scpt " + name
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
