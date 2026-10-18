import { safeExternalHref } from './externalLinks';

export interface ExternalDealLinkProps {
  url: string;
  children: React.ReactNode;
  className?: string;
}

export function ExternalDealLink({ url, children, className }: ExternalDealLinkProps) {
  const href = safeExternalHref(url);
  if (href === null) return <span className={className}>{children}</span>;
  return (
    <a className={className} href={href} target="_blank" rel="noopener noreferrer">
      {children}
    </a>
  );
}
