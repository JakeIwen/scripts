"""Dashboard HTTP adapter for cab-light dimming."""
import os
from urllib import parse

from van_dashboard_http import DASHBOARD_URL, DashboardError, dashboard_json

from .model import DimOutcome, DimResult

DASHBOARD_URL_ENV = "DISPLAY_CAB_DIMMER_DASHBOARD_URL"
CAB_GROUP = "cab"
HTTP_TIMEOUT_SECONDS = 8.0


def _cab_lights(payload: dict) -> list[dict]:
    try:
        groups = payload["lighting"]["groups"]
    except (KeyError, TypeError) as error:
        raise DashboardError("lights response has no groups") from error
    if not isinstance(groups, list):
        raise DashboardError("lights response groups are invalid")
    for group in groups:
        if isinstance(group, dict) and group.get("id") == CAB_GROUP:
            lights = group.get("lights")
            if not isinstance(lights, list):
                raise DashboardError("cab lights response is invalid")
            return lights
    raise DashboardError("cab light group is unavailable")


def dim_cab_lights(brightness: int, *, dashboard_url: str | None = None,
                   timeout: float = HTTP_TIMEOUT_SECONDS, logger=print) -> DimResult:
    base_url = dashboard_url or os.environ.get(DASHBOARD_URL_ENV, DASHBOARD_URL)
    changed = 0
    try:
        lights = _cab_lights(dashboard_json(f"{base_url.rstrip('/')}/api/lights", timeout=timeout))
        for light in lights:
            if not isinstance(light, dict) or light.get("state") != "on" or light.get("brightness") == brightness:
                continue
            entity_id = light.get("entity_id")
            if not isinstance(entity_id, str) or not entity_id:
                raise DashboardError("cab light has no entity id")
            body = parse.urlencode({"entity": entity_id, "brightness": brightness}).encode("ascii")
            dashboard_json(f"{base_url.rstrip('/')}/api/lights/brightness", data=body, timeout=timeout)
            changed += 1
            logger(f"{entity_id}: brightness set to {brightness}%")
        detail = f"changed {changed} light(s) to {brightness}%"
        logger(f"cab dimming complete: {detail}")
        return DimResult(DimOutcome.DIMMED if changed else DimOutcome.NOTHING_TO_DO, changed, detail)
    except Exception as error:
        detail = " ".join(f"{type(error).__name__}: {error}".split())
        logger(f"cab dimming skipped: {detail}")
        return DimResult(DimOutcome.FAILED, changed, detail)
