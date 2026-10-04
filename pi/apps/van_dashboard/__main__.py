from pi.package_runtime import record_running_release

from .van_dashboard import main
from .van_dashboard_common import RUNTIME_DIR


def record_package_release():
    """Publish the path already pinned by pi, never a fresh lookup of current."""
    record_running_release(RUNTIME_DIR, warning_prefix="dashboard")


record_package_release()
main()
