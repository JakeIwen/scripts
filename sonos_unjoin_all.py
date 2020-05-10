import soco
from soco.discovery import by_name

any_device = by_name("vanFront") or by_name("vonRear") or by_name("vonMid")

for group in any_device.all_groups:
    for member in group:
        try:
            member.unjoin()
        except:
            continue