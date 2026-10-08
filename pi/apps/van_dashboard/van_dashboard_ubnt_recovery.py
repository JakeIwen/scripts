"""Resolve uncertain Starlink antenna changes from later radio observations."""


class UbntConfirmationPending(RuntimeError):
    """The CLI could not confirm completion; it did not prove a failed change."""


def reconcile_starlink_confirmation(operation, wifi):
    """Clear only an uncertain outcome whose requested radio state is now proven."""
    if (operation.get("status") != "error"
            or not operation.get("confirmation_pending")
            or operation.get("kind") != "starlink"
            or operation.get("completed_at") is None
            or (wifi.get("checked_at") or 0) <= operation["completed_at"]
            or wifi.get("reachable") is not True):
        return operation
    state = wifi.get("state", {})
    if state.get("selector_running") is not False:
        return operation
    configured = state.get("configured_ssid")
    associated = state.get("associated_ssid")
    linked = bool(associated) and (state.get("ccq_percent") or 0) > 0
    if operation.get("power") == "on":
        recovered = linked and configured == associated == "denlink"
    elif operation.get("power") == "off":
        # Leaving denlink is also complete with no alternative in range or an
        # explicit maintenance pause. Positive link evidence is required to
        # describe another network as connected.
        recovered = bool(configured) and configured != "denlink" and associated != "denlink"
    else:
        recovered = False
    if not recovered:
        return operation
    message = (f"Antenna connected to {associated}" if linked else
               "Antenna has left the Starlink Wi-Fi network")
    return {**operation, "status": "complete", "error": None,
            "confirmation_pending": False, "message": message}
