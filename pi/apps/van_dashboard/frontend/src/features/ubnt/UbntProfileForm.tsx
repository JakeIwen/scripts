import { useEffect, useId, useRef, useState } from 'react';

import type { UbntProfile, UbntProfileUpdate, UbntRadioState, UbntRateModule } from './types';

export interface UbntProfileFormProps {
  profile: UbntProfile;
  radio: UbntRadioState;
  disabled: boolean;
  onCancel: () => void;
  onSubmit: (update: UbntProfileUpdate) => Promise<boolean>;
}

function currentRadioReadings(profile: UbntProfile, radio: UbntRadioState): string {
  if (profile.ssid !== radio.associatedSsid) {
    return 'Radio readings are available only for the currently associated network.';
  }
  const readings = [
    radio.signalDbm === null ? null : `Signal ${radio.signalDbm} dBm`,
    radio.noiseDbm === null ? null : `Noise ${radio.noiseDbm} dBm`,
    radio.snrDb === null ? null : `SNR ${radio.snrDb} dB`,
    radio.ccqPercent === null ? null : `CCQ ${radio.ccqPercent}%`,
  ].filter((reading): reading is string => reading !== null);
  return readings.length ? `Current link · ${readings.join(' · ')}` : 'No radio readings';
}

export function UbntProfileForm({
  profile,
  radio,
  disabled,
  onCancel,
  onSubmit,
}: UbntProfileFormProps) {
  const passwordId = useId();
  const bssidId = useId();
  const powerId = useId();
  const moduleId = useId();
  const automaticId = useId();
  const mcsId = useId();
  const formRef = useRef<HTMLFormElement>(null);
  const passwordRef = useRef<HTMLInputElement>(null);
  const [bssid, setBssid] = useState(profile.bssid);
  const [outputPowerDbm, setOutputPowerDbm] = useState(profile.outputPowerDbm ?? 21);
  const [rateModule, setRateModule] = useState<UbntRateModule>(profile.rateModule ?? 'atheros');
  const [rateAuto, setRateAuto] = useState(profile.rateAuto ?? true);
  const [rateMcs, setRateMcs] = useState(profile.rateMcs ?? 0);

  useEffect(() => {
    formRef.current?.scrollIntoView?.({ behavior: 'smooth', block: 'nearest' });
  }, []);

  const submit = async (event: React.FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const submitter = (event.nativeEvent as SubmitEvent).submitter as HTMLButtonElement | null;
    const password = passwordRef.current?.value ?? '';
    if (passwordRef.current) passwordRef.current.value = '';

    const saved = await onSubmit({
      profile: profile.name,
      password,
      bssid: bssid.trim(),
      outputPowerDbm,
      rateModule,
      rateAuto,
      rateMcs,
      applyNow: submitter?.dataset.applyNow === 'true',
    });
    if (saved) onCancel();
  };

  return (
    <form ref={formRef} className="ubnt-profile-form" onSubmit={(event) => void submit(event)}>
      <header>
        <span>
          <strong>Edit {profile.name}</strong>
          <small>
            {profile.ssid} · Priority {profile.priority ?? '—'}
          </small>
        </span>
        <button
          type="button"
          className="icon-button"
          onClick={onCancel}
          aria-label="Close profile editor"
        >
          ×
        </button>
      </header>

      {profile.security === 'wpa' && (
        <label htmlFor={passwordId}>
          <span>New Wi-Fi password</span>
          <input
            id={passwordId}
            ref={passwordRef}
            type="password"
            minLength={8}
            maxLength={63}
            autoComplete="new-password"
            placeholder="Leave blank to keep saved password"
            disabled={disabled}
          />
          <small>The existing password is never requested or displayed.</small>
        </label>
      )}

      <label htmlFor={bssidId}>
        <span>Lock to AP</span>
        <input
          id={bssidId}
          type="text"
          maxLength={17}
          placeholder="Blank selects the strongest matching AP"
          value={bssid}
          disabled={disabled}
          onChange={(event) => setBssid(event.target.value)}
        />
      </label>

      <label htmlFor={powerId}>
        <span>
          Output power <output>{outputPowerDbm} dBm</output>
        </span>
        <input
          id={powerId}
          type="range"
          aria-label="Output power in dBm"
          min={0}
          max={23}
          step={1}
          value={outputPowerDbm}
          disabled={disabled}
          onChange={(event) => setOutputPowerDbm(Number(event.target.value))}
        />
      </label>

      <label htmlFor={moduleId}>
        <span>Data Rate Module</span>
        <select
          id={moduleId}
          value={rateModule}
          disabled={disabled}
          onChange={(event) => setRateModule(event.target.value as UbntRateModule)}
        >
          <option value="atheros">Default (recommended)</option>
          <option value="ewma_ht">Alternative</option>
        </select>
      </label>

      <label className="ubnt-profile-form__checkbox" htmlFor={automaticId}>
        <input
          id={automaticId}
          type="checkbox"
          checked={rateAuto}
          disabled={disabled}
          onChange={(event) => setRateAuto(event.target.checked)}
        />
        <span>Maximum TX rate: Auto</span>
      </label>

      <label htmlFor={mcsId}>
        <span>Manual maximum TX rate</span>
        <select
          id={mcsId}
          value={rateMcs}
          disabled={disabled || rateAuto}
          onChange={(event) => setRateMcs(Number(event.target.value))}
        >
          {Array.from({ length: 16 }, (_, mcs) => (
            <option value={mcs} key={mcs}>
              MCS {mcs}
            </option>
          ))}
        </select>
      </label>

      <p className="ubnt-profile-form__readings">{currentRadioReadings(profile, radio)}</p>
      <div className="ubnt-profile-form__actions">
        <button type="button" className="secondary-button" disabled={disabled} onClick={onCancel}>
          Cancel
        </button>
        <button type="submit" data-apply-now="false" disabled={disabled}>
          Save for next connection
        </button>
        <button type="submit" data-apply-now="true" disabled={disabled}>
          Save &amp; reconnect now
        </button>
      </div>
    </form>
  );
}
