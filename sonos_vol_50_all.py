import soco
from soco.discovery import by_name

# soco.discovery.any_soco().partymode()

by_name("vanFront").partymode()
by_name("vanFront").mute = False
by_name("vanFront").volume = 50 
by_name("vonMid").mute = False
by_name("vonMid").volume = 50
by_name("vonRear").mute = False
by_name("vonRear").volume = 50

