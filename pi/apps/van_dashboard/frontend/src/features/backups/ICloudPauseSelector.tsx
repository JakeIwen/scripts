interface ICloudPauseSelectorProps {
  value: string;
  onChange: (value: string) => void;
  disabled: boolean;
}

export function ICloudPauseSelector({ value, onChange, disabled }: ICloudPauseSelectorProps) {
  return (
    <label>
      Pause for
      <select value={value} onChange={(event) => onChange(event.target.value)} disabled={disabled}>
        <option value="15">15 minutes</option>
        <option value="30">30 minutes</option>
        <option value="60">1 hour</option>
        <option value="240">4 hours</option>
        <option value="720">12 hours</option>
        <option value="1440">24 hours</option>
        <option value="10080">7 days</option>
      </select>
    </label>
  );
}
