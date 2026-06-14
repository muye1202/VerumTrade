import {
  formatPercentScore,
  getSignalRows,
  topSignalRows,
} from './discoverySignalsViewModel';

function SignalScore({ value, label }) {
  return (
    <div className="signal-score">
      <strong>{formatPercentScore(value)}</strong>
      <span>{label}</span>
    </div>
  );
}

function EmptySignalState({ children }) {
  return <div className="signal-empty-state">{children}</div>;
}

function SignalEvidence({ items }) {
  const evidence = Array.isArray(items) ? items.slice(0, 2) : [];
  if (evidence.length === 0) return null;

  return (
    <ul className="signal-evidence-list">
      {evidence.map((item, index) => (
        <li key={`${index}-${item}`}>{item}</li>
      ))}
    </ul>
  );
}

function BusinessInflectionCard({ signal }) {
  const topMetrics = Array.isArray(signal.metrics) ? signal.metrics.slice(0, 3) : [];

  return (
    <article className="signal-row-card">
      <div className="signal-row-main">
        <div>
          <span className="signal-ticker">{signal.ticker}</span>
          {signal.inflection_type && <span className="signal-chip">{signal.inflection_type.replaceAll('_', ' ')}</span>}
        </div>
        <SignalScore value={signal.confidence} label="confidence" />
      </div>
      {topMetrics.length > 0 && (
        <div className="signal-chip-row">
          {topMetrics.map((metric) => (
            <span key={`${signal.ticker}-${metric}`} className="signal-chip subtle">{metric.replaceAll('_', ' ')}</span>
          ))}
        </div>
      )}
      <SignalEvidence items={signal.evidence} />
    </article>
  );
}

function AttentionGapCard({ signal }) {
  return (
    <article className="signal-row-card">
      <div className="signal-row-main">
        <div>
          <span className="signal-ticker">{signal.ticker}</span>
          {signal.theme && <span className="signal-chip">{signal.theme}</span>}
        </div>
        <SignalScore value={signal.attention_gap_score} label="gap score" />
      </div>
      <div className="signal-factor-grid">
        <SignalScore value={signal.inflection_score} label="inflection" />
        <SignalScore value={signal.theme_score} label="theme fit" />
        <SignalScore value={signal.accumulation_score} label="accumulation" />
        <SignalScore value={signal.under_attention_score} label="under attention" />
      </div>
    </article>
  );
}

export default function DiscoverySignalsPanel({
  businessInflection,
  attentionGap,
  isStreaming = false,
  compact = false,
}) {
  const inflectionRows = topSignalRows(businessInflection, 'confidence', compact ? 3 : 6);
  const attentionRows = topSignalRows(attentionGap, 'attention_gap_score', compact ? 3 : 6);
  const inflectionCount = getSignalRows(businessInflection).length;
  const attentionCount = getSignalRows(attentionGap).length;

  if (inflectionCount === 0 && attentionCount === 0 && !isStreaming) return null;

  return (
    <div className={`discovery-signals-panel ${compact ? 'compact' : ''}`}>
      <div className="signal-summary-bar">
        <span><strong>{inflectionCount}</strong> inflection signal{inflectionCount !== 1 ? 's' : ''}</span>
        <span><strong>{attentionCount}</strong> attention gap{attentionCount !== 1 ? 's' : ''}</span>
        {isStreaming && <span className="signal-streaming">updating...</span>}
      </div>

      <div className="discovery-signal-grid">
        <section className="discovery-signal-card">
          <div className="signal-card-header">
            <h4>Business Inflection</h4>
            <span>{inflectionRows.length ? 'ranked by confidence' : 'waiting'}</span>
          </div>
          {inflectionRows.length > 0 ? (
            <div className="signal-row-list">
              {inflectionRows.map((signal) => (
                <BusinessInflectionCard key={`${signal.ticker}-${signal.inflection_type}`} signal={signal} />
              ))}
            </div>
          ) : (
            <EmptySignalState>{isStreaming ? 'Waiting for fundamentals pass.' : 'No inflection signals detected.'}</EmptySignalState>
          )}
        </section>

        <section className="discovery-signal-card">
          <div className="signal-card-header">
            <h4>Attention Gap</h4>
            <span>{attentionRows.length ? 'ranked by opportunity' : 'waiting'}</span>
          </div>
          {attentionRows.length > 0 ? (
            <div className="signal-row-list">
              {attentionRows.map((signal) => (
                <AttentionGapCard key={`${signal.ticker}-${signal.theme || 'theme'}`} signal={signal} />
              ))}
            </div>
          ) : (
            <EmptySignalState>{isStreaming ? 'Waiting for cross-signal scoring.' : 'No attention gaps detected.'}</EmptySignalState>
          )}
        </section>
      </div>
    </div>
  );
}
