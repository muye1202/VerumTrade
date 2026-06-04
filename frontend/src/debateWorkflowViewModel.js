const SPEAKER_PATTERN = /^(Bull Analyst|Bear Analyst|Risky Analyst|Safe Analyst|Neutral Analyst|Research Manager|Risk Manager|Judge):\s*/i;

const ROLE_META = {
  'Bull Analyst': { stance: 'Bull thesis', tone: 'bull' },
  'Bear Analyst': { stance: 'Bear thesis', tone: 'bear' },
  'Risky Analyst': { stance: 'Aggressive execution', tone: 'risky' },
  'Safe Analyst': { stance: 'Capital preservation', tone: 'safe' },
  'Neutral Analyst': { stance: 'Balanced risk-reward', tone: 'neutral' },
  'Research Manager': { stance: 'Research judge', tone: 'judge' },
  'Risk Manager': { stance: 'Risk judge', tone: 'judge' },
};

const asObject = (value) => (value && typeof value === 'object' ? value : {});
const asArray = (value) => (Array.isArray(value) ? value : []);

const compactText = (value) => String(value || '').replace(/\s+/g, ' ').trim();

const firstSentence = (value, limit = 180) => {
  const text = compactText(value);
  if (!text) return '';
  const sentenceEnd = text.search(/[.!?](\s|$)/);
  const candidate = sentenceEnd > 40 ? text.slice(0, sentenceEnd + 1) : text;
  return candidate.length > limit ? `${candidate.slice(0, limit).trim()}...` : candidate;
};

const stripSpeaker = (text, fallbackSpeaker = '') => {
  const value = compactText(text);
  const match = value.match(SPEAKER_PATTERN);
  if (!match) return { speaker: fallbackSpeaker, content: value };
  return {
    speaker: normalizeSpeaker(match[1]),
    content: value.slice(match[0].length).trim(),
  };
};

const normalizeSpeaker = (speaker) => {
  const value = compactText(speaker).toLowerCase();
  if (value === 'judge') return 'Risk Manager';
  return Object.keys(ROLE_META).find((role) => role.toLowerCase() === value) || speaker;
};

export const splitDebateHistory = (history) => {
  const text = String(history || '').trim();
  if (!text) return [];

  const turns = [];
  let current = null;

  text.split(/\r?\n/).forEach((line) => {
    const trimmed = line.trim();
    if (!trimmed) return;
    const match = trimmed.match(SPEAKER_PATTERN);
    if (match) {
      if (current) turns.push(current);
      current = {
        speaker: normalizeSpeaker(match[1]),
        content: trimmed.slice(match[0].length).trim(),
      };
      return;
    }
    if (current) {
      current.content = `${current.content}\n${trimmed}`.trim();
    } else {
      current = { speaker: 'Agent', content: trimmed };
    }
  });

  if (current) turns.push(current);
  return turns.filter((turn) => turn.content);
};

const getLatestFromHistory = (history, speaker) => {
  const turns = splitDebateHistory(history).filter((turn) => turn.speaker === speaker);
  return turns.at(-1)?.content || '';
};

const participant = (role, history, currentValue = '') => {
  const meta = ROLE_META[role] || { stance: 'Debater', tone: 'neutral' };
  const latest = stripSpeaker(currentValue || getLatestFromHistory(history, role), role);
  const turns = splitDebateHistory(history).filter((turn) => turn.speaker === role);
  return {
    role,
    stance: meta.stance,
    tone: meta.tone,
    turnCount: turns.length,
    claim: firstSentence(latest.content || turns.at(-1)?.content),
    content: latest.content || turns.at(-1)?.content || '',
  };
};

const extractDecisionJson = (value) => {
  if (value && typeof value === 'object') return value;
  const text = String(value || '');
  const markerMatch = text.match(/BEGIN_DECISION_JSON\s*({[\s\S]*?})\s*END_DECISION_JSON/i);
  if (!markerMatch) return null;
  try {
    return JSON.parse(markerMatch[1]);
  } catch {
    return null;
  }
};

const extractPlanField = (text, names) => {
  const source = String(text || '');
  for (const name of names) {
    const pattern = new RegExp(`${name.replace(/_/g, '[_\\\\s-]*')}\\s*[:=]\\s*([^\\n\\r]+)`, 'i');
    const match = source.match(pattern);
    if (match) return match[1].replace(/[`"*]/g, '').trim();
  }
  return '';
};

const normalizeToken = (value) => String(value ?? '').trim().replace(/[.。]+$/g, '').trim();

const normalizeAction = (value) => {
  const text = normalizeToken(value).toUpperCase();
  const match = text.match(/\b(BUY|SELL|HOLD)\b/);
  return match ? match[1] : '';
};

const normalizeExecutionIntent = (value) => {
  const text = normalizeToken(value).toUpperCase().replace(/[\s-]+/g, '_');
  if (text.includes('WAIT_FOR_TRIGGER')) return 'WAIT_FOR_TRIGGER';
  if (text.includes('ACT_NOW')) return 'ACT_NOW';
  return '';
};

const normalizeNullable = (value) => {
  const text = normalizeToken(value);
  if (!text) return '';
  if (/^(N\/A|NA|NONE|NULL|UNSPECIFIED|UNDEFINED)$/i.test(text)) return '';
  return text;
};

const normalizeComparable = (value) => String(value ?? '').trim().toUpperCase();

const buildTraderProposal = (text) => {
  const rawAction = extractPlanField(text, ['ACTION', 'FINAL_ACTION']);
  const rawExecutionIntent = extractPlanField(text, ['EXECUTION_INTENT', 'INTENT']) || String(text || '');
  return {
    action: normalizeAction(rawAction) || 'Unspecified',
    executionIntent: normalizeExecutionIntent(rawExecutionIntent) || 'Unspecified',
    positionSize: normalizeNullable(extractPlanField(text, ['POSITION_SIZE_PCT', 'POSITION_SIZE', 'SIZE'])) || 'Unspecified',
    stopLoss: normalizeNullable(extractPlanField(text, ['STOP_LOSS', 'STOP'])) || 'Unspecified',
    raw: String(text || ''),
  };
};

const buildFinalDecision = (text, structuredDecision) => {
  const parsed = extractDecisionJson(structuredDecision) || extractDecisionJson(text) || {};
  const action = normalizeAction(parsed.action || extractPlanField(text, ['ACTION']));
  const executionIntent = normalizeExecutionIntent(parsed.execution_intent || parsed.executionIntent || extractPlanField(text, ['EXECUTION_INTENT']));
  return {
    action: action || 'Unspecified',
    executionIntent: executionIntent || 'Unspecified',
    positionSize: normalizeNullable(parsed.position_size_pct ?? parsed.positionSize) || 'Unspecified',
    stopLoss: normalizeNullable(parsed.stop_loss ?? parsed.stopLoss) || 'Unspecified',
    takeProfit: normalizeNullable(parsed.take_profit ?? parsed.takeProfit) || 'Unspecified',
    rationale: parsed.rationale || firstSentence(text, 260),
    raw: String(text || ''),
  };
};

const changeItem = (field, before, after, impact) => ({
  field,
  before: before === null || before === undefined || before === '' ? 'Unspecified' : String(before),
  after: after === null || after === undefined || after === '' ? 'Unspecified' : String(after),
  impact,
});

const buildChangePanel = (traderProposal, finalDecision) => {
  const candidates = [
    changeItem('Action', traderProposal.action, finalDecision.action, 'Final trade direction changed after risk review.'),
    changeItem('Execution intent', traderProposal.executionIntent, finalDecision.executionIntent, 'Risk debate changed when or how the trade should execute.'),
    changeItem('Position size', traderProposal.positionSize, finalDecision.positionSize, 'Risk review adjusted capital exposure.'),
    changeItem('Stop loss', traderProposal.stopLoss, finalDecision.stopLoss, 'Risk review adjusted downside control.'),
  ];
  const items = candidates.filter((item) => (
    normalizeNullable(item.before)
    && normalizeNullable(item.after)
    && normalizeComparable(item.before) !== normalizeComparable(item.after)
  ));
  return {
    items,
    unchanged: items.length === 0,
  };
};

const buildWorkflowSteps = (reports, summary) => [
  { id: 'analysts', label: 'Analyst reports', status: reports.market_report || reports.news_report || reports.fundamentals_report ? 'available' : 'missing', takeaway: 'Evidence inputs assembled.' },
  { id: 'evidence_ledger', label: 'Evidence ledger', status: summary.evidenceCount > 0 ? 'available' : 'missing', takeaway: `${summary.acceptedEvidenceCount}/${summary.evidenceCount} admissible evidence item${summary.evidenceCount === 1 ? '' : 's'}.` },
  { id: 'contested_issues', label: 'Contested issues', status: summary.issueCount > 0 ? 'available' : 'missing', takeaway: `${summary.issueCount} issue${summary.issueCount === 1 ? '' : 's'} framed around decision fields.` },
  { id: 'research_debate', label: 'Bull/Bear debate', status: summary.researchTurns > 0 ? 'available' : 'missing', takeaway: `${summary.researchTurns} research turn${summary.researchTurns === 1 ? '' : 's'}.` },
  { id: 'research_manager', label: 'Thesis ledger', status: reports.thesis_ledger?.winning_thesis || reports.investment_debate_state?.judge_decision ? 'available' : 'missing', takeaway: reports.thesis_ledger?.winning_thesis || firstSentence(reports.investment_debate_state?.judge_decision) || 'No thesis ledger captured.' },
  { id: 'trader', label: 'Trader plan v1', status: reports.trader_plan_v1 || reports.trader_investment_plan ? 'available' : 'missing', takeaway: reports.trader_plan_v1?.execution_mode ? `Trader proposed ${reports.trader_plan_v1.execution_mode}.` : 'Research translated into an executable proposal.' },
  { id: 'risk_debate', label: 'Risk patches', status: summary.riskPatchCount > 0 || summary.riskTurns > 0 ? 'available' : 'missing', takeaway: `${summary.validRiskPatchCount}/${summary.riskPatchCount} valid patch${summary.riskPatchCount === 1 ? '' : 'es'}.` },
  { id: 'risk_manager', label: 'Risk manager', status: reports.risk_debate_state?.judge_decision ? 'available' : 'missing', takeaway: firstSentence(reports.risk_debate_state?.judge_decision) || 'No risk judge decision captured.' },
  { id: 'final', label: 'Final decision', status: reports.final_trade_decision ? 'available' : 'missing', takeaway: reports.decision_diff?.decision_diff ? 'Final decision changed executable fields.' : 'Canonical trade parameters finalized.' },
];

const buildEvidenceImpact = (reports) => {
  const ledger = asArray(reports.evidence_ledger);
  const admissibility = asObject(reports.admissibility_report);
  const accepted = new Set(asArray(admissibility.accepted_evidence_ids).map(String));
  return {
    ledger,
    admissibility,
    acceptedCount: accepted.size,
    criticalIds: asArray(reports.critical_evidence_ids).map(String),
    topEvidence: ledger
      .slice()
      .sort((a, b) => Number(b?.criticality || 0) - Number(a?.criticality || 0))
      .slice(0, 5)
      .map((item) => ({
        id: String(item?.evidence_id || ''),
        claim: String(item?.claim || ''),
        source: [item?.source_agent, item?.source_tool].filter(Boolean).join(' / '),
        status: accepted.has(String(item?.evidence_id || '')) ? 'accepted' : 'review',
        polarity: String(item?.polarity || 'neutral'),
        materiality: item?.materiality,
        confidence: item?.confidence,
        criticality: item?.criticality,
      })),
  };
};

const buildIssueImpact = (reports) => asArray(reports.contested_issues).map((issue) => ({
  id: String(issue?.issue_id || ''),
  question: String(issue?.question || ''),
  evidence: asArray(issue?.candidate_evidence).map(String),
  fields: asArray(issue?.decision_fields_at_risk).map(String),
}));

const buildThesisImpact = (reports) => {
  const thesis = asObject(reports.thesis_ledger);
  return {
    winningThesis: String(thesis.winning_thesis || ''),
    acceptedClaims: asArray(thesis.accepted_claims),
    rejectedClaims: asArray(thesis.rejected_claims),
    unresolvedUncertainties: asArray(thesis.unresolved_uncertainties),
    constraints: asObject(thesis.recommended_plan_constraints),
    validation: asObject(reports.thesis_ledger_validation),
  };
};

const buildTraderPlanImpact = (reports) => {
  const plan = asObject(reports.trader_plan_v1);
  return {
    plan,
    validation: asObject(reports.trader_plan_validation),
    rationaleLinkCount: Object.values(asObject(plan.rationale_links)).reduce(
      (count, refs) => count + asArray(refs).length,
      0,
    ),
  };
};

const buildRiskPatchImpact = (reports) => {
  const validation = asArray(reports.risk_patch_validation);
  const patches = asArray(reports.risk_patches);
  const valid = validation.filter((item) => item?.valid);
  return {
    patches,
    validation,
    valid,
    rejected: validation.filter((item) => item && !item.valid),
  };
};

const buildDecisionDiffImpact = (reports) => {
  const diffTrace = asObject(reports.decision_diff);
  const finalTrace = extractDecisionJson(reports.final_trade_decision_structured)
    || extractDecisionJson(reports.final_trade_decision)
    || asObject(reports.final_trade_decision_structured);
  return {
    trace: diffTrace,
    diff: diffTrace.decision_diff || null,
    acceptedPatches: asArray(diffTrace.accepted_patches?.length ? diffTrace.accepted_patches : finalTrace.accepted_patches).map(String),
    rejectedPatches: asArray(diffTrace.rejected_patches?.length ? diffTrace.rejected_patches : finalTrace.rejected_patches),
    noMaterialChangeReason: diffTrace.no_material_change_reason || finalTrace.no_material_change_reason || '',
    rationaleEvidenceIds: asArray(finalTrace.rationale_evidence_ids).map(String),
  };
};

export const buildDebateWorkflowViewModel = (reportsValue = {}) => {
  const reports = asObject(reportsValue);
  const investmentDebate = asObject(reports.investment_debate_state);
  const riskDebate = asObject(reports.risk_debate_state);
  const researchTurns = splitDebateHistory(investmentDebate.history);
  const riskTurns = splitDebateHistory(riskDebate.history);
  const traderProposal = buildTraderProposal(reports.trader_investment_plan);
  const finalDecision = buildFinalDecision(reports.final_trade_decision, reports.final_trade_decision_structured);
  const summary = {
    researchTurns: researchTurns.length || Number(investmentDebate.count || 0),
    riskTurns: riskTurns.length || Number(riskDebate.count || 0),
    totalTurns: (researchTurns.length || Number(investmentDebate.count || 0)) + (riskTurns.length || Number(riskDebate.count || 0)),
    evidenceCount: asArray(reports.evidence_ledger).length,
    acceptedEvidenceCount: asArray(reports.admissibility_report?.accepted_evidence_ids).length,
    issueCount: asArray(reports.contested_issues).length,
    researchDebateTurnCount: asArray(reports.research_debate_turns).length,
    riskPatchCount: asArray(reports.risk_patches).length,
    validRiskPatchCount: asArray(reports.risk_patch_validation).filter((item) => item?.valid).length,
  };

  return {
    hasDebate: Boolean(
      investmentDebate.history
      || riskDebate.history
      || investmentDebate.judge_decision
      || riskDebate.judge_decision
      || reports.evidence_ledger
      || reports.trader_plan_v1
      || reports.decision_diff
    ),
    workflowSteps: buildWorkflowSteps(reports, summary),
    summary,
    evidenceImpact: buildEvidenceImpact(reports),
    issues: buildIssueImpact(reports),
    thesisImpact: buildThesisImpact(reports),
    traderPlanImpact: buildTraderPlanImpact(reports),
    riskPatchImpact: buildRiskPatchImpact(reports),
    decisionDiffImpact: buildDecisionDiffImpact(reports),
    researchArena: {
      title: 'Research debate',
      participants: [
        participant('Bull Analyst', investmentDebate.bull_history || investmentDebate.history),
        participant('Bear Analyst', investmentDebate.bear_history || investmentDebate.history),
      ],
      turns: researchTurns,
      judge: {
        role: 'Research Manager',
        stance: ROLE_META['Research Manager'].stance,
        tone: ROLE_META['Research Manager'].tone,
        content: investmentDebate.judge_decision || investmentDebate.current_response || '',
        claim: firstSentence(investmentDebate.judge_decision || investmentDebate.current_response),
      },
    },
    traderProposal,
    riskArena: {
      title: 'Risk debate',
      participants: [
        participant('Risky Analyst', riskDebate.risky_history || riskDebate.history, riskDebate.current_risky_response),
        participant('Safe Analyst', riskDebate.safe_history || riskDebate.history, riskDebate.current_safe_response),
        participant('Neutral Analyst', riskDebate.neutral_history || riskDebate.history, riskDebate.current_neutral_response),
      ],
      turns: riskTurns,
      judge: {
        role: 'Risk Manager',
        stance: ROLE_META['Risk Manager'].stance,
        tone: ROLE_META['Risk Manager'].tone,
        content: riskDebate.judge_decision || '',
        claim: firstSentence(riskDebate.judge_decision),
      },
    },
    finalDecision,
    changePanel: buildChangePanel(traderProposal, finalDecision),
  };
};
