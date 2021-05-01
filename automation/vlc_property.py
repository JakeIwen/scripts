import dbus
import sys
import time
import pprint
import re

req = sys.argv[1]
prop = req
iface = "Player"
format_time = False
sub_pattern = None
key = None

if req == 'Title':
    prop = 'Metadata'
    key = 'xesam:title'

elif req == 'URL':
    prop = 'Metadata'
    key = 'xesam:url'
    sub_pattern = "file:\/\/"
    
elif req == 'TotalTime':
    prop = 'Metadata'
    key = 'mpris:length'    
    format_time = True
    
elif req == 'Position':
    format_time = True
elif req == 'NS':
    prop = 'Position'
# not working
# elif req == 'TrackList':
#     iface = "TrackList"
#     prop = 'Tracks'
    
bus = dbus.SessionBus()
vlc_media_player_obj = bus.get_object("org.mpris.MediaPlayer2.vlc", "/org/mpris/MediaPlayer2")
props_iface = dbus.Interface(vlc_media_player_obj, 'org.freedesktop.DBus.Properties')
result = props_iface.Get("org.mpris.MediaPlayer2.{}".format(iface), prop)

if key:
    result = result[key]
if sub_pattern:
    result = re.sub(sub_pattern, "", result)
if format_time:
    seconds = round(result/1000/1000)
    result = time.strftime('%H:%M:%S', time.gmtime(seconds))
    
print(result)

    # 'mpris:length': dbus.Int64(1353696000, variant_level=1),
    # 'mpris:trackid': dbus.ObjectPath('/org/videolan/vlc/playlist/90', variant_level=1),
    # 'vlc:length': dbus.Int64(1353696, variant_level=1),
    # 'vlc:publisher': dbus.Int32(5, variant_level=1),
    # 'vlc:time': dbus.UInt32(1353, variant_level=1),
    # 'xesam:title': dbus.String('The_Simpsons_ S06E15', variant_level=1),
    # 'xesam:url': dbus.String('file:///mnt/bigboi/mp_backup/links/TV/The_Simpsons/T

# Properties
# PlaybackStatus	s (Playback_Status)	Read only		
# LoopStatus	s (Loop_Status)	Read/Write		(optional)
# Rate    	d (Playback_Rate)	Read/Write		
# Shuffle	b	Read/Write		(optional)
# Metadata	a{sv} (Metadata_Map)	Read only		
# Volume	d (Volume)	Read/Write		
# Position	x (Time_In_Us)	Read only		
# MinimumRate	d (Playback_Rate)	Read only		
# MaximumRate	d (Playback_Rate)	Read only		
# CanGoNext	b	Read only		
# CanGoPrevious	b	Read only		
# CanPlay	b	Read only		
# CanPause	b	Read only		
# CanSeek	b	Read only		
# CanControl	b	Read only	
