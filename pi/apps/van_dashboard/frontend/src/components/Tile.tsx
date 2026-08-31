import type { StatusTone } from './StatusPill';

interface TileProps {
  icon: string;
  title: string;
  summary: React.ReactNode;
  children?: React.ReactNode;
  status?: React.ReactNode;
  tone?: StatusTone;
  className?: string;
  onClick?: () => void;
  ariaLabel?: string;
  style?: React.CSSProperties;
}

export function Tile({
  icon,
  title,
  summary,
  children,
  status,
  tone = 'neutral',
  className = '',
  onClick,
  ariaLabel,
  style,
}: TileProps) {
  const content = (
    <>
      <header className="tile__header">
        <span className="tile__icon" aria-hidden="true">
          {icon}
        </span>
        <h2 className="tile__title">{title}</h2>
        {status && <div className="tile__status">{status}</div>}
      </header>
      <div className="tile__summary">{summary}</div>
      {children && <div className="tile__body">{children}</div>}
    </>
  );

  const classes = `tile tile--${tone} ${className}`.trim();
  if (onClick) {
    return (
      <button
        className={`${classes} tile--button`}
        type="button"
        onClick={onClick}
        aria-label={ariaLabel}
        style={style}
      >
        {content}
      </button>
    );
  }
  return (
    <article className={classes} style={style}>
      {content}
    </article>
  );
}

interface ServiceLinkTileProps {
  icon: string;
  title: string;
  detail: string;
  port: number;
}

export function ServiceLinkTile({ icon, title, detail, port }: ServiceLinkTileProps) {
  const url = new URL(window.location.href);
  url.port = String(port);
  url.pathname = '/';
  url.search = '';
  url.hash = '';

  return (
    <a className="tile tile--button service-link-tile" href={url.toString()}>
      <header className="tile__header">
        <span className="tile__icon" aria-hidden="true">
          {icon}
        </span>
        <h2 className="tile__title">{title}</h2>
      </header>
      <p className="tile__summary">{detail}</p>
    </a>
  );
}
