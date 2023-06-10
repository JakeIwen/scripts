import sys
import vlc
import getch
from sonos_tasks import audio_source 

file = sys.argv[1]

def vplay(p):
    audio_source('vonRear', 'line')
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
# xzxz1    print(e)
    


# Basic Commands associated with the class object
# p.play()
# p.pause()
# p.stop()