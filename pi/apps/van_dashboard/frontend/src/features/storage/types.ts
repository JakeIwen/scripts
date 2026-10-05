import type { StatusTone } from '../../components/StatusPill';
import type { UsbPort } from '../usb/types';

export type * from './schema';

// Computed UI presentation, not a decoded wire response; keep its existing contract.
export interface UsbResetAssessment {
  eligible: boolean;
  tone: StatusTone;
  label: string;
  detail: string;
  port: UsbPort | null;
}
