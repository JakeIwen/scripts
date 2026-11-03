import { useOffsiteStatus } from './hooks';
import { OffsiteSourceCard } from './OffsiteSourceCard';
import { OffsiteStatusCard } from './OffsiteStatusCard';
import { offsiteChip, offsiteSourceKeys, offsiteSourceView } from './offsitePresentation';

/** Encrypted restic backups to the offsite drive or NAS; collapsed so the sheet stays short. */
export function OffsiteSection({ open }: { open: boolean }) {
  const resource = useOffsiteStatus(open);
  const status = resource.data;
  const chip = offsiteChip(status, resource.error !== null);
  return (
    <section className="backups-sheet__section" aria-labelledby="backup-offsite-title">
      <details className="backups-sheet__disclosure">
        <summary className="backups-sheet__summary">
          <span className="backups-sheet__chevron" aria-hidden="true">
            ▸
          </span>
          <h3 id="backup-offsite-title">Offsite NAS</h3>
          <span className={`backup-offsite__chip backup-offsite__chip--${chip.tone}`}>
            {chip.text}
          </span>
        </summary>
        <div className="backups-sheet__body">
          <div className="backups-sheet__heading">
            <p>Encrypted restic copies of the van’s data on the offsite drive.</p>
          </div>
          {resource.error && !status && <p className="error-message">{resource.error.message}</p>}
          {status ? (
            <>
              <OffsiteStatusCard status={status} />
              <div className="backup-evidence-grid">
                {offsiteSourceKeys(status).map((source) => (
                  <OffsiteSourceCard
                    key={source}
                    view={offsiteSourceView(status, source)}
                    run={status.run}
                    remote={status.run.targetKind === 'remote'}
                  />
                ))}
              </div>
            </>
          ) : (
            !resource.error && <p className="backups-empty">Loading offsite status…</p>
          )}
        </div>
      </details>
    </section>
  );
}
