import { memo, useState } from 'react';
import { JsonReportView } from './JsonReportRenderer';
import { humanizeKey } from './jsonReportUtils';

/* ── Icons ────────────────────────────────────────────── */

const ChevronDown = () => (
  <svg viewBox="0 0 24 24" width="14" height="14" fill="currentColor"><path d="M7.41 8.59L12 13.17l4.59-4.58L18 10l-6 6-6-6z"/></svg>
);
const ChevronRight = () => (
  <svg viewBox="0 0 24 24" width="14" height="14" fill="currentColor"><path d="M8.59 16.59L13.17 12 8.59 7.41 10 6l6 6-6 6z"/></svg>
);

/* ── Status / Agent / Kind badges ─────────────────────── */

const STATUS_STYLE = {
  available: { color: '#34d399', bg: 'rgba(52,211,153,0.12)', label: 'Available' },
  missing:   { color: '#9ca3af', bg: 'rgba(156,163,175,0.12)', label: 'Pending' },
  partial:   { color: '#f59e0b', bg: 'rgba(245,158,11,0.12)', label: 'Partial' },
};

const AGENT_META = {
  'Trader':           { color: '#f472b6', bg: 'rgba(244,114,182,0.10)', icon: '🧠' },
  'Risk Management':  { color: '#ef4444', bg: 'rgba(239,68,68,0.10)',  icon: '🛡️' },
  'Execution Guard':  { color: '#f59e0b', bg: 'rgba(245,158,11,0.10)', icon: '🚦' },
};

const AgentBadge = ({ agent }) => {
  const meta = AGENT_META[agent] || { color: '#a78bfa', bg: 'rgba(167,139,250,0.10)', icon: '🤖' };
  return (
    <span className="trp-badge" style={{ color: meta.color, background: meta.bg }}>
      {meta.icon} {agent}
    </span>
  );
};

const StatusBadge = ({ status }) => {
  const s = STATUS_STYLE[status] || STATUS_STYLE.missing;
  return (
    <span className="trp-badge" style={{ color: s.color, background: s.bg }}>
      {s.label}
    </span>
  );
};

const KindBadge = ({ kind }) => (
  <span className="trp-badge" style={{ color: '#93c5fd', background: 'rgba(147,197,253,0.12)' }}>
    {humanizeKey(kind)}
  </span>
);

/* ── Summary row: compact metrics ─────────────────────── */

const SummaryMetrics = ({ summary }) => {
  if (!summary || Object.keys(summary).length === 0) return null;
  return (
    <div className="trp-metrics">
      {Object.entries(summary).map(([k, v]) => {
        let display;
        if (typeof v === 'boolean') {
          display = <span className={v ? 'trp-metric-yes' : 'trp-metric-no'}>{v ? '✓ Yes' : '✗ No'}</span>;
        } else if (v === null || v === undefined) {
          display = <span className="trp-metric-na">N/A</span>;
        } else {
          display = <span className="trp-metric-value">{String(v)}</span>;
        }

        return (
          <div key={k} className="trp-metric-chip">
            <span className="trp-metric-label">{humanizeKey(k)}</span>
            {display}
          </div>
        );
      })}
    </div>
  );
};

/* ── Stage card (foldable) ────────────────────────────── */

const StageCard = memo(({ stage }) => {
  const { label, agent, kind, status, summary, content } = stage;
  const isAvailable = status === 'available';
  const [open, setOpen] = useState(false);

  return (
    <div className={`trp-stage ${open ? 'trp-stage--open' : ''} ${!isAvailable ? 'trp-stage--pending' : ''}`}>
      <button className="trp-stage__header" onClick={() => setOpen((o) => !o)}>
        <span className="trp-stage__chevron">{open ? <ChevronDown /> : <ChevronRight />}</span>
        <span className="trp-stage__title">{label}</span>
        <div className="trp-stage__badges">
          <AgentBadge agent={agent} />
          <KindBadge kind={kind} />
          <StatusBadge status={status} />
        </div>
      </button>

      {open && (
        <div className="trp-stage__body">
          {isAvailable ? (
            <>
              <SummaryMetrics summary={summary} />
              <div className="trp-stage__content">
                <JsonReportView data={content} />
              </div>
            </>
          ) : (
            <p className="trp-stage__pending-hint">
              ⏳ Waiting for this stage to complete…
            </p>
          )}
        </div>
      )}
    </div>
  );
});

/* ── Top-level progress header ────────────────────────── */

const ProgressHeader = ({ trace, availableCount, total, progressPct }) => (
  <div className="trp-header">
    <div className="trp-header__left">
      <span className="trp-header__ticker">{trace.ticker || '—'}</span>
      <span className="trp-header__horizon">{trace.time_horizon || '—'}</span>
    </div>
    <div className="trp-header__right">
      <div className="trp-progress">
        <div className="trp-progress__bar">
          <div
            className="trp-progress__fill"
            style={{ width: `${progressPct}%` }}
          />
        </div>
        <span className="trp-progress__label">
          {availableCount}/{total} stages · {progressPct}%
        </span>
      </div>
      <StatusBadge status={trace.status} />
    </div>
  </div>
);

/* ── Main panel ───────────────────────────────────────── */

const TraderReasoningPanel = ({ data }) => {
  const trace = data || {};
  const stages = trace.stages || [];

  if (!stages.length) {
    return (
      <div className="trp-panel">
        <div className="trp-empty">
          <p>No reasoning trace available yet.</p>
        </div>
      </div>
    );
  }

  const availableCount = stages.filter((s) => s.status === 'available').length;
  const progressPct = Math.round((availableCount / stages.length) * 100);

  return (
    <div className="trp-panel">
      <ProgressHeader
        trace={trace}
        availableCount={availableCount}
        total={stages.length}
        progressPct={progressPct}
      />

      <div className="trp-stages">
        {stages.map((stage) => (
          <StageCard key={stage.id} stage={stage} />
        ))}
      </div>
    </div>
  );
};

export default TraderReasoningPanel;
