import soco
from soco.discovery import by_name

any_device = by_name("vanFront") or by_name("vonRear") or by_name("vonMid")

any_device.partymode()