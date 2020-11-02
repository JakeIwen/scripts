import soco
from soco.discovery import by_name

front = by_name("vonFront")
mid = by_name("vonMid")
rear = by_name("vonRear") or by_name("vonRear2")

front.partymode()
front.mute = True
mid.mute = True
rear.volume = 90
