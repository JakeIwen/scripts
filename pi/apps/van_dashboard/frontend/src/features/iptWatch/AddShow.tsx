import { useState } from 'react';
import { MediaHelp } from '../../components/media/MediaHelp';
import { WantAction, type WantRequest } from './schema';
import { pendingLabels, watchCopy as copy } from './content';

export function AddShow({
  disabled,
  pending,
  add,
}: {
  disabled: boolean;
  pending: boolean;
  add: (payload: WantRequest) => Promise<boolean>;
}) {
  const [name, setName] = useState('');
  const [season, setSeason] = useState('');
  const [episode, setEpisode] = useState('');
  const validNumbers = [season, episode].every(
    (value) => value === '' || (Number.isSafeInteger(Number(value)) && Number(value) >= 0),
  );
  const error = !validNumbers
    ? copy.numberError
    : episode !== '' && season === ''
      ? copy.seasonError
      : null;
  return (
    <form
      className="ipt-watch-add"
      onSubmit={async (event) => {
        event.preventDefault();
        if (disabled || error || !name.trim()) return;
        if (
          await add({
            action: WantAction.Add,
            name: name.trim(),
            ...(season !== '' ? { season: Number(season) } : {}),
            ...(episode !== '' ? { episode: Number(episode) } : {}),
          })
        ) {
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
          maxLength={200}
          disabled={disabled}
          onChange={(event) => setName(event.target.value)}
        />
      </label>
      <div className="ipt-watch-add__help">
        <span>Already on disk (optional)</span>
        <MediaHelp label="Already on disk">{copy.positionHelp}</MediaHelp>
      </div>
      <PositionFields
        season={season}
        episode={episode}
        setSeason={setSeason}
        setEpisode={setEpisode}
        disabled={disabled}
        invalid={!!error}
      />
      {error && (
        <p className="error-message" role="alert">
          {error}
        </p>
      )}
      <button
        type="submit"
        className="secondary-button"
        disabled={disabled || !!error || !name.trim()}
      >
        {pending ? pendingLabels[WantAction.Add] : 'Add show'}
      </button>
    </form>
  );
}

function PositionFields({
  season,
  episode,
  setSeason,
  setEpisode,
  disabled,
  invalid,
}: {
  season: string;
  episode: string;
  setSeason: (value: string) => void;
  setEpisode: (value: string) => void;
  disabled: boolean;
  invalid: boolean;
}) {
  return (
    <>
      <label>
        Season (optional)
        <input
          type="number"
          min="0"
          step="1"
          value={season}
          disabled={disabled}
          aria-invalid={invalid}
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
          aria-invalid={invalid}
          onChange={(event) => setEpisode(event.target.value)}
        />
      </label>
    </>
  );
}
