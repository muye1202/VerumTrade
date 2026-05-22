import { useState } from 'react';

// Colour-scales confidence 0–1: green ≥0.85, yellow ≥0.70, red <0.70
function confidenceColor(v) {
  if (v >= 0.85) return '#4ade80';   // green
  if (v >= 0.70) return '#facc15';   // yellow
  return '#f87171';                   // red
}

// Exposure-type badge styles
const EXPOSURE_STYLES = {
  direct:       { background: 'rgba(99,179,237,0.18)', color: '#63b3ed', border: '1px solid rgba(99,179,237,0.35)' },
  indirect:     { background: 'var(--surface-muted)',  color: 'var(--muted)', border: '1px solid var(--border)' },
  second_order: { background: 'transparent',           color: 'var(--muted)', border: '1px solid var(--border)' },
};

function ExposureBadge({ type }) {
  const style = EXPOSURE_STYLES[type] || EXPOSURE_STYLES.indirect;
  return (
    <span style={{
      ...style,
      fontSize: '10px', fontWeight: 600, letterSpacing: '0.04em',
      padding: '2px 7px', borderRadius: '20px', textTransform: 'uppercase',
      whiteSpace: 'nowrap', flexShrink: 0,
    }}>
      {type?.replace('_', ' ') || 'indirect'}
    </span>
  );
}

function MomentumBar({ value, label = 'Momentum' }) {
  const pct = Math.round((value || 0) * 100);
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: '6px', fontSize: '11px', color: 'var(--muted)' }}>
      <span style={{ minWidth: '52px' }}>{label}</span>
      <div style={{ flex: 1, height: '4px', borderRadius: '2px', background: 'var(--border)', maxWidth: '80px' }}>
        <div style={{
          height: '100%', borderRadius: '2px',
          width: `${pct}%`,
          background: pct >= 60 ? '#4ade80' : pct >= 35 ? '#facc15' : 'var(--muted)',
          transition: 'width 0.4s ease',
        }} />
      </div>
      <span>{pct}%</span>
    </div>
  );
}

function ConfidenceBar({ value }) {
  const pct = Math.round((value || 0) * 100);
  const color = confidenceColor(value || 0);
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: '6px', fontSize: '11px' }}>
      <div style={{ width: '52px', height: '4px', borderRadius: '2px', background: 'var(--border)' }}>
        <div style={{
          height: '100%', borderRadius: '2px',
          width: `${pct}%`,
          background: color,
          transition: 'width 0.4s ease',
        }} />
      </div>
      <span style={{ color, fontWeight: 600, minWidth: '28px' }}>{pct}%</span>
    </div>
  );
}

function EvidenceBullets({ items }) {
  const [open, setOpen] = useState(false);
  if (!items || items.length === 0) return null;
  const visible = items.slice(0, 3);
  return (
    <div style={{ marginTop: '6px' }}>
      <button
        onClick={() => setOpen(p => !p)}
        style={{
          background: 'none', border: 'none', cursor: 'pointer',
          color: 'var(--muted)', fontSize: '11px', padding: '0',
          display: 'flex', alignItems: 'center', gap: '4px',
        }}
      >
        <span style={{ fontSize: '9px' }}>{open ? '▼' : '▶'}</span>
        {open ? 'Hide evidence' : `${visible.length} evidence bullet${visible.length > 1 ? 's' : ''}`}
      </button>
      {open && (
        <ul style={{ margin: '4px 0 0 0', padding: '0 0 0 14px', listStyle: 'disc' }}>
          {visible.map((e, i) => (
            <li key={i} style={{ fontSize: '11px', color: 'var(--muted)', lineHeight: 1.5, marginBottom: '2px' }}>
              {e}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

function TickerRow({ c }) {
  return (
    <div style={{
      display: 'flex', flexDirection: 'column', gap: '4px',
      padding: '10px 12px',
      borderRadius: '8px',
      background: 'var(--surface-muted)',
      border: '1px solid var(--border)',
    }}>
      {/* Top row: ticker + bottleneck + exposure badge + confidence */}
      <div style={{ display: 'flex', alignItems: 'center', gap: '8px', flexWrap: 'wrap' }}>
        <span style={{
          fontWeight: 700, fontSize: '13px',
          background: 'color-mix(in srgb, var(--accent) 14%, transparent)',
          color: 'var(--accent)',
          border: '1px solid color-mix(in srgb, var(--accent) 30%, transparent)',
          borderRadius: '5px', padding: '2px 8px',
          letterSpacing: '0.05em',
        }}>
          {c.ticker}
        </span>
        {c.bottleneck && (
          <span style={{ fontSize: '11px', color: 'var(--muted)', flex: 1, minWidth: '80px' }}>
            {c.bottleneck}
          </span>
        )}
        <ExposureBadge type={c.exposure_type} />
        <ConfidenceBar value={c.exposure_confidence} />
      </div>

      {/* Why it matters */}
      {c.why_it_matters && (
        <p style={{ margin: 0, fontSize: '12px', color: 'var(--text)', lineHeight: 1.5 }}>
          {c.why_it_matters}
        </p>
      )}

      <EvidenceBullets items={c.evidence} />
    </div>
  );
}

function ThemeGroup({ theme, candidates }) {
  const [collapsed, setCollapsed] = useState(false);
  // Acceleration: average of candidates in group
  const avgAccel = candidates.reduce((s, c) => s + (c.theme_acceleration || 0), 0) / candidates.length;

  // Sort: highest confidence first (bottleneck tickers already surface naturally)
  const sorted = [...candidates].sort((a, b) => (b.exposure_confidence || 0) - (a.exposure_confidence || 0));

  return (
    <div style={{
      marginBottom: '12px',
      borderRadius: '10px',
      border: '1px solid var(--border-strong)',
      overflow: 'hidden',
    }}>
      {/* Theme header */}
      <button
        onClick={() => setCollapsed(p => !p)}
        style={{
          width: '100%', display: 'flex', alignItems: 'center', gap: '10px',
          padding: '10px 14px',
          background: 'var(--surface-strong)',
          border: 'none', cursor: 'pointer',
          textAlign: 'left',
        }}
      >
        <span style={{ flex: 1, fontWeight: 600, fontSize: '13px', color: 'var(--text)' }}>
          {theme}
        </span>
        <span style={{ fontSize: '11px', color: 'var(--muted)', marginRight: '4px' }}>
          {candidates.length} ticker{candidates.length !== 1 ? 's' : ''}
        </span>
        <div style={{ width: '72px' }}>
          <MomentumBar value={avgAccel} label="" />
        </div>
        <span style={{ fontSize: '10px', color: 'var(--muted)', marginLeft: '4px' }}>
          {collapsed ? '▶' : '▼'}
        </span>
      </button>

      {/* Ticker rows */}
      {!collapsed && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: '6px', padding: '10px 12px 12px' }}>
          {sorted.map(c => <TickerRow key={`${c.theme_id}-${c.ticker}-${c.node_id}`} c={c} />)}
        </div>
      )}
    </div>
  );
}

/**
 * ThemeCandidatesPanel
 *
 * Props:
 *   candidates  – Array of ThemeExposureCandidate.to_dict() objects
 *   isStreaming – Boolean; if true shows a subtle "updating…" hint
 */
export default function ThemeCandidatesPanel({ candidates = [], isStreaming = false }) {
  if (!candidates || candidates.length === 0) {
    return (
      <div style={{ padding: '16px', color: 'var(--muted)', fontSize: '13px' }}>
        {isStreaming ? 'Waiting for theme signals…' : 'No theme signals detected.'}
      </div>
    );
  }

  // Group by theme label, preserving insertion order
  const groups = {};
  for (const c of candidates) {
    const key = c.theme || 'Unknown Theme';
    if (!groups[key]) groups[key] = [];
    groups[key].push(c);
  }

  const totalThemes = Object.keys(groups).length;
  const freshDate = candidates[0]?.freshness_date || '';

  return (
    <div>
      {/* Summary bar */}
      <div style={{
        display: 'flex', alignItems: 'center', gap: '12px',
        marginBottom: '12px', fontSize: '12px', color: 'var(--muted)',
      }}>
        <span>
          <strong style={{ color: 'var(--text)' }}>{candidates.length}</strong> signal{candidates.length !== 1 ? 's' : ''}
          {' across '}
          <strong style={{ color: 'var(--text)' }}>{totalThemes}</strong> theme{totalThemes !== 1 ? 's' : ''}
        </span>
        {freshDate && <span>· {freshDate}</span>}
        {isStreaming && (
          <span style={{ marginLeft: 'auto', color: 'var(--accent)', fontSize: '11px' }}>
            ⟳ updating…
          </span>
        )}
      </div>

      {/* One collapsible group per theme */}
      {Object.entries(groups).map(([theme, cands]) => (
        <ThemeGroup key={theme} theme={theme} candidates={cands} />
      ))}
    </div>
  );
}
