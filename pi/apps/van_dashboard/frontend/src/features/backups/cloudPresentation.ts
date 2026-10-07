import { lastSuccessLabel } from './presentation';
import { iCloudPhaseLabel, iCloudProgress } from './icloud';
import type { ICloudStatus } from './icloud';

export function iCloudTileLabel(status: ICloudStatus | null): string {
  if (!status?.available) return 'Status unavailable';
  if (status.phase === 'not due' || status.phase === 'complete') {
    return status.lastSuccessAt === null
      ? 'Not yet verified'
      : `Verified ${lastSuccessLabel(status.lastSuccessAt)}`;
  }
  const percent = iCloudProgress(status).percent;
  return `${iCloudPhaseLabel(status.phase)}${percent === null ? '' : ` · ${percent.toFixed(1)}%`}`;
}
