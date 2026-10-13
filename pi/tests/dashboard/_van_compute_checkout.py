"""Make the sibling van-compute checkout the dashboard tests' metrics provider.

Production imports ``van_compute.metrics`` from the separately installed
/home/pi/van_compute/current release. Locally the standalone checkout supplies
it (default ../van-compute, override VAN_COMPUTE_CHECKOUT). It is a required
prerequisite: a missing checkout, or a different provider winning the import,
fails loudly instead of silently skipping dashboard coverage.
"""

import importlib
import os
from pathlib import Path
import sys


REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
CHECKOUT = Path(
    os.environ.get("VAN_COMPUTE_CHECKOUT")
    or REPOSITORY_ROOT.parent / "van-compute"
).expanduser().resolve()


def install():
    package = CHECKOUT / "van_compute"
    if not (package / "metrics.py").is_file():
        raise ImportError(
            f"dashboard tests require the van-compute checkout at {CHECKOUT}; "
            "clone it there or set VAN_COMPUTE_CHECKOUT"
        )
    # Appended, not prepended: the checkout also contains a `pi` test package
    # that must never shadow this repository's.
    if str(CHECKOUT) not in sys.path:
        sys.path.append(str(CHECKOUT))
    origin = Path(importlib.import_module("van_compute.metrics").__file__).resolve()
    if not origin.is_relative_to(package):
        raise ImportError(
            f"van_compute.metrics was imported from {origin}, not {package}"
        )
