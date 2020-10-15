import soco
from soco.discovery import by_name

by_name("vonFront").partymode()
by_name("vonFront").mute = True
by_name("vonMid").mute = True
by_name("vonRear").volume = 90