interface KeyValueItem {
  label: React.ReactNode;
  value: React.ReactNode;
}

interface KeyValueListProps {
  items: readonly KeyValueItem[];
  className?: string;
}

export function KeyValueList({ items, className = '' }: KeyValueListProps) {
  return (
    <dl className={`key-value-list ${className}`.trim()}>
      {items.map((item, index) => (
        <div className="key-value-list__row" key={index}>
          <dt>{item.label}</dt>
          <dd>{item.value}</dd>
        </div>
      ))}
    </dl>
  );
}
