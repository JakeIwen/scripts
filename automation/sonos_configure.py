from sonos_tasks import get_playing_device, get_spkr, all_devices

def ensure_group():
    devices = all_devices()
    for device in devices:
        if not device.is_visible:
            continue
        t_state = device.get_current_transport_info()['current_transport_state']
        if t_state not in ['PAUSED_PLAYBACK', 'STOPPED']:
            return print("in use, no action taken")

    device = next((x for x in devices if x.player_name == 'vonFront'), None)
    if len(device.group.members) == len(devices):
        return print("already grouped")

    print("not in use, grouping")
    device.partymode()

ensure_group()
