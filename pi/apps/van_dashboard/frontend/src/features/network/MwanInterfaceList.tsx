import type { MwanInterface } from './types';

interface MwanInterfaceListProps {
  interfaces: readonly MwanInterface[];
}

export function MwanInterfaceList({ interfaces }: MwanInterfaceListProps) {
  if (interfaces.length === 0) {
    return <span className="mwan-interface-list__empty">No interface state</span>;
  }

  return (
    <div className="mwan-interface-list" aria-label="MWAN3 interfaces">
      {interfaces.map((item) => (
        <span
          className={`mwan-interface mwan-interface--${item.state}`}
          key={item.name}
          title={item.detail ?? undefined}
        >
          {item.name} · {item.state}
        </span>
      ))}
    </div>
  );
}
