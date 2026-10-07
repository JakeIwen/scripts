import { lastSuccessLabel } from './presentation';
import { iCloudPhaseLabel, iCloudProgress } from './icloud';
import type { ICloudStatus } from './icloud';
import { formatDuration } from '../../utils/format';

export function iCloudTileTone(
  status: ICloudStatus | null | undefined,
): 'paused' | 'stalled' | null {
  if (!status?.available) return status ? 'stalled' : null;
  if (status.stalled || ['error', 'authentication_required', 'interrupted'].includes(status.phase))
    return 'stalled';
  if (status.progressStale || ['paused', 'deferred'].includes(status.phase)) return 'paused';
  return null;
}

export function iCloudUploadTimeLeft(status: ICloudStatus, now = Date.now() / 1000): string | null {
  if (
    !status.available ||
    !status.running ||
    status.phase !== 'uploading' ||
    status.progressStale ||
    status.stalled
  )
    return null;
  if (status.updatedAt === null || now - status.updatedAt > 60 || status.updatedAt - now > 60)
    return null;
  const {
    uploadEstimatedBytes: done,
    uploadTotalBytes: total,
    uploadBytesPerSecond: speed,
  } = status.progress;
  if (done === null || total === null || speed == null || speed <= 0 || done >= total) return null;
  const seconds = (total - done) / speed;
  return Number.isFinite(seconds) ? formatDuration(Math.ceil(seconds / 60) * 60) : null;
}

export function iCloudTileLabel(status: ICloudStatus | null): string {
  if (!status?.available) return 'Status unavailable';
  if (status.phase === 'not due' || status.phase === 'complete') {
    return status.lastSuccessAt === null
      ? 'Not yet verified'
      : `Verified ${lastSuccessLabel(status.lastSuccessAt)}`;
  }
  const percent = iCloudProgress(status).percent;
  const phase = status.stalled
    ? 'Stalled'
    : status.running && status.progressStale
      ? 'Progress stale'
      : iCloudPhaseLabel(status.phase);
  return `${phase}${percent === null ? '' : ` · ${percent.toFixed(1)}%`}`;
}
