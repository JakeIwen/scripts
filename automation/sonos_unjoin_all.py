import soco
from soco.discovery import by_name, any_soco

for group in any_soco().all_groups:
    for member in group:
        try:
            member.unjoin()
        except:
            continue