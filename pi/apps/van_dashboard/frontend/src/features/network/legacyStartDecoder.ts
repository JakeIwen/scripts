import { objectValue, stringValue } from '../../api/validation';
import { decodeSpeedtestResponse } from './decoders';
import type { SpeedtestStartResult } from './api';

/** Exact payload-only extraction of the legacy startSpeedtest decoder. */
export function decodeLegacyStartSpeedtestResponse(value: unknown): SpeedtestStartResult {
  const response = objectValue(value, 'speed test start response');
  return {
    message: stringValue(response.message, 'speed test start response.message'),
    status: decodeSpeedtestResponse(value),
  };
}
