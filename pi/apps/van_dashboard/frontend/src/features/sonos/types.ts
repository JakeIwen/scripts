export type SonosTransportState =
  'PLAYING' | 'PAUSED_PLAYBACK' | 'STOPPED' | 'TRANSITIONING' | 'NO_MEDIA_PRESENT' | 'UNKNOWN';

export interface SonosGroup {
  volume: number | null;
  muted: boolean | null;
}

export interface SonosNowPlaying {
  title: string;
  artist: string;
  album: string;
  positionSeconds: number | null;
  durationSeconds: number | null;
  transportState: SonosTransportState;
  albumArt: string | null;
}

export interface SonosSpeaker {
  name: string;
  volume: number | null;
  muted: boolean | null;
  grouped: boolean;
  coordinator: boolean;
  groupCoordinator: string;
}

export interface SonosStatus {
  coordinator: string;
  group: SonosGroup;
  nowPlaying: SonosNowPlaying;
  speakers: SonosSpeaker[];
}

export interface SonosTrackProgress {
  positionSeconds: number;
  durationSeconds: number;
  percent: number;
  label: string;
}

export type SonosTransportAction = 'play_pause' | 'previous' | 'next';

export interface SonosMutationResult {
  message: string;
}

export interface SonosSelectionResult extends SonosMutationResult {
  device: string;
}

export interface SonosVolumeResult extends SonosMutationResult {
  volume: number;
}

export interface SonosMuteResult extends SonosMutationResult {
  muted: boolean;
}
