import { useState } from 'react';
import { formatBytes, formatPercent, formatRelativeTime, formatSpeed } from '../../utils/format';
import { currentIo, smartVerdict } from './diskStats';
import { DiskStatsSheet } from './DiskStatsSheet';
import type { DiskStats, ManagedDisk } from './types';
import './diskStats.css';

/** The disk card's live I/O, temperature, space and SMART line, with its history sheet. */
export function DiskStatsStrip({ disk, stats }: { disk: ManagedDisk; stats: DiskStats | null }) {
  const [historyOpen, setHistoryOpen] = useState(false);
  const entry = stats?.disks[disk.label];
  const io = stats ? currentIo(entry, stats.checkedAt, stats.ioSampleSeconds) : null;
  const ioNote = io ? 'last minute' : disk.attached ? 'no recent sample' : 'not attached';
  const smart = entry?.smart ?? null;
  const verdict = smart ? smartVerdict(smart) : null;
  const space = disk.space;
  const cells: [label: string, value: string, note: string][] = [
    ['Read', io ? formatSpeed(io.readBytesPerSecond) : '—', ioNote],
    ['Write', io ? formatSpeed(io.writeBytesPerSecond) : '—', ioNote],
    ['Busy', io ? formatPercent(io.busyPercent) : '—', io ? 'time with I/O queued' : ioNote],
    [
      'Temp',
      smart?.temperatureC != null ? `${smart.temperatureC} °C` : '—',
      smart ? formatRelativeTime(smart.at) : 'no SMART reading',
    ],
    [
      'Free',
      space ? formatBytes(space.availableBytes) : '—',
      space ? `of ${formatBytes(space.totalBytes)}` : 'not mounted',
    ],
  ];
  return (
    <div className="disk-stats">
      <dl className="disk-stats__cells">
        {cells.map(([label, value, note]) => (
          <div key={label}>
            <dt>{label}</dt>
            <dd>{value}</dd>
            <dd className="disk-stats__note">{note}</dd>
          </div>
        ))}
      </dl>
      <div className="disk-stats__footer">
        {verdict && smart && (
          <p className={`disk-stats__smart disk-stats__smart--${verdict.tone}`}>
            {verdict.text}
            {smart.powerOnHours !== null &&
              ` · ${smart.powerOnHours.toLocaleString()} h powered on`}
          </p>
        )}
        <button
          type="button"
          className="secondary-button disk-stats__history-button"
          aria-label={`${disk.label} I/O and temperature history`}
          onClick={() => setHistoryOpen(true)}
        >
          <svg viewBox="0 0 20 20" width="16" height="16" aria-hidden="true">
            <path d="M2 17h16" />
            <path d="M3 14l4-5 3 3 4-6 3 3" />
          </svg>
          History
        </button>
      </div>
      {historyOpen && <DiskStatsSheet label={disk.label} onClose={() => setHistoryOpen(false)} />}
    </div>
  );
}
