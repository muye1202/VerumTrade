import {
  formatDiscoveryScore,
  getEvidencePackRows,
  getThesisCardRows,
  topDecisionRows,
} from './discoverySignalsViewModel';

const TIER_LABELS = {
  actionable: 'Actionable',
  watchlist: 'Watchlist',
  theme_candidate: 'Theme Candidate',
  rejected: 'Reject',
};

function TierBadge({ tier }) {
  const normalized = String(tier || 'rejected').toLowerCase();
  return (
    <span className={`decision-tier ${normalized}`}>
      {TIER_LABELS[normalized] || normalized.replaceAll('_', ' ')}
    </span>
  );
}

function MetricCell({ label, value }) {
  return (
    <div className="decision-metric">
      <strong>{formatDiscoveryScore(value)}</strong>
      <span>{label}</span>
    </div>
  );
}

function EvidencePreview({ pack }) {
  const bullets = Array.isArray(pack?.evidence_bullets) ? pack.evidence_bullets.slice(0, 2) : [];
  if (bullets.length === 0) return null;
  return (
    <ul className="decision-evidence">
      {bullets.map((item, index) => (
        <li key={`${index}-${item}`}>{item}</li>
      ))}
    </ul>
  );
}

function ThesisCardDetails({ card }) {
  if (!card) return null;
  const evidence = Array.isArray(card.evidence) ? card.evidence.slice(0, 3) : [];
  const risks = Array.isArray(card.risks) ? card.risks.slice(0, 3) : [];
  const killConditions = Array.isArray(card.kill_conditions) ? card.kill_conditions.slice(0, 3) : [];

  return (
    <details className="thesis-details">
      <summary>Thesis card</summary>
      {card.bull_thesis && <p>{card.bull_thesis}</p>}
      {evidence.length > 0 && (
        <div>
          <span>Evidence</span>
          <ul>{evidence.map((item, index) => <li key={`e-${index}-${item}`}>{item}</li>)}</ul>
        </div>
      )}
      {risks.length > 0 && (
        <div>
          <span>Risks</span>
          <ul>{risks.map((item, index) => <li key={`r-${index}-${item}`}>{item}</li>)}</ul>
        </div>
      )}
      {killConditions.length > 0 && (
        <div>
          <span>Kill Conditions</span>
          <ul>{killConditions.map((item, index) => <li key={`k-${index}-${item}`}>{item}</li>)}</ul>
        </div>
      )}
    </details>
  );
}

function DecisionCard({ candidate, pack, thesisCard, compact }) {
  return (
    <article className="decision-card">
      <div className="decision-card-top">
        <div>
          <span className="signal-ticker">{candidate.ticker}</span>
          <TierBadge tier={candidate.tier} />
        </div>
        <div className="decision-score">
          <strong>{formatDiscoveryScore(candidate.discovery_score)}</strong>
          <span>Discovery</span>
        </div>
      </div>

      <div className="decision-metric-grid">
        <MetricCell label="Thesis" value={candidate.thesis_score} />
        <MetricCell label="Evidence" value={candidate.evidence_score} />
        <MetricCell label="Momentum" value={candidate.momentum_confirmation_score} />
        <MetricCell label="Gap" value={candidate.attention_gap_score} />
      </div>

      {pack?.primary_theme && (
        <div className="decision-theme-line">
          <span>{pack.primary_theme}</span>
          {pack.primary_bottleneck && <small>{pack.primary_bottleneck}</small>}
        </div>
      )}

      <EvidencePreview pack={pack} />
      {!compact && <ThesisCardDetails card={thesisCard} />}
    </article>
  );
}

export default function DiscoveryDecisionPanel({
  twoLayerScoring,
  evidencePacks,
  thesisCards,
  isStreaming = false,
  compact = false,
}) {
  const decisions = topDecisionRows(twoLayerScoring, compact ? 3 : 8);
  const packsByTicker = new Map(getEvidencePackRows(evidencePacks).map((pack) => [pack.ticker, pack]));
  const cardsByTicker = new Map(getThesisCardRows(thesisCards).map((card) => [card.ticker, card]));

  if (decisions.length === 0 && !isStreaming) return null;

  return (
    <div className={`discovery-decision-panel ${compact ? 'compact' : ''}`}>
      <div className="decision-summary-bar">
        <span><strong>{decisions.length}</strong> ranked candidate{decisions.length !== 1 ? 's' : ''}</span>
        {isStreaming && <span className="signal-streaming">updating...</span>}
      </div>

      {decisions.length > 0 ? (
        <div className="decision-card-grid">
          {decisions.map((candidate) => (
            <DecisionCard
              key={`${candidate.ticker}-${candidate.tier}`}
              candidate={candidate}
              pack={packsByTicker.get(candidate.ticker)}
              thesisCard={cardsByTicker.get(candidate.ticker)}
              compact={compact}
            />
          ))}
        </div>
      ) : (
        <div className="signal-empty-state">
          {isStreaming ? 'Waiting for candidate tiering.' : 'No candidate tiers generated.'}
        </div>
      )}
    </div>
  );
}
