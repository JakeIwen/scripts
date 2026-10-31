"""Process-wide controller instances for the van dashboard."""

from van_compute.metrics import ComputeMetricsReader
from pi.apps.metered.controller import Controller as MeteredController

from .van_dashboard_media_organizer import MediaOrganizerController
from .van_dashboard_ipt_watch import IptWatchController
from .van_dashboard_backups import BackupManager
from .van_dashboard_cloud_controls import CloudBackupControl
from .van_dashboard_backup_priority import BackupPriorityControl
from .backup_settings_control import BackupSettingsControl
from .van_dashboard_common import COMPUTE_ROOT, StateStore
from .van_dashboard_cop import CopAlertManager, CopCanWakeStatusReader
from .van_dashboard_disks import DiskManager
from .van_dashboard_disk_stats import DiskStatsReader
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
from .sonos_alarms import SonosAlarmController
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
sonos_alarms = SonosAlarmController(sonos)
connectivity = ConnectivityMonitor()
metered = MeteredController()
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
backup_settings_control = BackupSettingsControl()
backups = BackupManager(settings_read=backup_settings_control.local_configuration)
time_machine_cloud_control = CloudBackupControl('time-machine')
pi_cloud_control = CloudBackupControl('pi')
backup_priority_control = BackupPriorityControl()
ignition_monitor_control = IgnitionMonitorController()
disk_manager = DiskManager()
disk_stats = DiskStatsReader(system_monitor)
system_power = SystemPowerController()
dashboard_restart = DashboardRestartController()
telemetry_summary = TelemetrySummaryReader()
voltage_check = VoltageCheckManager()
vonstar = VonstarClient()

media_organizer = MediaOrganizerController()

ipt_watch = IptWatchController()
