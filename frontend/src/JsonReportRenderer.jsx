/**
 * JsonReportRenderer – reusable template for converting JSON objects into
 * readable, well-structured report snippets.
 *
 * Usage:
 *   import { JsonReportView } from './JsonReportRenderer';
 *   <JsonReportView data={someJsonObject} />
 *
 * The renderer auto-detects value types and picks the best visual treatment:
 *   • strings → paragraph or callout block
 *   • numbers → inline metric chip
 *   • booleans → pass/fail badge
 *   • arrays → numbered list or nested cards
 *   • nested objects → recursively rendered subsection
 *   • null/undefined → muted "N/A"
 */
import { memo, useState } from 'react';

/* ── Helpers ──────────────────────────────────────────── */

/** Pretty-print a snake_case / camelCase key into Title Case */
const humanizeKey = (key) =>
  key
    .replace(/([a-z])([A-Z])/g, '$1 $2')   // camelCase
    .replace(/[_-]+/g, ' ')                 // snake_case / kebab
    .replace(/\b\w/g, (c) => c.toUpperCase());

/** Decide if a string value is "long" (multi-line or > threshold chars) */
const isLongText = (v) => typeof v === 'string' && (v.length > 120 || v.includes('\n'));

/** Format a numeric value with locale grouping */
const fmtNumber = (n) => {
  if (Number.isFinite(n)) {
    // Percentages (0..1 range named with "probability", "pct", etc.) kept raw
    return n.toLocaleString(undefined, {
      maximumFractionDigits: 4,
    });
  }
  return String(n);
};

/* ── Leaf renderers ───────────────────────────────────── */

const BoolBadge = ({ value }) => (
  <span className={`jrr-bool ${value ? 'jrr-bool--yes' : 'jrr-bool--no'}`}>
    {value ? '✓ Yes' : '✗ No'}
  </span>
);

const NullBadge = () => (
  <span className="jrr-null">N/A</span>
);

const NumberChip = ({ value }) => (
  <span className="jrr-number">{fmtNumber(value)}</span>
);

const ShortText = ({ value }) => (
  <span className="jrr-short-text">{value}</span>
);

const LongText = ({ value }) => {
  const [expanded, setExpanded] = useState(false);
  const preview = value.slice(0, 200);
  const needsTrunc = value.length > 220;

  return (
    <div className="jrr-long-text">
      <p className="jrr-long-text__body">
        {expanded || !needsTrunc ? value : `${preview}…`}
      </p>
      {needsTrunc && (
        <button
          className="jrr-long-text__toggle"
          onClick={() => setExpanded((e) => !e)}
        >
          {expanded ? '▲ Collapse' : '▼ Read more'}
        </button>
      )}
    </div>
  );
};

/* ── Array renderer ───────────────────────────────────── */

const ArrayView = ({ items, depth }) => {
  if (!items.length) return <span className="jrr-null">Empty list</span>;

  // If all items are primitives, render as a compact list
  const allPrimitive = items.every(
    (item) => typeof item !== 'object' || item === null,
  );

  if (allPrimitive) {
    return (
      <ul className="jrr-list">
        {items.map((item, i) => (
          <li key={i} className="jrr-list__item">
            <ValueRenderer value={item} depth={depth + 1} />
          </li>
        ))}
      </ul>
    );
  }

  // Complex items → render each as a nested card
  return (
    <div className="jrr-nested-array">
      {items.map((item, i) => (
        <div key={i} className="jrr-nested-array__card">
          <span className="jrr-nested-array__index">#{i + 1}</span>
          <ValueRenderer value={item} depth={depth + 1} />
        </div>
      ))}
    </div>
  );
};

/* ── Single-value dispatcher ──────────────────────────── */

const ValueRenderer = memo(({ value, depth = 0 }) => {
  if (value === null || value === undefined) return <NullBadge />;
  if (typeof value === 'boolean') return <BoolBadge value={value} />;
  if (typeof value === 'number') return <NumberChip value={value} />;
  if (typeof value === 'string') {
    return isLongText(value)
      ? <LongText value={value} />
      : <ShortText value={value} />;
  }
  if (Array.isArray(value)) {
    return <ArrayView items={value} depth={depth} />;
  }
  if (typeof value === 'object') {
    return <ObjectSection data={value} depth={depth + 1} />;
  }
  return <ShortText value={String(value)} />;
});

/* ── Object section (recursive) ───────────────────────── */

const ObjectSection = memo(({ data, depth = 0 }) => {
  if (!data || typeof data !== 'object' || Array.isArray(data)) {
    return <ValueRenderer value={data} depth={depth} />;
  }

  const entries = Object.entries(data);
  if (entries.length === 0) return <NullBadge />;

  // Separate entries: "simple" key-value pairs vs nested complex values
  const simpleEntries = [];
  const complexEntries = [];

  entries.forEach(([k, v]) => {
    if (
      v !== null &&
      typeof v === 'object' &&
      !Array.isArray(v) &&
      Object.keys(v).length > 0
    ) {
      complexEntries.push([k, v]);
    } else if (Array.isArray(v) && v.length > 0 && typeof v[0] === 'object') {
      complexEntries.push([k, v]);
    } else {
      simpleEntries.push([k, v]);
    }
  });

  return (
    <div className={`jrr-obj ${depth > 0 ? 'jrr-obj--nested' : ''}`}>
      {/* Simple KV pairs → compact table layout */}
      {simpleEntries.length > 0 && (
        <div className="jrr-kv-grid">
          {simpleEntries.map(([k, v]) => (
            <div key={k} className="jrr-kv-row">
              <span className="jrr-kv-key">{humanizeKey(k)}</span>
              <span className="jrr-kv-val">
                <ValueRenderer value={v} depth={depth} />
              </span>
            </div>
          ))}
        </div>
      )}

      {/* Complex nested values → collapsible subsections */}
      {complexEntries.map(([k, v]) => (
        <NestedCollapsible key={k} label={humanizeKey(k)} depth={depth}>
          <ValueRenderer value={v} depth={depth} />
        </NestedCollapsible>
      ))}
    </div>
  );
});

/* ── Nested collapsible ───────────────────────────────── */

const NestedCollapsible = ({ label, depth, children }) => {
  const [open, setOpen] = useState(depth < 1);

  return (
    <div className={`jrr-collapse ${open ? 'jrr-collapse--open' : ''}`}>
      <button className="jrr-collapse__header" onClick={() => setOpen((o) => !o)}>
        <span className="jrr-collapse__icon">{open ? '▾' : '▸'}</span>
        <span className="jrr-collapse__label">{label}</span>
        {!open && <span className="jrr-collapse__hint">click to expand</span>}
      </button>
      {open && <div className="jrr-collapse__body">{children}</div>}
    </div>
  );
};

/* ── Top-level export ─────────────────────────────────── */

/**
 * Render any JSON-like data as a human-readable report.
 *
 * @param {{ data: any, title?: string }} props
 */
const JsonReportView = memo(({ data, title }) => {
  if (data === null || data === undefined) {
    return <span className="jrr-null">No data</span>;
  }

  return (
    <div className="jrr-root">
      {title && <h4 className="jrr-root__title">{title}</h4>}
      <ValueRenderer value={data} depth={0} />
    </div>
  );
});

export { JsonReportView, humanizeKey, ValueRenderer, ObjectSection };
export default JsonReportView;
