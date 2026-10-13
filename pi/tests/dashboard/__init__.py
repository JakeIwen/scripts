"""Van dashboard tests."""

from pi.tests.dashboard import _van_compute_checkout

# Runs before any dashboard module imports the app, which needs van_compute.
_van_compute_checkout.install()
