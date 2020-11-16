import vlc
sys.argv[1]

# Create Player Object and set MRL (your_video.mp4)
p = vlc.MediaPlayer(sys.argv[1])

def play():
    
    p.play()

# Basic Commands associated with the class object
p.play()
p.pause()
p.stop()