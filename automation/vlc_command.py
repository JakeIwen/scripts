import sys
import vlc
import getch
from sonos_tasks import sonos_audio_source

file = sys.argv[1]
# std_ref = sys.stdout
# sys.stdout = open('/dev/null', 'w')
# sys.stdout = std_ref

def vplay(p):
    sonos_audio_source('vonRear', 'line')
    print('playing')
    p.play()
    p.set_fullscreen(True)
    return True

def vpause(p):
    print('pausing')
    p.pause()
    return False

# Create Player Object and set MRL (your_video.mp4)
player = vlc.MediaPlayer(file)
# playing = vplay(player)

player.video_set_mouse_input(True)
player.video_set_key_input(True)

# try:
#     player.video_set_track(1)
# except Exception as e:
#     print(e)

playing = False

# import pdb; pdb.set_trace()

while True:
  try:
    print("Enter command: ")
    char = getch.getche()
    if char == ' ':
        playing = vpause(player) if playing else vplay(player)
  except Exception as e:
    print(e)

# Basic Commands associated with the class object
# p.play()
# p.pause()
# p.stop()