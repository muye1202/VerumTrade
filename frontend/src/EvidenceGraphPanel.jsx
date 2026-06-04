import { useState, useMemo } from 'react';

/* ── tiny icons (inline SVG) ────────────────────────── */
const ChevronDown = () => (
  <svg viewBox="0 0 24 24" width="14" height="14" fill="currentColor"><path d="M7.41 8.59L12 13.17l4.59-4.58L18 10l-6 6-6-6z"/></svg>
);
const ChevronRight = () => (
  <svg viewBox="0 0 24 24" width="14" height="14" fill="currentColor"><path d="M8.59 16.59L13.17 12 8.59 7.41 10 6l6 6-6 6z"/></svg>
);

/* ── Domain colour tokens ───────────────────────────── */
const DOMAIN_META = {
  catalyst:      { color: '#f59e0b', bg: 'rgba(245,158,11,0.10)', icon: '⚡' },
  market:        { color: '#3b82f6', bg: 'rgba(59,130,246,0.10)', icon: '📊' },
  sentiment:     { color: '#a78bfa', bg: 'rgba(167,139,250,0.10)', icon: '💬' },
  news:          { color: '#60a5fa', bg: 'rgba(96,165,250,0.10)', icon: '📰' },
  fundamentals:  { color: '#34d399', bg: 'rgba(52,211,153,0.10)', icon: '📈' },
};

const STANCE_STYLE = {
  bullish: { color: '#22c55e', bg: 'rgba(34,197,94,0.12)', label: 'Bullish' },
  bearish: { color: '#ef4444', bg: 'rgba(239,68,68,0.12)', label: 'Bearish' },
  mixed:   { color: '#f59e0b', bg: 'rgba(245,158,11,0.12)', label: 'Mixed' },
};

const SEVERITY_STYLE = {
  high:   { color: '#ef4444', bg: 'rgba(239,68,68,0.12)' },
  medium: { color: '#f59e0b', bg: 'rgba(245,158,11,0.12)' },
  low:    { color: '#60a5fa', bg: 'rgba(96,165,250,0.12)' },
};

/* ── Confidence bar ─────────────────────────────────── */
const ConfidenceBar = ({ value }) => {
  const pct = Math.round((value || 0) * 100);
  const hue = value >= 0.7 ? 142 : value >= 0.45 ? 45 : 0;
  return (
    <div className="eg-confidence-bar" title={`Confidence: ${pct}%`}>
      <div className="eg-confidence-track">
        <div
          className="eg-confidence-fill"
          style={{ width: `${pct}%`, background: `hsl(${hue}, 70%, 55%)` }}
        />
      </div>
      <span className="eg-confidence-label">{pct}%</span>
    </div>
  );
};

/* ── Badge helpers ──────────────────────────────────── */
const DomainBadge = ({ domain }) => {
  const meta = DOMAIN_META[domain] || DOMAIN_META.market;
  return (
    <span className="eg-badge eg-domain-badge" style={{ color: meta.color, background: meta.bg }}>
      {meta.icon} {domain}
    </span>
  );
};

const StanceBadge = ({ stance }) => {
  const s = STANCE_STYLE[stance] || STANCE_STYLE.mixed;
  return (
    <span className="eg-badge eg-stance-badge" style={{ color: s.color, background: s.bg }}>
      {s.label}
    </span>
  );
};

const SeverityBadge = ({ severity }) => {
  const s = SEVERITY_STYLE[severity] || SEVERITY_STYLE.medium;
  return (
    <span className="eg-badge eg-severity-badge" style={{ color: s.color, background: s.bg }}>
      {severity}
    </span>
  );
};

/* ── Stat card ──────────────────────────────────────── */
const StatCard = ({ label, value, accent }) => (
  <div className="eg-stat-card">
    <span className="eg-stat-value" style={{ color: accent }}>{value}</span>
    <span className="eg-stat-label">{label}</span>
  </div>
);

/* ── Collapsible section wrapper ────────────────────── */
const CollapsibleSection = ({ title, count, defaultOpen = false, accent, children }) => {
  const [open, setOpen] = useState(defaultOpen);
  return (
    <div className={`eg-section ${open ? 'open' : ''}`}>
      <button className="eg-section-header" onClick={() => setOpen(o => !o)}>
        <span className="eg-section-chevron">{open ? <ChevronDown /> : <ChevronRight />}</span>
        <span className="eg-section-title">{title}</span>
        <span className="eg-section-count" style={accent ? { color: accent } : undefined}>{count}</span>
      </button>
      {open && <div className="eg-section-body">{children}</div>}
    </div>
  );
};

/* ── Fact card ──────────────────────────────────────── */
const FactCard = ({ fact }) => (
  <div className="eg-fact-card">
    <div className="eg-fact-header">
      <code className="eg-fact-id">{fact.id}</code>
      <DomainBadge domain={fact.domain} />
      {fact.source_type === 'ledger_recovery' && (
        <span className="eg-badge eg-recovery-badge">recovery</span>
      )}
    </div>
    <p className="eg-fact-claim">{fact.claim}</p>
    <div className="eg-fact-meta">
      <ConfidenceBar value={fact.confidence} />
      {fact.source && <span className="eg-fact-source" title={fact.source}>src: {fact.source}</span>}
      {fact.as_of && <span className="eg-fact-date">{fact.as_of}</span>}
    </div>
  </div>
);

/* ── Inference card ─────────────────────────────────── */
const InferenceCard = ({ inference, factLookup }) => {
  const [showDeps, setShowDeps] = useState(false);
  const supportFacts = (inference.support_fact_ids || []).map(id => factLookup[id]).filter(Boolean);
  const counterFacts = (inference.counter_fact_ids || []).map(id => factLookup[id]).filter(Boolean);
  const hasDeps = supportFacts.length > 0 || counterFacts.length > 0;

  return (
    <div className="eg-inference-card">
      <div className="eg-inference-header">
        <code className="eg-inference-id">{inference.id}</code>
        <DomainBadge domain={inference.domain} />
        <StanceBadge stance={inference.stance} />
      </div>
      <p className="eg-inference-claim">{inference.claim}</p>

      <div className="eg-inference-details">
        <ConfidenceBar value={inference.confidence} />
        <span className="eg-inference-analyst">Analyst: <strong>{inference.analyst}</strong></span>
      </div>

      {inference.falsifier && (
        <div className="eg-falsifier">
          <span className="eg-falsifier-label">Falsifier:</span>
          <span className="eg-falsifier-text">{inference.falsifier}</span>
        </div>
      )}

      {hasDeps && (
        <div className="eg-deps">
          <button className="eg-deps-toggle" onClick={() => setShowDeps(d => !d)}>
            {showDeps ? <ChevronDown /> : <ChevronRight />}
            <span>{supportFacts.length} supporting · {counterFacts.length} counter evidence</span>
          </button>
          {showDeps && (
            <div className="eg-deps-list">
              {supportFacts.length > 0 && (
                <div className="eg-deps-group">
                  <span className="eg-deps-group-label support">Supporting facts</span>
                  {supportFacts.map(f => (
                    <div key={f.id} className="eg-dep-item support">
                      <code>{f.id}</code>
                      <span>{(f.claim || '').slice(0, 140)}</span>
                    </div>
                  ))}
                </div>
              )}
              {counterFacts.length > 0 && (
                <div className="eg-deps-group">
                  <span className="eg-deps-group-label counter">Counter facts</span>
                  {counterFacts.map(f => (
                    <div key={f.id} className="eg-dep-item counter">
                      <code>{f.id}</code>
                      <span>{(f.claim || '').slice(0, 140)}</span>
                    </div>
                  ))}
                </div>
              )}
            </div>
          )}
        </div>
      )}
    </div>
  );
};

/* ── Conflict card ──────────────────────────────────── */
const ConflictCard = ({ conflict }) => (
  <div className="eg-conflict-card">
    <div className="eg-conflict-header">
      <svg viewBox="0 0 24 24" width="16" height="16" fill="#f59e0b"><path d="M1 21h22L12 2 1 21zm12-3h-2v-2h2v2zm0-4h-2v-4h2v4z"/></svg>
      <span className="eg-conflict-reason">{conflict.reason}</span>
      <ConfidenceBar value={conflict.confidence} />
    </div>
    <div className="eg-conflict-claims">
      <div className="eg-conflict-claim-box claim-a">
        <span className="eg-conflict-claim-label">Claim A</span>
        <p>{(conflict.claim_a || '').slice(0, 220)}</p>
      </div>
      <div className="eg-conflict-vs">VS</div>
      <div className="eg-conflict-claim-box claim-b">
        <span className="eg-conflict-claim-label">Claim B</span>
        <p>{(conflict.claim_b || '').slice(0, 220)}</p>
      </div>
    </div>
    {(conflict.inference_ids?.length > 0 || conflict.fact_ids?.length > 0) && (
      <div className="eg-conflict-refs">
        {conflict.inference_ids?.length > 0 && (
          <span>Inferences: {conflict.inference_ids.join(', ')}</span>
        )}
        {conflict.fact_ids?.length > 0 && (
          <span>Facts: {conflict.fact_ids.join(', ')}</span>
        )}
      </div>
    )}
  </div>
);

/* ── Audit issue row ────────────────────────────────── */
const AuditRow = ({ issue }) => (
  <div className="eg-audit-row">
    <SeverityBadge severity={issue.severity} />
    <code className="eg-audit-code">{issue.code}</code>
    {issue.domain && <DomainBadge domain={issue.domain} />}
    <span className="eg-audit-msg">{issue.message}</span>
    {issue.node_id && <code className="eg-audit-node">{issue.node_id}</code>}
  </div>
);

const LedgerItem = ({ item, status }) => (
  <div className={`eg-ledger-item eg-ledger-item--${status}`}>
    <div className="eg-fact-header">
      <code className="eg-fact-id">{item.evidence_id}</code>
      <DomainBadge domain={String(item.source_agent || '').replace(/_analyst$/, '') || 'market'} />
      <StanceBadge stance={item.polarity || 'mixed'} />
      <span className="eg-badge eg-ledger-status">{status}</span>
    </div>
    <p className="eg-fact-claim">{item.claim}</p>
    <div className="eg-fact-meta">
      <ConfidenceBar value={Number(item.confidence || 0)} />
      {item.materiality !== undefined && <span className="eg-fact-source">materiality: {Math.round(Number(item.materiality || 0) * 100)}%</span>}
      {item.criticality !== undefined && <span className="eg-fact-source">criticality: {Math.round(Number(item.criticality || 0) * 100)}%</span>}
      {item.observed_at && <span className="eg-fact-date">{item.observed_at}</span>}
    </div>
    {(item.supports?.length > 0 || item.contradicts?.length > 0) && (
      <div className="eg-ledger-links">
        {item.supports?.map((value) => <span key={`s-${value}`} className="eg-ledger-link support">{value}</span>)}
        {item.contradicts?.map((value) => <span key={`c-${value}`} className="eg-ledger-link counter">{value}</span>)}
      </div>
    )}
  </div>
);

/* ════════════════════════════════════════════════════
   Main panel
   ════════════════════════════════════════════════════ */
const EvidenceGraphPanel = ({ data, reports }) => {
  const [domainFilter, setDomainFilter] = useState('all');

  const graph = data || {};
  const allReports = reports || {};
  const evidenceLedger = useMemo(() => allReports.evidence_ledger || graph.evidence_ledger || [], [allReports.evidence_ledger, graph.evidence_ledger]);
  const admissibility = allReports.admissibility_report || graph.admissibility_report || {};
  const acceptedEvidenceIds = useMemo(() => new Set((admissibility.accepted_evidence_ids || []).map(String)), [admissibility.accepted_evidence_ids]);
  const criticalEvidenceIds = allReports.critical_evidence_ids || graph.critical_evidence_ids || [];
  const facts = useMemo(() => graph.facts || [], [graph.facts]);
  const inferences = useMemo(() => graph.inferences || [], [graph.inferences]);
  const conflicts = useMemo(() => graph.conflicts || [], [graph.conflicts]);
  const auditIssues = useMemo(() => graph.audit_issues || [], [graph.audit_issues]);

  // Fact lookup map for linking
  const factLookup = useMemo(() => {
    const map = {};
    for (const f of facts) { map[f.id] = f; }
    return map;
  }, [facts]);

  // Derive unique domains
  const domains = useMemo(() => {
    const set = new Set();
    for (const f of facts) set.add(f.domain);
    for (const i of inferences) set.add(i.domain);
    return ['all', ...Array.from(set).sort()];
  }, [facts, inferences]);

  // Filtered data
  const filteredFacts = domainFilter === 'all' ? facts : facts.filter(f => f.domain === domainFilter);
  const filteredInferences = domainFilter === 'all' ? inferences : inferences.filter(i => i.domain === domainFilter);
  const filteredAuditIssues = domainFilter === 'all' ? auditIssues : auditIssues.filter(a => a.domain === domainFilter);

  // Stance distribution for summary
  const stanceCounts = useMemo(() => {
    const counts = { bullish: 0, bearish: 0, mixed: 0 };
    for (const i of inferences) {
      counts[i.stance] = (counts[i.stance] || 0) + 1;
    }
    return counts;
  }, [inferences]);

  const avgConfidence = useMemo(() => {
    if (inferences.length === 0) return 0;
    return inferences.reduce((sum, i) => sum + (i.confidence || 0), 0) / inferences.length;
  }, [inferences]);

  const highSeverityCount = auditIssues.filter(a => a.severity === 'high').length;

  if (!facts.length && !inferences.length && !conflicts.length && !auditIssues.length && !evidenceLedger.length) {
    return (
      <div className="eg-panel">
        <div className="eg-empty">
          <svg viewBox="0 0 24 24" width="48" height="48" fill="currentColor" opacity="0.2"><path d="M12 2C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2zm-1 17.93c-3.95-.49-7-3.85-7-7.93 0-.62.08-1.21.21-1.79L9 15v1c0 1.1.9 2 2 2v1.93zm6.9-2.54c-.26-.81-1-1.39-1.9-1.39h-1v-3c0-.55-.45-1-1-1H8v-2h2c.55 0 1-.45 1-1V7h2c1.1 0 2-.9 2-2v-.41c2.93 1.19 5 4.06 5 7.41 0 2.08-.8 3.97-2.1 5.39z"/></svg>
          <p>No evidence graph data available yet.</p>
        </div>
      </div>
    );
  }

  return (
    <div className="eg-panel">
      {/* ── Top summary strip ───────────── */}
      <div className="eg-summary-strip">
        <StatCard label="Facts" value={facts.length} accent="#60a5fa" />
        <StatCard label="Ledger" value={evidenceLedger.length} accent="#93c5fd" />
        <StatCard label="Inferences" value={inferences.length} accent="#a78bfa" />
        <StatCard label="Conflicts" value={conflicts.length} accent="#f59e0b" />
        <StatCard label="Audit Issues" value={auditIssues.length} accent={highSeverityCount > 0 ? '#ef4444' : '#60a5fa'} />
        <StatCard label="Avg. Confidence" value={`${Math.round(avgConfidence * 100)}%`} accent="#34d399" />
      </div>

      {evidenceLedger.length > 0 && (
        <CollapsibleSection
          title="Evidence Ledger"
          count={`${acceptedEvidenceIds.size}/${evidenceLedger.length} accepted`}
          defaultOpen={true}
          accent="#93c5fd"
        >
          <div className="eg-ledger-summary">
            <span>{criticalEvidenceIds.length} critical evidence ID{criticalEvidenceIds.length === 1 ? '' : 's'}</span>
            <span>{(admissibility.downgraded_evidence || []).length} downgraded</span>
            <span>{(admissibility.rejected_evidence || []).length} rejected</span>
          </div>
          {evidenceLedger
            .slice()
            .sort((a, b) => Number(b.criticality || 0) - Number(a.criticality || 0))
            .map((item) => (
              <LedgerItem
                key={item.evidence_id}
                item={item}
                status={acceptedEvidenceIds.has(String(item.evidence_id)) ? 'accepted' : 'review'}
              />
            ))}
        </CollapsibleSection>
      )}

      {/* ── Stance distribution ─────────── */}
      <div className="eg-stance-bar">
        <span className="eg-stance-segment bullish" style={{ flex: stanceCounts.bullish || 0.1 }}>
          {stanceCounts.bullish > 0 && `${stanceCounts.bullish} Bullish`}
        </span>
        <span className="eg-stance-segment mixed" style={{ flex: stanceCounts.mixed || 0.1 }}>
          {stanceCounts.mixed > 0 && `${stanceCounts.mixed} Mixed`}
        </span>
        <span className="eg-stance-segment bearish" style={{ flex: stanceCounts.bearish || 0.1 }}>
          {stanceCounts.bearish > 0 && `${stanceCounts.bearish} Bearish`}
        </span>
      </div>

      {/* ── Domain filter ───────────────── */}
      <div className="eg-domain-filter">
        {domains.map(d => {
          const meta = DOMAIN_META[d];
          const isActive = domainFilter === d;
          return (
            <button
              key={d}
              className={`eg-domain-filter-btn ${isActive ? 'active' : ''}`}
              onClick={() => setDomainFilter(d)}
              style={isActive && meta ? { borderColor: meta.color, color: meta.color, background: meta.bg } : undefined}
            >
              {meta ? `${meta.icon} ${d}` : 'All domains'}
            </button>
          );
        })}
      </div>

      {/* ── Inferences ──────────────────── */}
      <CollapsibleSection
        title="Analyst Inferences"
        count={filteredInferences.length}
        defaultOpen={true}
        accent="#a78bfa"
      >
        {filteredInferences.length === 0 ? (
          <p className="eg-empty-hint">No inferences in this domain.</p>
        ) : (
          filteredInferences
            .sort((a, b) => (b.confidence || 0) - (a.confidence || 0))
            .map(inf => <InferenceCard key={inf.id} inference={inf} factLookup={factLookup} />)
        )}
      </CollapsibleSection>

      {/* ── Conflicts ───────────────────── */}
      {conflicts.length > 0 && (
        <CollapsibleSection title="Conflicts" count={conflicts.length} defaultOpen={true} accent="#f59e0b">
          {conflicts.map((c, i) => <ConflictCard key={i} conflict={c} />)}
        </CollapsibleSection>
      )}

      {/* ── Facts ───────────────────────── */}
      <CollapsibleSection title="Vendor Facts" count={filteredFacts.length} defaultOpen={false} accent="#60a5fa">
        {filteredFacts.length === 0 ? (
          <p className="eg-empty-hint">No facts in this domain.</p>
        ) : (
          filteredFacts.map(f => <FactCard key={f.id} fact={f} />)
        )}
      </CollapsibleSection>

      {/* ── Audit Issues ────────────────── */}
      {filteredAuditIssues.length > 0 && (
        <CollapsibleSection
          title="Audit Issues"
          count={filteredAuditIssues.length}
          defaultOpen={false}
          accent={highSeverityCount > 0 ? '#ef4444' : '#f59e0b'}
        >
          {filteredAuditIssues.map((issue, i) => <AuditRow key={i} issue={issue} />)}
        </CollapsibleSection>
      )}
    </div>
  );
};

export default EvidenceGraphPanel;
