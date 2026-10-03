"""Test-only import aliases for deployed flat-layout dependencies."""

import sys

from pi.van_compute.scripts import van_compute_metrics as _van_compute_metrics


sys.modules["van_compute_metrics"] = _van_compute_metrics
