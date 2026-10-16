import type { AlarmFilters } from './alarmFilters';
import { ALARM_DAYS } from './alarms';

export function AlarmFilterControls({
  filters,
  onChange,
}: {
  filters: AlarmFilters;
  onChange: (filters: AlarmFilters) => void;
}) {
  return (
    <div className="sonos-alarm-filters">
      <label>
        Includes day of week
        <select
          value={filters.day ?? 'all'}
          onChange={(event) =>
            onChange({
              ...filters,
              day: event.target.value === 'all' ? null : Number(event.target.value),
            })
          }
        >
          <option value="all">Any day</option>
          {ALARM_DAYS.map((day, index) => (
            <option key={day} value={index}>
              {day}
            </option>
          ))}
        </select>
      </label>
      <label>
        Enabled
        <select
          value={filters.enabled === null ? 'all' : String(filters.enabled)}
          onChange={(event) =>
            onChange({
              ...filters,
              enabled: event.target.value === 'all' ? null : event.target.value === 'true',
            })
          }
        >
          <option value="all">All</option>
          <option value="true">Enabled</option>
          <option value="false">Disabled</option>
        </select>
      </label>
    </div>
  );
}
