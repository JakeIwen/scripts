import { useState } from 'react';
import { formatRelativeTime } from '../../utils/format';
import { Source, WantAction, type WantedShow, type WantRequest } from './schema';

export function WantedShows({
  shows,
  disabled,
  change,
}: {
  shows: WantedShow[];
  disabled: boolean;
  change: (payload: WantRequest) => Promise<boolean>;
}) {
  return (
    <section className="ipt-watch-section">
      <h3>Wanted shows</h3>
      {shows.length === 0 && <p>No wanted shows yet.</p>}
      {shows.map((show) => (
        <article className="ipt-watch-row" key={show.name}>
          <div className="ipt-watch-details">
            <strong>{show.name}</strong> <span className="ipt-watch-badge">{show.source}</span>
            {show.paused && <span className="ipt-watch-badge">Paused</span>}
            <p>
              {haveText(show)} · Last watched {formatRelativeTime(show.last_watched)}
            </p>
          </div>
          <div className="ipt-watch-actions">
            <button
              type="button"
              disabled={disabled}
              aria-label={`${show.paused ? 'Resume' : 'Pause'} ${show.name}`}
              onClick={() =>
                void change({
                  action: show.paused ? WantAction.Resume : WantAction.Pause,
                  name: show.name,
                })
              }
            >
              {show.paused ? 'Resume' : 'Pause'}
            </button>
            {show.source === Source.Manual && (
              <button
                type="button"
                disabled={disabled}
                aria-label={`Remove ${show.name}`}
                onClick={() => void change({ action: WantAction.Remove, name: show.name })}
              >
                Remove
              </button>
            )}
          </div>
        </article>
      ))}
      <AddShow disabled={disabled} add={change} />
    </section>
  );
}
function haveText(show: WantedShow) {
  if (show.have_season === null) return 'On disk unknown';
  return `have S${String(show.have_season).padStart(2, '0')}${show.have_episode === null ? '' : `E${String(show.have_episode).padStart(2, '0')}`}`;
}
function AddShow({
  disabled,
  add,
}: {
  disabled: boolean;
  add: (payload: WantRequest) => Promise<boolean>;
}) {
  const [name, setName] = useState('');
  const [season, setSeason] = useState('');
  const [episode, setEpisode] = useState('');
  return (
    <form
      className="ipt-watch-add"
      onSubmit={async (event) => {
        event.preventDefault();
        if (!name.trim()) return;
        const saved = await add({
          action: WantAction.Add,
          name: name.trim(),
          ...(season !== '' ? { season: Number(season) } : {}),
          ...(episode !== '' ? { episode: Number(episode) } : {}),
        });
        if (saved) {
          setName('');
          setSeason('');
          setEpisode('');
        }
      }}
    >
      <label>
        Show name
        <input
          required
          value={name}
          maxLength={250}
          disabled={disabled}
          onChange={(event) => setName(event.target.value)}
        />
      </label>
      <label>
        Season (optional)
        <input
          type="number"
          min="0"
          step="1"
          value={season}
          disabled={disabled}
          onChange={(event) => setSeason(event.target.value)}
        />
      </label>
      <label>
        Episode (optional)
        <input
          type="number"
          min="0"
          step="1"
          value={episode}
          disabled={disabled}
          onChange={(event) => setEpisode(event.target.value)}
        />
      </label>
      <button type="submit" disabled={disabled || !name.trim()}>
        Add show
      </button>
    </form>
  );
}
