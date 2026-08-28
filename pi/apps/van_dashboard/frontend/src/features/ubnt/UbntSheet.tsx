import { useEffect, useState } from 'react';

import { BottomSheet } from '../../components/BottomSheet';
import type { PollingState } from '../../hooks/usePollingResource';
import { formatRelativeTime } from '../../utils/format';
import type { UbntControls } from './controls';
import { ubntOperationLabel, ubntSecurityLabel } from './presentation';
import type { UbntNetwork, UbntProfile, UbntWifiStatus } from './types';
import { UbntProfileForm } from './UbntProfileForm';
import { UbntProvisionForm } from './UbntProvisionForm';
import { StarlinkControl, type StarlinkStatusResource } from './StarlinkControl';
import './ubnt.css';

export interface UbntSheetProps {
  open: boolean;
  onClose: () => void;
  resource: PollingState<UbntWifiStatus>;
  controls: UbntControls;
  dashboardStatus?: StarlinkStatusResource;
}

function profileDetails(profile: UbntProfile): string[] {
  const passwordDetail =
    profile.security !== 'wpa'
      ? null
      : profile.hasPassword === true
        ? 'Password saved'
        : profile.hasPassword === false
          ? 'Password missing'
          : 'Password status unknown';
  return [
    ubntSecurityLabel(profile.security),
    profile.priority === null ? null : `Priority ${profile.priority}`,
    profile.bssid ? `AP ${profile.bssid}` : 'Any matching AP',
    passwordDetail,
  ].filter((part): part is string => part !== null);
}

function radioProfileDetails(profile: UbntProfile): string | null {
  if (profile.outputPowerDbm === null || profile.rateModule === null) return null;
  const rate = profile.rateAuto ? 'automatic rate' : `maximum MCS ${profile.rateMcs ?? '—'}`;
  return `${profile.outputPowerDbm} dBm · ${profile.rateModule} · ${rate}`;
}

function profileCanBeEdited(profile: UbntProfile): boolean {
  return (
    profile.outputPowerDbm !== null &&
    profile.rateModule !== null &&
    profile.rateAuto !== null &&
    profile.rateMcs !== null
  );
}

function networkDetails(network: UbntNetwork): string[] {
  return [
    network.signalDbm === null ? null : `${network.signalDbm} dBm`,
    `${network.qualityPercent}% quality`,
    ubntSecurityLabel(network.security),
    network.channel === null ? null : `Channel ${network.channel}`,
  ].filter((part): part is string => part !== null);
}

function NetworkState({ network }: { network: UbntNetwork }) {
  const label = network.connected
    ? 'Connected'
    : network.known
      ? 'Saved'
      : network.supported
        ? 'Available'
        : 'Unsupported';
  return <b>{label}</b>;
}

export function UbntSheet({ open, onClose, resource, controls, dashboardStatus }: UbntSheetProps) {
  const status = resource.data;
  const associated = status?.state.associatedSsid || status?.state.configuredSsid;
  const operation = status?.operation;
  const operationRunning = operation?.status === 'running';
  const controlsDisabled = controls.busy || operationRunning;
  const [joiningNetwork, setJoiningNetwork] = useState<UbntNetwork | null>(null);
  const [editingProfileName, setEditingProfileName] = useState<string | null>(null);
  const editingProfile =
    status?.profiles.find((profile) => profile.name === editingProfileName) ?? null;

  useEffect(() => {
    if (editingProfileName && status && editingProfile === null) setEditingProfileName(null);
  }, [editingProfile, editingProfileName, status]);

  const provisionOpenNetwork = (network: UbntNetwork) => {
    void controls.provision({
      ssid: network.ssid,
      security: 'none',
      bssid: network.bssid,
      password: '',
    });
  };

  return (
    <BottomSheet
      open={open}
      title="UBNT Wi-Fi"
      description="Scan, connect, and tune saved antenna profiles through the existing controller."
      onClose={onClose}
    >
      <div className="ubnt-sheet__toolbar">
        <span aria-live="polite">
          {controls.busy
            ? 'Antenna operation in progress…'
            : status?.checkedAt === null || status?.checkedAt === undefined
              ? 'No status timestamp'
              : `Updated ${formatRelativeTime(status.checkedAt)}`}
        </span>
        <div>
          {status?.state.automaticPaused === true && (
            <button
              type="button"
              disabled={controlsDisabled}
              onClick={() => void controls.resumeAutomatic()}
            >
              Resume automatic selection
            </button>
          )}
          <button type="button" disabled={controlsDisabled} onClick={() => void controls.scan()}>
            {operationRunning && operation?.kind === 'scan' ? 'Scanning…' : 'Scan nearby Wi-Fi'}
          </button>
          <button
            className="secondary-button"
            type="button"
            disabled={resource.refreshing || controlsDisabled}
            onClick={() => void resource.refresh()}
          >
            {resource.refreshing ? 'Refreshing…' : 'Refresh status'}
          </button>
        </div>
      </div>

      {resource.error && <p className="error-message">{resource.error.message}</p>}

      <div className="ubnt-sheet__stack" aria-busy={resource.refreshing || controlsDisabled}>
        <section className="ubnt-current panel-card">
          <span
            className={`ubnt-current__dot ubnt-current__dot--${status?.reachable === true && associated ? 'good' : status?.reachable === false ? 'bad' : 'neutral'}`}
            aria-hidden="true"
          />
          <span>
            <strong>{associated || 'Not associated'}</strong>
            <small>
              {status?.state.signalDbm === null || status?.state.signalDbm === undefined
                ? 'Radio measurements unavailable'
                : `${status.state.signalDbm} dBm · ${status.state.snrDb ?? '—'} dB SNR · ${status.state.ccqPercent ?? '—'}% CCQ`}
            </small>
          </span>
        </section>

        {dashboardStatus && <StarlinkControl resource={dashboardStatus} />}

        {operation && (
          <section className={`ubnt-operation ubnt-operation--${operation.status}`}>
            <strong>Latest antenna operation</strong>
            <span>{ubntOperationLabel(operation)}</span>
            {operation.error && <small>{operation.error}</small>}
          </section>
        )}

        {joiningNetwork && (
          <UbntProvisionForm
            network={joiningNetwork}
            disabled={controlsDisabled}
            onCancel={() => setJoiningNetwork(null)}
            onSubmit={async (request) => {
              setJoiningNetwork(null);
              return controls.provision(request);
            }}
          />
        )}

        <section className="panel-card">
          <header className="ubnt-section-heading">
            <div>
              <h3>Saved networks</h3>
              <p>Profiles include only non-secret settings and password availability.</p>
            </div>
            <strong>{status?.profiles.length ?? 0} saved</strong>
          </header>
          <div className="ubnt-profile-list">
            {status?.profiles.length ? (
              status.profiles.map((profile) => {
                const connected = profile.ssid === status.state.associatedSsid;
                const radio = radioProfileDetails(profile);
                return (
                  <article
                    className={connected ? 'ubnt-profile ubnt-profile--connected' : 'ubnt-profile'}
                    key={profile.name}
                  >
                    <span>
                      <strong>{profile.name}</strong>
                      {profile.name !== profile.ssid && <small>SSID {profile.ssid}</small>}
                      <small>{profileDetails(profile).join(' · ')}</small>
                      {radio && <small>{radio}</small>}
                    </span>
                    <div className="ubnt-row-actions">
                      <button
                        type="button"
                        disabled={controlsDisabled}
                        onClick={() => void controls.connect(profile.name)}
                      >
                        {connected ? 'Reconnect' : 'Connect'}
                      </button>
                      <button
                        type="button"
                        className="secondary-button"
                        disabled={controlsDisabled || !profileCanBeEdited(profile)}
                        onClick={() => {
                          setJoiningNetwork(null);
                          setEditingProfileName(profile.name);
                        }}
                      >
                        Edit
                      </button>
                    </div>
                  </article>
                );
              })
            ) : (
              <p className="ubnt-sheet__empty">No saved UBNT profiles reported.</p>
            )}
          </div>
        </section>

        {editingProfile && status && (
          <UbntProfileForm
            key={editingProfile.name}
            profile={editingProfile}
            radio={status.state}
            disabled={controlsDisabled}
            onCancel={() => setEditingProfileName(null)}
            onSubmit={async (update) => {
              setEditingProfileName(null);
              return controls.updateProfile(update);
            }}
          />
        )}

        <section className="panel-card">
          <header className="ubnt-section-heading">
            <div>
              <h3>Nearby networks</h3>
              <p>The latest scan retained by the antenna controller.</p>
            </div>
            <strong>{status?.networks.length ?? 0} visible</strong>
          </header>
          <div className="ubnt-network-list">
            {status?.networks.length ? (
              status.networks.map((network) => {
                const canProvision =
                  network.supported &&
                  !network.known &&
                  (network.security === 'wpa' || network.security === 'none');
                return (
                  <article
                    className={
                      network.connected ? 'ubnt-network ubnt-network--connected' : 'ubnt-network'
                    }
                    key={`${network.ssid}:${network.bssid}`}
                  >
                    <span>
                      <strong>{network.ssid || 'Hidden network'}</strong>
                      <small>{networkDetails(network).join(' · ')}</small>
                    </span>
                    <div className="ubnt-network__control">
                      <NetworkState network={network} />
                      {network.known ? (
                        network.profiles.map((profile) => (
                          <button
                            type="button"
                            disabled={controlsDisabled || network.connected}
                            onClick={() => void controls.connect(profile)}
                            key={profile}
                          >
                            {network.connected
                              ? 'Connected'
                              : network.profiles.length > 1
                                ? profile
                                : 'Connect'}
                          </button>
                        ))
                      ) : canProvision ? (
                        <button
                          type="button"
                          disabled={controlsDisabled}
                          onClick={() => {
                            setEditingProfileName(null);
                            if (network.security === 'none') provisionOpenNetwork(network);
                            else setJoiningNetwork(network);
                          }}
                        >
                          {network.security === 'none' ? 'Add & connect' : 'Add'}
                        </button>
                      ) : null}
                    </div>
                  </article>
                );
              })
            ) : (
              <p className="ubnt-sheet__empty">
                {operationRunning && operation?.kind === 'scan'
                  ? 'Scanning nearby networks…'
                  : 'No scan results yet. Run a nearby Wi-Fi scan.'}
              </p>
            )}
          </div>
        </section>

        <aside className="ubnt-sheet__domain-note">
          Internet-route status is owned by the OpenWrt tile and reconciles on its independent poll.
        </aside>
      </div>
    </BottomSheet>
  );
}
