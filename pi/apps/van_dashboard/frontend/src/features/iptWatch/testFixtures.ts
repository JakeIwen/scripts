import { CandidateState, Login, Source, type Candidate, type WatchStatus } from './schema';
export function sampleCandidate(changes: Partial<Candidate> = {}): Candidate {
  return {
    torrent_id: '123',
    name: 'Silo.S03E08.1080p.x264',
    show: 'Silo',
    season: 3,
    episode: 8,
    size_bytes: 1024 ** 3,
    seeders: 21,
    leechers: 2,
    snatches: 40,
    freeleech: true,
    age_text: '3.1 minutes ago',
    category: 'TV/x264',
    download_path: '/download.php/123/Silo.torrent',
    first_seen: '2026-10-29T06:00:00Z',
    status: CandidateState.New,
    downloaded_at: null,
    file_path: null,
    error: null,
    ...changes,
  };
}
export function sampleStatus(changes: Partial<WatchStatus> = {}): WatchStatus {
  return {
    login: Login.Ok,
    last_check: '2026-10-29T06:00:00Z',
    last_error: null,
    checking: false,
    wanted_count: 2,
    new_candidate_count: 3,
    tfiles_writable: true,
    library_available: true,
    wanted: [
      {
        name: 'Silo',
        source: Source.Library,
        library_show_id: 'silo',
        last_watched: 1793253600,
        have_season: 3,
        have_episode: 7,
        added_at: '2026-10-28T06:00:00Z',
        paused: false,
      },
      {
        name: 'Severance',
        source: Source.Manual,
        library_show_id: null,
        last_watched: null,
        have_season: null,
        have_episode: null,
        added_at: '2026-10-28T06:00:00Z',
        paused: true,
      },
    ],
    candidates: [
      sampleCandidate(),
      sampleCandidate({ torrent_id: '124', name: 'Silo.S03.1080p.x264', episode: null }),
      sampleCandidate({ torrent_id: '125', name: 'Severance.S02E01', show: 'Severance' }),
      sampleCandidate({
        torrent_id: '126',
        name: 'Silo.handled',
        status: CandidateState.Dismissed,
      }),
    ],
    job: null,
    ...changes,
  };
}
