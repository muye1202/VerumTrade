import { memo, useMemo, useState } from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { JsonReportView } from './JsonReportRenderer';
import { getDecisionTraceViewModel } from './DecisionTracePanel.helpers';

const ChevronDown = () => (
  <svg viewBox="0 0 24 24" width="14" height="14" fill="currentColor"><path d="M7.41 8.59L12 13.17l4.59-4.58L18 10l-6 6-6-6z"/></svg>
);

const ChevronRight = () => (
  <svg viewBox="0 0 24 24" width="14" height="14" fill="currentColor"><path d="M8.59 16.59L13.17 12 8.59 7.41 10 6l6 6-6 6z"/></svg>
);

const ACTION_TONES = {
  BUY: 'buy',
  SELL: 'sell',
  HOLD: 'hold',
};

const STANCE_TONES = {
  bullish: 'buy',
  bearish: 'sell',
  mixed: 'hold',
};

const truncate = (value, limit = 520) => {
  const text = String(value || '').trim();
  if (text.length <= limit) return text;
  return `${text.slice(0, limit).trim()}...`;
};

const pct = (value) => {
  if (typeof value !== 'number' || !Number.isFinite(value)) return null;
  return `${Math.round(value * 100)}%`;
};

const TraceBadge = ({ children, tone = 'neutral' }) => (
  <span className={`dtp-badge dtp-badge--${tone}`}>{children}</span>
);

const Metric = ({ label, value, tone }) => (
  <div className="dtp-metric">
    <span className="dtp-metric__value dtp-metric__value--compact">
      {value}
    </span>
    <span className={`dtp-metric__label ${tone ? `dtp-metric__label--${tone}` : ''}`}>
      {label}
    </span>
  </div>
);

const FactRow = ({ fact, id, missing }) => (
  <div className={`dtp-fact-row ${missing ? 'dtp-fact-row--missing' : ''}`}>
    <code>{id}</code>
    {missing ? (
      <span className="dtp-muted">Details unavailable in evidence graph.</span>
    ) : (
      <>
        <span>{fact.claim || fact.text || 'No claim text available.'}</span>
        <div className="dtp-fact-meta">
          {fact.source && <TraceBadge>{fact.source}</TraceBadge>}
          {fact.domain && <TraceBadge>{fact.domain}</TraceBadge>}
          {pct(fact.confidence) && <TraceBadge>{pct(fact.confidence)}</TraceBadge>}
          {fact.source_type === 'ledger_recovery' && <TraceBadge tone="warning">recovery</TraceBadge>}
        </div>
      </>
    )}
  </div>
);

const InferenceCard = memo(({ row }) => {
  const [open, setOpen] = useState(false);
  const inference = row.inference || {};
  const stance = String(inference.stance || 'mixed').toLowerCase();
  const confidence = pct(inference.confidence);
  const factCount = row.supportFacts.length + row.counterFacts.length;

  if (row.missing) {
    return (
      <div className="dtp-inference dtp-inference--missing">
        <div className="dtp-inference__header">
          <code>{row.id}</code>
          <TraceBadge tone="warning">details unavailable</TraceBadge>
        </div>
        <p className="dtp-muted">This inference ID was selected by the decision trace but was not found in the evidence graph.</p>
      </div>
    );
  }

  return (
    <div className="dtp-inference">
      <div className="dtp-inference__header">
        <code>{row.id}</code>
        {inference.domain && <TraceBadge>{inference.domain}</TraceBadge>}
        {inference.stance && <TraceBadge tone={STANCE_TONES[stance] || 'neutral'}>{inference.stance}</TraceBadge>}
        {confidence && <TraceBadge>{confidence}</TraceBadge>}
      </div>
      <p className="dtp-inference__claim">{inference.claim || 'No inference claim available.'}</p>
      {inference.falsifier && (
        <div className="dtp-falsifier">
          <span>Falsifier</span>
          <p>{inference.falsifier}</p>
        </div>
      )}
      <button className="dtp-inline-toggle" onClick={() => setOpen((current) => !current)}>
        {open ? <ChevronDown /> : <ChevronRight />}
        <span>{factCount} linked fact{factCount === 1 ? '' : 's'}</span>
      </button>
      {open && (
        <div className="dtp-linked-facts">
          {row.supportFacts.length > 0 && (
            <div className="dtp-linked-fact-group">
              <span className="dtp-linked-fact-group__label">Supporting facts</span>
              {row.supportFacts.map((fact) => (
                <FactRow key={fact.id} id={fact.id} fact={fact} missing={false} />
              ))}
            </div>
          )}
          {row.counterFacts.length > 0 && (
            <div className="dtp-linked-fact-group">
              <span className="dtp-linked-fact-group__label">Counter facts</span>
              {row.counterFacts.map((fact) => (
                <FactRow key={fact.id} id={fact.id} fact={fact} missing={false} />
              ))}
            </div>
          )}
          {factCount === 0 && <p className="dtp-muted">No supporting or counter facts were linked for this inference.</p>}
        </div>
      )}
    </div>
  );
});

const AuditIssueRow = ({ issue }) => {
  const severity = String(issue.severity || 'medium').toLowerCase();
  return (
    <div className={`dtp-audit-row dtp-audit-row--${severity}`}>
      <TraceBadge tone={severity === 'high' ? 'danger' : severity === 'medium' ? 'warning' : 'neutral'}>
        {severity}
      </TraceBadge>
      {issue.code && <code>{issue.code}</code>}
      <span>{issue.message || 'Trace audit issue.'}</span>
      {issue.domain && <TraceBadge>{issue.domain}</TraceBadge>}
      {issue.node_id && <code>{issue.node_id}</code>}
    </div>
  );
};

const CollapsibleBlock = ({ title, hint, defaultOpen = false, children }) => {
  const [open, setOpen] = useState(defaultOpen);
  return (
    <div className={`dtp-collapse ${open ? 'dtp-collapse--open' : ''}`}>
      <button className="dtp-collapse__header" onClick={() => setOpen((current) => !current)}>
        <span className="dtp-collapse__chevron">{open ? <ChevronDown /> : <ChevronRight />}</span>
        <span className="dtp-collapse__title">{title}</span>
        {hint && <span className="dtp-collapse__hint">{hint}</span>}
      </button>
      {open && <div className="dtp-collapse__body">{children}</div>}
    </div>
  );
};

const DecisionTracePanel = ({ trace, evidenceGraph }) => {
  const viewModel = useMemo(
    () => getDecisionTraceViewModel(trace, evidenceGraph),
    [trace, evidenceGraph],
  );
  const { decision, thesis, summary } = viewModel;
  const action = decision.action || 'UNKNOWN';
  const auditHint = viewModel.auditIssues.length
    ? `${viewModel.auditIssues.length} issue${viewModel.auditIssues.length === 1 ? '' : 's'}`
    : 'clean';
  const auditDefaultOpen = viewModel.auditIssues.length > 0 && viewModel.auditIssues.length <= 4;

  if (!trace || Object.keys(trace).length === 0) {
    return (
      <div className="dtp-panel">
        <div className="dtp-empty">
          <p>No decision trace available yet.</p>
        </div>
      </div>
    );
  }

  return (
    <div className="dtp-panel">
      <div className="dtp-summary">
        <div className="dtp-decision-token">
          <span>{decision.ticker || 'Ticker'}</span>
          <strong className={`dtp-action dtp-action--${ACTION_TONES[action] || 'unknown'}`}>{action}</strong>
        </div>
        <Metric label="Inferences" value={`${summary.linkedInferenceCount}/${viewModel.inferenceRows.length}`} />
        <Metric label="Facts" value={`${summary.linkedFactCount}/${viewModel.factRows.length}`} />
        <Metric label="Sources" value={summary.sourceCount} />
        <Metric
          label="High audit"
          value={summary.highSeverityAuditCount}
          tone={summary.highSeverityAuditCount > 0 ? 'danger' : 'ok'}
        />
      </div>

      <section className="dtp-section">
        <div className="dtp-section__title-row">
          <h4>Decision</h4>
          <TraceBadge tone={ACTION_TONES[action] || 'neutral'}>{action}</TraceBadge>
        </div>
        <div className="dtp-decision-summary markdown-content">
          <ReactMarkdown remarkPlugins={[remarkGfm]}>
            {truncate(decision.summary) || 'No decision summary available.'}
          </ReactMarkdown>
        </div>
      </section>

      <section className="dtp-section">
        <div className="dtp-section__title-row">
          <h4>Thesis</h4>
          <TraceBadge>{viewModel.thesis.inferenceIds.length} inference links</TraceBadge>
        </div>
        <p>{thesis.claim || 'No thesis claim was linked to the decision.'}</p>
        {viewModel.thesis.inferenceIds.length > 0 && (
          <div className="dtp-id-row">
            {viewModel.thesis.inferenceIds.map((id) => <code key={id}>{id}</code>)}
          </div>
        )}
      </section>

      <section className="dtp-section">
        <div className="dtp-section__title-row">
          <h4>Evidence Chain</h4>
          {summary.missingInferenceCount > 0 && <TraceBadge tone="warning">{summary.missingInferenceCount} missing</TraceBadge>}
        </div>
        {viewModel.inferenceRows.length > 0 ? (
          <div className="dtp-inference-list">
            {viewModel.inferenceRows.map((row) => <InferenceCard key={row.id} row={row} />)}
          </div>
        ) : (
          <p className="dtp-muted">No selected inference links were provided by the decision trace.</p>
        )}
      </section>

      {viewModel.factRows.some((row) => row.missing) && (
        <section className="dtp-section">
          <div className="dtp-section__title-row">
            <h4>Unresolved Fact Links</h4>
            <TraceBadge tone="warning">{summary.missingFactCount} missing</TraceBadge>
          </div>
          <div className="dtp-fact-list">
            {viewModel.factRows.filter((row) => row.missing).map((row) => (
              <FactRow key={row.id} id={row.id} fact={row.fact} missing={row.missing} />
            ))}
          </div>
        </section>
      )}

      <section className="dtp-section">
        <div className="dtp-section__title-row">
          <h4>Source Coverage</h4>
          <TraceBadge>{viewModel.sourceLabels.length} sources</TraceBadge>
        </div>
        {viewModel.sourceLabels.length > 0 ? (
          <div className="dtp-source-list">
            {viewModel.sourceLabels.map((source) => <TraceBadge key={source}>{source}</TraceBadge>)}
          </div>
        ) : (
          <p className="dtp-muted">No source labels were attached to this decision trace.</p>
        )}
      </section>

      <CollapsibleBlock title="Audit Warnings" hint={auditHint} defaultOpen={auditDefaultOpen}>
        {viewModel.auditIssues.length > 0 ? (
          <div className="dtp-audit-list">
            {viewModel.auditIssues.map((issue, index) => (
              <AuditIssueRow key={`${issue.code || 'audit'}-${issue.node_id || index}`} issue={issue} />
            ))}
          </div>
        ) : (
          <p className="dtp-muted">No trace-quality warnings were reported.</p>
        )}
      </CollapsibleBlock>

      <CollapsibleBlock title="Raw Trace" hint="debug JSON">
        <JsonReportView data={viewModel.rawTrace} />
      </CollapsibleBlock>
    </div>
  );
};

export default DecisionTracePanel;
