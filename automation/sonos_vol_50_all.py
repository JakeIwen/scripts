import soco
from soco.discovery import by_name

by_name("vonFront").partymode()
by_name("vonFront").mute = False
by_name("vonFront").volume = 50 
by_name("vonMid").mute = False
by_name("vonMid").volume = 50
by_name("vonRear").mute = False
by_name("vonRear").volume = 50

