#!/usr/bin/env python 
import socket 
s=socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
print s.sendto('\xff'*6+'\xb8\xe8\x56\x0c\x81\xf2'*16, ('192.168.6.255', 80))
# "b8:e8:56:0c:81:f2" device mac


# vlc && osascript /Users/jacobr/dev/scripts/audioVanFront.scpt && python sonos_rear_movie.py