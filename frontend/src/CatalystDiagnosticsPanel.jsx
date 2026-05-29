import { memo, useState } from 'react';
import { JsonReportView } from './JsonReportRenderer';
import { humanizeKey } from './jsonReportUtils';

const STATUS_META = {
  'Parse failure': { tone: 'danger', label: 'Parse failure' },
  'Fetch/source data sparse': { tone: 'warning', label: 'Sparse source data' },
  'No decision-useful catalyst': { tone: 'neutral', label: 'Low signal' },
  'Data-quality note': { tone: 'warning', label: 'Data quality' },
};

const ChevronDown = () => (
  <svg viewBox="0 0 24 24" width="14" height="14" fill="currentColor"><path d="M7.41 8.59L12 13.17l4.59-4.58L18 10l-6 6-6-6z"/></svg>
);

const ChevronRight = () => (
  <svg viewBox="0 0 24 24" width="14" height="14" fill="currentColor"><path d="M8.59 16.59L13.17 12 8.59 7.41 10 6l6 6-6 6z"/></svg>
);

const DiagnosticBadge = ({ diagnosis }) => {
  const meta = STATUS_META[diagnosis] || STATUS_META['Data-quality note'];
  return (
    <span className={`cdp-badge cdp-badge--${meta.tone}`}>
      {meta.label}
    </span>
  );
};

const Metric = ({ label, value, tone }) => (
  <div className="cdp-metric">
    <span className="cdp-metric__label">{label}</span>
    <span className={`cdp-metric__value ${tone ? `cdp-metric__value--${tone}` : ''}`}>
      {Array.isArray(value) ? (value.length ? value.join(', ') : 'None') : String(value ?? 'N/A')}
    </span>
  </div>
);

const RawSection = memo(({ label, data, initiallyOpen = false }) => {
  const [open, setOpen] = useState(initiallyOpen);
  if (data === null || data === undefined || data === '') return null;

  return (
    <div className={`cdp-stage ${open ? 'cdp-stage--open' : ''}`}>
      <button className="cdp-stage__header" onClick={() => setOpen((current) => !current)}>
        <span className="cdp-stage__chevron">{open ? <ChevronDown /> : <ChevronRight />}</span>
        <span className="cdp-stage__title">{humanizeKey(label)}</span>
        <span className="cdp-stage__hint">{open ? 'Visible' : 'Open JSON'}</span>
      </button>
      {open && (
        <div className="cdp-stage__body">
          <JsonReportView data={data} />
        </div>
      )}
    </div>
  );
});

const CatalystDiagnosticsPanel = ({ data }) => {
  if (!data) return null;

  const summary = data.summary || {};
  const raw = data.raw || {};
  const notes = Array.isArray(data.data_quality_notes) ? data.data_quality_notes : [];

  return (
    <div className="cdp-panel">
      <div className="cdp-header">
        <div className="cdp-header__copy">
          <span className="cdp-kicker">Catalyst Diagnostics</span>
          <strong>{summary.primary_diagnosis || 'Data quality note'}</strong>
        </div>
        <DiagnosticBadge diagnosis={summary.primary_diagnosis} />
      </div>

      <div className="cdp-metrics">
        <Metric label="Parse" value={summary.parse_status} tone={summary.parse_status === 'failed' ? 'danger' : 'ok'} />
        <Metric label="Action" value={summary.recommended_action || 'N/A'} />
        <Metric label="Quality Gate" value={summary.bundle_quality_gate || 'N/A'} />
        <Metric label="Accepted Events" value={summary.accepted_catalyst_events ?? 'N/A'} />
        <Metric label="Evidence Rows" value={summary.structured_evidence_rows ?? 0} />
        <Metric label="Missing Sources" value={summary.missing_sources || []} tone={(summary.missing_sources || []).length ? 'warning' : ''} />
      </div>

      {(summary.fallback_mode || summary.parse_failure_stage || summary.parse_exception || notes.length > 0) && (
        <div className="cdp-notes">
          {summary.fallback_mode && <Metric label="Fallback Mode" value={summary.fallback_mode} tone="warning" />}
          {summary.parse_failure_stage && <Metric label="Parse Stage" value={summary.parse_failure_stage} tone="danger" />}
          {summary.parse_exception && <Metric label="Parse Exception" value={summary.parse_exception} tone="danger" />}
          {notes.length > 0 && (
            <div className="cdp-note-list">
              <span className="cdp-note-list__label">Data-Quality Notes</span>
              <ul>
                {notes.map((note, index) => <li key={`${note}-${index}`}>{note}</li>)}
              </ul>
            </div>
          )}
        </div>
      )}

      <div className="cdp-stages">
        <RawSection label="catalyst_parse_telemetry" data={raw.catalyst_parse_telemetry} initiallyOpen />
        <RawSection label="catalyst_event_report_structured" data={raw.catalyst_event_report_structured} />
        <RawSection label="catalyst_event_bundle_quality" data={raw.catalyst_event_bundle_quality} />
        <RawSection label="catalyst_evidence" data={raw.catalyst_evidence} />
      </div>
    </div>
  );
};

export default CatalystDiagnosticsPanel;
