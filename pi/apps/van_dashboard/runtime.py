"""Process-wide controller instances for the van dashboard."""

from van_compute.metrics import ComputeMetricsReader

from .van_dashboard_backups import BackupManager
from .van_dashboard_common import COMPUTE_ROOT, StateStore
from .van_dashboard_cop import CopAlertManager, CopCanWakeStatusReader
from .van_dashboard_disks import DiskManager
from .van_dashboard_history import NetworkHistoryClient
from .van_dashboard_home import LightingController, TuyaSwitchManager
from .van_dashboard_integrations import PriceCheckController, SystemMonitorClient
from .van_dashboard_network import (
    ConnectivityMonitor,
    OpenWrtClientsController,
    SpeedTestManager,
    UbntWifiController,
)
from .van_dashboard_projects import HostedProjectStore
from .van_dashboard_sonos import SonosController
from .van_dashboard_storage import StoragePolicyManager
from .van_dashboard_system import (
    DashboardRestartController,
    IgnitionMonitorController,
    SystemPowerController,
)
from .van_dashboard_telemetry import TelemetrySummaryReader, VoltageCheckManager
from .van_dashboard_usb import UsbDeviceMonitor, UsbPortController
from .van_dashboard_vonstar import VonstarClient


state_store = StateStore()
hosted_projects = HostedProjectStore(state_store)
cop_alert = CopAlertManager(state_store)
cop_can_wake = CopCanWakeStatusReader()
# Retain the API field used by the React tile; there is only one relay owner.
cop_led = cop_alert.light
sonos = SonosController(state_store)
connectivity = ConnectivityMonitor()
openwrt_clients = OpenWrtClientsController()
ubnt_wifi = UbntWifiController(on_change=connectivity.request_refresh)
speedtest = SpeedTestManager()
starlink = TuyaSwitchManager("starlink")
storage_policy = StoragePolicyManager()
lighting = LightingController()
price_checks = PriceCheckController()
system_monitor = SystemMonitorClient()
network_history = NetworkHistoryClient()
compute_monitor = ComputeMetricsReader(COMPUTE_ROOT)
usb_devices = UsbDeviceMonitor()
usb_ports = UsbPortController(usb_devices)
backups = BackupManager()
ignition_monitor_control = IgnitionMonitorController()
disk_manager = DiskManager()
system_power = SystemPowerController()
dashboard_restart = DashboardRestartController()
telemetry_summary = TelemetrySummaryReader()
voltage_check = VoltageCheckManager()
vonstar = VonstarClient()
