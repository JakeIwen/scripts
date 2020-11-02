import soco
from soco.discovery import by_name

front = by_name("vonFront")
mid = by_name("vonMid")
rear = by_name("vonRear") or by_name("vonRear2")

front.partymode()
front.mute = False
front.volume = 50 
mid.mute = False
mid.volume = 50
rear.mute = False
rear.volume = 50

