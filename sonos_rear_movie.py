import soco
from soco.discovery import by_name

# soco.discovery.any_soco().partymode()

by_name("vanFront").partymode()
by_name("vanFront").mute = True
by_name("vonMid").mute = True
by_name("vonRear").volume = 90