import { Fragment, memo, useMemo } from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { buildDebateWorkflowViewModel } from './debateWorkflowViewModel';

const ToneBadge = ({ children, tone = 'neutral' }) => (
  <span className={`dwp-badge dwp-badge--${tone}`}>{children}</span>
);

const WorkflowStep = ({ step, index }) => (
  <li className={`dwp-step dwp-step--${step.status}`}>
    <span className="dwp-step__index">{index + 1}</span>
    <div>
      <strong>{step.label}</strong>
      <p>{step.takeaway}</p>
    </div>
  </li>
);

const Metric = ({ label, value }) => (
  <div className="dwp-metric">
    <strong>{value}</strong>
    <span>{label}</span>
  </div>
);

const FieldPill = ({ children }) => (
  <span className="dwp-field-pill">{children}</span>
);

const CompactValue = ({ label, children }) => (
  <span className="dwp-compact-value">
    <span>{label}</span>
    <strong>{children}</strong>
  </span>
);

const EvidenceIdRow = ({ ids, empty = 'No evidence cited.' }) => (
  ids?.length > 0 ? (
    <div className="dwp-field-row">
      {ids.map((id) => <FieldPill key={id}>{id}</FieldPill>)}
    </div>
  ) : <p className="dwp-muted">{empty}</p>
);

const RawMarkdown = ({ children }) => {
  const text = String(children || '').trim();
  if (!text) return <p className="dwp-muted">No raw text captured.</p>;
  return (
    <div className="dwp-raw markdown-content">
      <ReactMarkdown remarkPlugins={[remarkGfm]}>{text}</ReactMarkdown>
    </div>
  );
};

const ParticipantCard = ({ participant }) => (
  <article className={`dwp-argument dwp-argument--${participant.tone}`}>
    <div className="dwp-argument__header">
      <div>
        <strong>{participant.role}</strong>
        <span>{participant.stance}</span>
      </div>
      <ToneBadge tone={participant.tone}>{participant.turnCount} turn{participant.turnCount === 1 ? '' : 's'}</ToneBadge>
    </div>
    <p>{participant.claim || 'No summarized claim captured.'}</p>
    <details className="dwp-details">
      <summary>Raw argument</summary>
      <RawMarkdown>{participant.content}</RawMarkdown>
    </details>
  </article>
);

const JudgeCard = ({ judge }) => (
  <article className="dwp-judge">
    <div className="dwp-argument__header">
      <div>
        <strong>{judge.role}</strong>
        <span>{judge.stance}</span>
      </div>
      <ToneBadge tone="judge">Resolution</ToneBadge>
    </div>
    <p>{judge.claim || 'No judge resolution captured.'}</p>
    <details className="dwp-details">
      <summary>Judge text</summary>
      <RawMarkdown>{judge.content}</RawMarkdown>
    </details>
  </article>
);

const Arena = ({ arena }) => (
  <section className="dwp-section">
    <div className="dwp-section__header">
      <div>
        <h4>{arena.title}</h4>
        <p>{arena.turns.length} captured turn{arena.turns.length === 1 ? '' : 's'} before judge resolution.</p>
      </div>
    </div>
    <div className="dwp-arguments">
      {arena.participants.map((participant) => (
        <ParticipantCard key={participant.role} participant={participant} />
      ))}
    </div>
    <JudgeCard judge={arena.judge} />
    <details className="dwp-details dwp-details--timeline">
      <summary>Turn-by-turn transcript</summary>
      <div className="dwp-turns">
        {arena.turns.length > 0 ? arena.turns.map((turn, index) => (
          <div key={`${turn.speaker}-${index}`} className="dwp-turn">
            <ToneBadge>{turn.speaker}</ToneBadge>
            <p>{turn.content}</p>
          </div>
        )) : (
          <p className="dwp-muted">No turn-level transcript captured.</p>
        )}
      </div>
    </details>
  </section>
);

const ChangePanel = ({ changePanel, traderProposal, finalDecision }) => (
  <section className="dwp-section dwp-section--changes">
    <div className="dwp-section__header">
      <div>
        <h4>What changed after debate</h4>
        <p>Highlights material differences between the Trader proposal and Risk Manager final decision.</p>
      </div>
    </div>
    <div className="dwp-change-grid">
      {changePanel.unchanged ? (
        <div className="dwp-change-empty">
          <strong>No material parameter changes detected.</strong>
          <p>The final decision preserved the comparable action and execution parameters. Review the Risk Manager resolution below for qualitative rationale.</p>
        </div>
      ) : changePanel.items.map((item) => (
        <div key={item.field} className="dwp-change">
          <span>{item.field}</span>
          <div className="dwp-change__values">
            <code>{item.before}</code>
            <span aria-hidden="true">to</span>
            <code>{item.after}</code>
          </div>
          <p>{item.impact}</p>
        </div>
      ))}
    </div>
    <div className="dwp-proposal-strip">
      <div>
        <span>Trader proposal</span>
        <strong>{traderProposal.action}</strong>
        <small>{traderProposal.executionIntent}</small>
      </div>
      <div>
        <span>Final decision</span>
        <strong>{finalDecision.action}</strong>
        <small>{finalDecision.executionIntent}</small>
      </div>
    </div>
  </section>
);

const EvidenceImpactPanel = ({ evidenceImpact, issues }) => (
  <section className="dwp-section dwp-section--impact">
    <div className="dwp-section__header">
      <div>
        <h4>Evidence and issues</h4>
        <p>Admissible evidence and the decision fields it put at risk.</p>
      </div>
    </div>
    <div className="dwp-impact-grid">
      <div className="dwp-impact-card">
        <strong>{evidenceImpact.acceptedCount}/{evidenceImpact.ledger.length}</strong>
        <span>Admissible evidence</span>
        <div className="dwp-evidence-list">
          {evidenceImpact.topEvidence.length > 0 ? evidenceImpact.topEvidence.map((item) => (
            <div key={item.id} className="dwp-evidence-row">
              <code>{item.id}</code>
              <p>{item.claim || 'No claim text captured.'}</p>
              <div className="dwp-mini-grid">
                <CompactValue label="Source">{item.source || 'N/A'}</CompactValue>
                <CompactValue label="Observed">{item.timestamp || 'N/A'}</CompactValue>
                <CompactValue label="Polarity">{item.polarity || 'neutral'}</CompactValue>
                <CompactValue label="Confidence">{item.confidence ?? 'N/A'}</CompactValue>
                <CompactValue label="Materiality">{item.materiality ?? 'N/A'}</CompactValue>
              </div>
              <EvidenceIdRow ids={[...item.supports, ...item.contradicts]} empty="No linked decision implications." />
              {item.statusReason && <small>{item.status}: {item.statusReason}</small>}
            </div>
          )) : <p className="dwp-muted">No evidence ledger items captured.</p>}
        </div>
      </div>
      <div className="dwp-impact-card">
        <strong>{issues.length}</strong>
        <span>Contested issues</span>
        <div className="dwp-issue-list">
          {issues.length > 0 ? issues.map((issue) => (
            <div key={issue.id} className="dwp-issue-row">
              <code>{issue.id}</code>
              <p>{issue.question}</p>
              <small>Candidate evidence</small>
              <EvidenceIdRow ids={issue.evidence} />
              <small>Used by Bull/Bear</small>
              <EvidenceIdRow ids={issue.usedEvidence} empty="No structured debate evidence was linked." />
              <div className="dwp-field-row">
                {issue.fields.map((field) => <FieldPill key={field}>{field}</FieldPill>)}
              </div>
              {issue.turns.length > 0 && (
                <details className="dwp-details">
                  <summary>{issue.turns.length} structured debate turn{issue.turns.length === 1 ? '' : 's'}</summary>
                  <div className="dwp-turns">
                    {issue.turns.map((turn) => (
                      <div key={turn.id || `${turn.speaker}-${turn.claim}`} className="dwp-turn">
                        <ToneBadge>{turn.speaker || turn.position || 'Turn'}</ToneBadge>
                        <p>{turn.claim || 'No claim captured.'}</p>
                        <div className="dwp-mini-grid">
                          <CompactValue label="Implication">{turn.implicationField || 'N/A'}: {turn.implicationValue}</CompactValue>
                          <CompactValue label="Rebuttal">{turn.rebuttalTo || 'N/A'}</CompactValue>
                          <CompactValue label="Confidence">{turn.confidence ?? 'N/A'}</CompactValue>
                        </div>
                        <EvidenceIdRow ids={turn.evidenceIds} />
                        {turn.falsificationCondition && <small>Falsifier: {turn.falsificationCondition}</small>}
                      </div>
                    ))}
                  </div>
                </details>
              )}
              {(issue.acceptedClaims.length > 0 || issue.rejectedClaims.length > 0 || issue.unresolvedUncertainties.length > 0) && (
                <details className="dwp-details">
                  <summary>Manager outcomes</summary>
                  <div className="dwp-claim-list">
                    {issue.acceptedClaims.map((claim) => (
                      <div key={claim.claim_id || claim.effect} className="dwp-claim-row">
                        <code>{claim.claim_id || 'claim'}</code>
                        <span>{claim.effect || claim.claim || 'Accepted claim.'}</span>
                      </div>
                    ))}
                    {issue.rejectedClaims.map((claim) => (
                      <div key={claim.claim_id || claim.reason} className="dwp-claim-row is-rejected">
                        <code>{claim.claim_id || 'claim'}</code>
                        <span>{claim.reason || 'Rejected claim.'}</span>
                      </div>
                    ))}
                    {issue.unresolvedUncertainties.map((item) => (
                      <div key={item.uncertainty || item.decision_effect} className="dwp-claim-row">
                        <span>{item.uncertainty || 'Unresolved uncertainty'}</span>
                        <small>{item.decision_effect || 'No decision effect captured.'}</small>
                      </div>
                    ))}
                  </div>
                </details>
              )}
            </div>
          )) : <p className="dwp-muted">No contested issues framed.</p>}
        </div>
      </div>
    </div>
  </section>
);

const ThesisTraderPanel = ({ thesisImpact, traderPlanImpact }) => {
  const plan = traderPlanImpact.plan || {};
  return (
    <section className="dwp-section dwp-section--impact">
      <div className="dwp-section__header">
        <div>
          <h4>Thesis to trader plan</h4>
          <p>Research Manager claims converted into executable Trader fields.</p>
        </div>
      </div>
      <div className="dwp-impact-grid">
        <div className="dwp-impact-card">
          <strong>{thesisImpact.acceptedClaims.length}</strong>
          <span>Accepted claims</span>
          <p>{thesisImpact.winningThesis || 'No winning thesis captured.'}</p>
          <div className="dwp-claim-list">
            {thesisImpact.acceptedClaims.map((claim) => (
              <div key={claim.claim_id || claim.effect} className="dwp-claim-row">
                <code>{claim.claim_id || 'claim'}</code>
                <span>{claim.effect || claim.claim || 'Accepted by Research Manager.'}</span>
                <EvidenceIdRow ids={claim.evidence_ids || []} />
              </div>
            ))}
            {thesisImpact.rejectedClaims.map((claim) => (
              <div key={claim.claim_id || claim.reason} className="dwp-claim-row is-rejected">
                <code>{claim.claim_id || 'claim'}</code>
                <span>{claim.reason || 'Rejected by Research Manager.'}</span>
                <EvidenceIdRow ids={claim.evidence_ids || []} />
              </div>
            ))}
          </div>
          {thesisImpact.unresolvedUncertainties.length > 0 && (
            <div className="dwp-claim-list">
              {thesisImpact.unresolvedUncertainties.map((item) => (
                <div key={item.uncertainty || item.decision_effect} className="dwp-claim-row">
                  <span>{item.uncertainty || 'Unresolved uncertainty'}</span>
                  <small>{item.decision_effect || 'No decision effect captured.'}</small>
                </div>
              ))}
            </div>
          )}
          <div className="dwp-field-row">
            {Object.entries(thesisImpact.constraints).map(([key, value]) => (
              <FieldPill key={key}>{key}: {String(value)}</FieldPill>
            ))}
          </div>
        </div>
        <div className="dwp-impact-card">
          <strong>{plan.action || 'N/A'}</strong>
          <span>Trader plan v1</span>
          <div className="dwp-plan-grid">
            <span>Mode</span><code>{plan.execution_mode || 'missing'}</code>
            <span>Order</span><code>{plan.order_type || 'missing'}</code>
            <span>Size</span><code>{plan.position_size_pct ?? 'N/A'}</code>
            <span>Citations</span><code>{traderPlanImpact.rationaleLinkCount}</code>
          </div>
          <div className="dwp-field-link-list">
            {traderPlanImpact.fieldLinks.length > 0 ? traderPlanImpact.fieldLinks.map((row) => (
              <div key={row.field} className="dwp-field-link-row">
                <span>{row.field}</span>
                <code>{row.value}</code>
                <EvidenceIdRow ids={row.refs} empty="No citations captured." />
              </div>
            )) : <p className="dwp-muted">No field-level rationale links captured.</p>}
          </div>
        </div>
      </div>
    </section>
  );
};

const RiskPatchPanel = ({ riskPatchImpact, decisionDiffImpact }) => (
  <section className="dwp-section dwp-section--impact">
    <div className="dwp-section__header">
      <div>
        <h4>Risk patch enforcement</h4>
        <p>Validated patch trail and final decision diff.</p>
      </div>
    </div>
    <div className="dwp-impact-grid">
      <div className="dwp-impact-card">
        <strong>{riskPatchImpact.valid.length}/{riskPatchImpact.validation.length}</strong>
        <span>Valid patches</span>
        <div className="dwp-patch-list">
          {riskPatchImpact.rows.length > 0 ? riskPatchImpact.rows.map((item) => (
            <div key={item.id || item.reason} className={`dwp-patch-row ${item.valid ? 'is-valid' : 'is-rejected'}`}>
              <div className="dwp-patch-row__header">
                <code>{item.id || 'patch'}</code>
                <ToneBadge tone={item.valid ? 'safe' : 'bear'}>{item.valid ? 'valid' : 'rejected'}</ToneBadge>
              </div>
              <div className="dwp-mini-grid">
                <CompactValue label="Author">{item.author || 'N/A'}</CompactValue>
                <CompactValue label="Target">{item.targetPlanVersion || 'N/A'}</CompactValue>
                <CompactValue label="Field">{item.field || 'N/A'}</CompactValue>
                <CompactValue label="Old">{item.oldValue}</CompactValue>
                <CompactValue label="New">{item.newValue}</CompactValue>
                <CompactValue label="Materiality">{item.materiality || 'N/A'}</CompactValue>
              </div>
              <EvidenceIdRow ids={item.evidenceIds} />
              <span>{item.reason || item.validationReason || item.expectedEffect || 'No patch rationale captured.'}</span>
            </div>
          )) : <p className="dwp-muted">No risk patches captured.</p>}
        </div>
      </div>
      <div className="dwp-impact-card">
        <strong>{decisionDiffImpact.acceptedPatches.length}</strong>
        <span>Accepted in final decision</span>
        <EvidenceIdRow ids={decisionDiffImpact.acceptedPatches} empty="No accepted patches captured." />
        {decisionDiffImpact.rejectedPatches.length > 0 && (
          <div className="dwp-claim-list">
            {decisionDiffImpact.rejectedPatches.map((patch, index) => {
              const label = typeof patch === 'object' ? patch.patch_id || `patch-${index + 1}` : String(patch);
              const reason = typeof patch === 'object' ? patch.reason : '';
              return (
                <div key={`${label}-${index}`} className="dwp-claim-row is-rejected">
                  <code>{label}</code>
                  <span>{reason || 'Rejected by final decision.'}</span>
                </div>
              );
            })}
          </div>
        )}
        {decisionDiffImpact.diff ? (
          <div className="dwp-plan-grid">
            {Object.keys(decisionDiffImpact.diff.to_final_decision || {}).map((field) => (
              <Fragment key={field}>
                <span>{field}</span>
                <code>
                  {String(decisionDiffImpact.diff.from_trader_plan?.[field] ?? 'N/A')} to {String(decisionDiffImpact.diff.to_final_decision?.[field] ?? 'N/A')}
                </code>
              </Fragment>
            ))}
          </div>
        ) : (
          <p>{decisionDiffImpact.noMaterialChangeReason || 'No material decision diff captured.'}</p>
        )}
      </div>
    </div>
  </section>
);

const DebateWorkflowPanel = ({ reports }) => {
  const viewModel = useMemo(() => buildDebateWorkflowViewModel(reports), [reports]);

  if (!viewModel.hasDebate) {
    return (
      <div className="dwp-panel">
        <div className="dwp-empty">
          <p>No debate workflow data is available for this report.</p>
        </div>
      </div>
    );
  }

  return (
    <div className="dwp-panel">
      <div className="dwp-hero">
        <div>
          <h3>Debate workflow</h3>
          <p>Post-run audit of how adversarial agents shaped the final trade decision.</p>
        </div>
        <div className="dwp-metrics">
          <Metric label="Research turns" value={viewModel.summary.researchTurns} />
          <Metric label="Risk turns" value={viewModel.summary.riskTurns} />
          <Metric label="Total turns" value={viewModel.summary.totalTurns} />
        </div>
      </div>

      <ol className="dwp-workflow">
        {viewModel.workflowSteps.map((step, index) => (
          <WorkflowStep key={step.id} step={step} index={index} />
        ))}
      </ol>

      <EvidenceImpactPanel evidenceImpact={viewModel.evidenceImpact} issues={viewModel.issues} />

      <ThesisTraderPanel
        thesisImpact={viewModel.thesisImpact}
        traderPlanImpact={viewModel.traderPlanImpact}
      />

      <ChangePanel
        changePanel={viewModel.changePanel}
        traderProposal={viewModel.traderProposal}
        finalDecision={viewModel.finalDecision}
      />

      <RiskPatchPanel
        riskPatchImpact={viewModel.riskPatchImpact}
        decisionDiffImpact={viewModel.decisionDiffImpact}
      />

      <Arena arena={viewModel.researchArena} />
      <Arena arena={viewModel.riskArena} />
    </div>
  );
};

export default memo(DebateWorkflowPanel);
