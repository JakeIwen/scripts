import AppKit
import CoreLocation
import CoreWLAN
import Foundation

// These raw values are the wire contract validated by reader.py, not a metering verdict.
private enum WiFiStatus: String, Codable {
    case ok
    case notAuthorized = "not_authorized"
    case noWiFi = "no_wifi"
    case error
}

private struct WiFiReading: Encodable {
    let interface: String?
    let ssid: String?
    let bssid: String?
    let status: WiFiStatus
    let detail: String

    func encode(to encoder: Encoder) throws {
        var container = encoder.container(keyedBy: CodingKeys.self)
        try container.encode(interface, forKey: .interface)
        try container.encode(ssid, forKey: .ssid)
        try container.encode(bssid, forKey: .bssid)
        try container.encode(status, forKey: .status)
        try container.encode(detail, forKey: .detail)
    }

    private enum CodingKeys: String, CodingKey {
        case interface, ssid, bssid, status, detail
    }
}

@MainActor
// CLLocationManager delivers callbacks on the run loop where it is created: the app's main actor.
final class WiFiProbe: NSObject, NSApplicationDelegate, @MainActor CLLocationManagerDelegate {
    private let manager = CLLocationManager()
    private let outputPath: String?
    private let requestAuthorization: Bool
    private var finished = false

    init(outputPath: String?, requestAuthorization: Bool) {
        self.outputPath = outputPath
        self.requestAuthorization = requestAuthorization
    }

    func applicationDidFinishLaunching(_ notification: Notification) {
        manager.delegate = self
        if requestAuthorization && manager.authorizationStatus == .notDetermined {
            NSApp.activate(ignoringOtherApps: true)
            manager.requestWhenInUseAuthorization()
            // A user can leave the consent dialog unanswered; no worker stays behind forever.
            scheduleFinish(after: 120)
        } else {
            scheduleFinish(after: 0.3)
        }
    }

    func locationManagerDidChangeAuthorization(_ manager: CLLocationManager) {
        if manager.authorizationStatus != .notDetermined {
            scheduleFinish(after: 0.3)
        }
    }

    private func scheduleFinish(after delay: TimeInterval) {
        Task { @MainActor in
            try? await Task.sleep(for: .seconds(delay))
            finish()
        }
    }

    private func reading() -> WiFiReading {
        guard let interfaces = CWWiFiClient.shared().interfaces() else {
            return result(.error, nil, "corewlan_interfaces_unavailable")
        }
        let powered = interfaces.filter { $0.powerOn() }
        let associated = powered.filter { $0.wlanChannel() != nil }
        guard associated.count <= 1 else {
            return result(.error, nil, "multiple_associated_wifi_interfaces")
        }
        let interface = associated.first ?? powered.first ?? interfaces.first
        guard let interface else {
            return result(.noWiFi, nil, "no_wifi_interface")
        }
        guard !powered.isEmpty else {
            return result(.noWiFi, interface, "wifi_power_off_or_unavailable")
        }
        guard CLLocationManager.locationServicesEnabled() else {
            return result(.notAuthorized, interface, "location_services_disabled")
        }
        switch manager.authorizationStatus {
        case .notDetermined:
            return result(.notAuthorized, interface, "location_not_determined")
        case .denied:
            return result(.notAuthorized, interface, "location_denied")
        case .restricted:
            return result(.notAuthorized, interface, "location_restricted")
        case .authorizedAlways, .authorizedWhenInUse:
            return association(interface)
        @unknown default:
            return result(.error, interface, "unknown_location_authorization")
        }
    }

    private func association(_ interface: CWInterface) -> WiFiReading {
        let ssid = interface.ssid()
        let bssid = interface.bssid()
        if ssid?.lowercased() == "<redacted>" || bssid?.lowercased() == "<redacted>" {
            return result(.notAuthorized, interface, "corewlan_redacted_despite_authorization")
        }
        guard let ssid, !ssid.isEmpty, let bssid, !bssid.isEmpty else {
            // CoreWLAN returns nil for both disassociation and API errors; neither is unmetered.
            return result(.error, interface, "wifi_unassociated_or_identity_unavailable")
        }
        return WiFiReading(interface: interface.interfaceName, ssid: ssid, bssid: bssid,
                           status: .ok, detail: "corewlan_location_authorized")
    }

    private func result(_ status: WiFiStatus, _ interface: CWInterface?, _ detail: String) -> WiFiReading {
        WiFiReading(interface: interface?.interfaceName, ssid: nil, bssid: nil,
                    status: status, detail: detail)
    }

    private func finish() {
        guard !finished else { return }
        finished = true
        do {
            let encoder = JSONEncoder()
            encoder.outputFormatting = [.sortedKeys]
            var data = try encoder.encode(reading())
            data.append(0x0A)
            if let outputPath {
                let destination = URL(fileURLWithPath: outputPath)
                try data.write(to: destination, options: .atomic)
                try FileManager.default.setAttributes([.posixPermissions: 0o600],
                                                      ofItemAtPath: outputPath)
            } else {
                FileHandle.standardOutput.write(data)
            }
            NSApp.terminate(nil)
        } catch {
            FileHandle.standardError.write(Data("MeteredWiFi output failed: \(error)\n".utf8))
            exit(74)
        }
    }
}
