import assert from 'node:assert/strict';

import {
  buildDebateWorkflowViewModel,
  splitDebateHistory,
} from './debateWorkflowViewModel.js';

const reports = {
  investment_debate_state: {
    count: 2,
    history: [
      'Bull Analyst: Revenue acceleration and margin expansion support a tactical BUY.',
      'Bear Analyst: Valuation and post-earnings reversal risk argue for patience.',
    ].join('\n'),
    bull_history: 'Bull Analyst: Revenue acceleration and margin expansion support a tactical BUY.',
    bear_history: 'Bear Analyst: Valuation and post-earnings reversal risk argue for patience.',
    judge_decision: 'Research Manager: BUY, but only with sizing discipline and catalyst confirmation.',
  },
  evidence_ledger: [
    {
      evidence_id: 'E-MKT-001',
      claim: 'RSI is elevated and argues against full-size entry.',
      source_agent: 'market_analyst',
      source_tool: 'get_indicators',
      observed_at: '2026-06-03T14:30:00Z',
      polarity: 'bearish',
      confidence: 0.82,
      materiality: 0.7,
      criticality: 0.57,
      supports: ['prefer_wait_for_pullback'],
    },
  ],
  admissibility_report: {
    accepted_evidence_ids: ['E-MKT-001'],
    downgraded_evidence: [],
    rejected_evidence: [],
  },
  critical_evidence_ids: ['E-MKT-001'],
  contested_issues: [
    {
      issue_id: 'I-001',
      question: 'Act now or wait?',
      candidate_evidence: ['E-MKT-001'],
      decision_fields_at_risk: ['execution_mode', 'position_size_pct'],
    },
  ],
  research_debate_turns: [
    {
      turn_id: 'BEAR-001',
      speaker: 'Bear Analyst',
      issue_id: 'I-001',
      claim: 'Short-term risk argues for waiting.',
      evidence_ids: ['E-MKT-001'],
      plan_implication: { field: 'execution_mode', proposed_value: 'wait_for_trigger' },
      confidence: 0.72,
    },
  ],
  thesis_ledger: {
    winning_thesis: 'Cautious long setup after confirmation.',
    accepted_claims: [{ claim_id: 'C-001', evidence_ids: ['E-MKT-001'], effect: 'execution_mode=wait_for_trigger' }],
    rejected_claims: [],
    unresolved_uncertainties: [],
    recommended_plan_constraints: { max_size_pct: 0.04 },
  },
  trader_plan_v1: {
    plan_id: 'trader_plan_v1',
    action: 'BUY',
    execution_mode: 'act_now',
    order_type: 'LIMIT',
    position_size_pct: 0.1,
    entry_condition: 'Enter now.',
    stop_loss: 84.5,
    take_profit: 115,
    rationale_links: {
      action: ['E-MKT-001'],
      execution_mode: ['E-MKT-001'],
      position_size_pct: ['E-MKT-001'],
    },
  },
  trader_plan_validation: { valid: true, violations: [] },
  trader_investment_plan: [
    'FINAL TRANSACTION PROPOSAL',
    'ACTION: BUY',
    'POSITION_SIZE_PCT: 12',
    'EXECUTION_INTENT: ACT_NOW',
    'STOP_LOSS: 84.5',
  ].join('\n'),
  risk_debate_state: {
    count: 3,
    history: [
      'Risky Analyst: Act now before momentum reprices the upside.',
      'Safe Analyst: Reduce sizing because support is still unconfirmed.',
      'Neutral Analyst: Wait for confirmation and keep upside optionality.',
    ].join('\n'),
    risky_history: 'Risky Analyst: Act now before momentum reprices the upside.',
    safe_history: 'Safe Analyst: Reduce sizing because support is still unconfirmed.',
    neutral_history: 'Neutral Analyst: Wait for confirmation and keep upside optionality.',
    judge_decision: 'Risk Manager: HOLD until trigger, reduce risk, and use a tighter stop.',
  },
  final_trade_decision: [
    'Final decision: preserve capital.',
    'BEGIN_DECISION_JSON {"action":"HOLD","ticker":"INTC","position_size_pct":null,"stop_loss":84.5,"take_profit":115,"execution_intent":"wait_for_trigger","rationale":"Risk debate shifted the proposal from immediate action to conditional confirmation."} END_DECISION_JSON',
  ].join('\n'),
  risk_patches: [
    {
      patch_id: 'P-SAFE-001',
      author: 'safe_analyst',
      target_plan_version: 'trader_plan_v1',
      patch_type: 'modify',
      field: 'position_size_pct',
      old_value: 0.1,
      new_value: 0.04,
      evidence_ids: ['E-MKT-001'],
      reason: 'Reduce starter size until confirmation.',
      expected_effect: 'reduce_drawdown_risk',
      materiality: 'high',
    },
  ],
  risk_patch_validation: [
    {
      patch_id: 'P-SAFE-001',
      valid: true,
      reason: '',
      patch: { field: 'position_size_pct', new_value: 0.04 },
    },
  ],
  decision_diff: {
    decision_diff: {
      from_trader_plan: { position_size_pct: 0.1, execution_mode: 'act_now' },
      to_final_decision: { position_size_pct: 0.04, execution_mode: 'wait_for_trigger' },
    },
    accepted_patches: ['P-SAFE-001'],
    rejected_patches: [],
    no_material_change_reason: null,
  },
};

const turns = splitDebateHistory(reports.risk_debate_state.history);
assert.equal(turns.length, 3);
assert.equal(turns[0].speaker, 'Risky Analyst');
assert.match(turns[1].content, /Reduce sizing/);

const viewModel = buildDebateWorkflowViewModel(reports);

assert.equal(viewModel.hasDebate, true);
assert.equal(viewModel.workflowSteps.length, 9);
assert.equal(viewModel.evidenceImpact.acceptedCount, 1);
assert.equal(viewModel.evidenceImpact.topEvidence[0].id, 'E-MKT-001');
assert.equal(viewModel.evidenceImpact.topEvidence[0].timestamp, '2026-06-03T14:30:00Z');
assert.deepEqual(viewModel.evidenceImpact.topEvidence[0].supports, ['prefer_wait_for_pullback']);
assert.equal(viewModel.issues[0].fields.includes('position_size_pct'), true);
assert.equal(viewModel.issues[0].turns[0].implicationField, 'execution_mode');
assert.deepEqual(viewModel.issues[0].usedEvidence, ['E-MKT-001']);
assert.equal(viewModel.thesisImpact.acceptedClaims[0].claim_id, 'C-001');
assert.equal(viewModel.traderPlanImpact.rationaleLinkCount, 3);
assert.equal(viewModel.traderPlanImpact.fieldLinks.find((row) => row.field === 'position_size_pct').refs[0], 'E-MKT-001');
assert.equal(viewModel.riskPatchImpact.valid[0].patch_id, 'P-SAFE-001');
assert.equal(viewModel.riskPatchImpact.rows[0].field, 'position_size_pct');
assert.equal(viewModel.riskPatchImpact.rows[0].oldValue, '0.1');
assert.equal(viewModel.riskPatchImpact.rows[0].newValue, '0.04');
assert.equal(viewModel.decisionDiffImpact.acceptedPatches[0], 'P-SAFE-001');
assert.equal(viewModel.researchArena.participants.length, 2);
assert.equal(viewModel.researchArena.judge.role, 'Research Manager');
assert.equal(viewModel.riskArena.participants.length, 3);
assert.equal(viewModel.riskArena.judge.role, 'Risk Manager');
assert.equal(viewModel.summary.researchTurns, 2);
assert.equal(viewModel.summary.riskTurns, 3);

assert.equal(viewModel.traderProposal.action, 'BUY');
assert.equal(viewModel.traderProposal.executionIntent, 'ACT_NOW');
assert.equal(viewModel.finalDecision.action, 'HOLD');
assert.equal(viewModel.finalDecision.executionIntent, 'WAIT_FOR_TRIGGER');

assert.equal(viewModel.changePanel.items.length >= 2, true);
assert.deepEqual(
  viewModel.changePanel.items.map((item) => item.field),
  ['Action', 'Execution intent'],
);

const noisyHoldReports = {
  investment_debate_state: {
    history: 'Bull Analyst: Hold is acceptable until confirmation.\nBear Analyst: Hold avoids chasing.',
    judge_decision: 'Research Manager: HOLD now while watching the trigger.',
  },
  trader_investment_plan: [
    'FINAL TRANSACTION PROPOSAL',
    'WAIT_FOR_TRIGGER and ACTION = HOLD now.',
    'POSITION_SIZE_PCT: N/A',
    'STOP_LOSS: 196.03',
  ].join('\n'),
  risk_debate_state: {
    history: 'Safe Analyst: Keep the hold until the trigger is confirmed.',
    safe_history: 'Safe Analyst: Keep the hold until the trigger is confirmed.',
    judge_decision: 'Risk Manager: HOLD remains appropriate; wait for trigger confirmation.',
  },
  final_trade_decision: [
    'BEGIN_DECISION_JSON {"action":"HOLD","execution_intent":"wait_for_trigger","position_size_pct":null,"rationale":"Hold remains appropriate until the trigger confirms."} END_DECISION_JSON',
  ].join('\n'),
};

const noisyHoldModel = buildDebateWorkflowViewModel(noisyHoldReports);

assert.equal(noisyHoldModel.traderProposal.action, 'HOLD');
assert.equal(noisyHoldModel.traderProposal.executionIntent, 'WAIT_FOR_TRIGGER');
assert.equal(noisyHoldModel.changePanel.items.length, 0);
assert.equal(noisyHoldModel.changePanel.unchanged, true);

const realDbShapeReports = {
  contested_issues: [
    {
      issue_id: 'I-001',
      question: 'Does the admissible evidence support a long bias, or should action remain HOLD?',
      candidate_evidence: ['E-SEN-005', 'E-MKT-002'],
      decision_fields_at_risk: ['action', 'execution_mode'],
    },
  ],
  research_debate_turns: [
    {
      turn_id: 8,
      speaker: 'bull_analyst',
      issue_id: 'I-001',
      position: 'act_now_scale_in',
      claim: 'Initiate a disciplined, staggered long entry.',
      evidence_ids: ['E-SEN-005', 'E-MKT-002'],
      rebuttal_to: 'bear_analyst',
      plan_implication: { field: 'execution_mode', proposed_value: 'act_now_scale_in' },
    },
  ],
  thesis_ledger: {
    accepted_claims: [],
    rejected_claims: [],
    unresolved_uncertainties: [
      'Primary 8-K / 10-Q / Form-4 filings remain unverified and materially relevant.',
    ],
  },
  investment_debate_state: {
    history: 'Bull Analyst: Real DB history still uses canonical labels.',
  },
};

const realDbShapeModel = buildDebateWorkflowViewModel(realDbShapeReports);

assert.equal(realDbShapeModel.issues[0].turns[0].speaker, 'Bull Analyst');
assert.equal(realDbShapeModel.issues[0].turns[0].rebuttalTo, 'Bear Analyst');
assert.equal(
  realDbShapeModel.issues[0].unresolvedUncertainties[0].uncertainty,
  'Primary 8-K / 10-Q / Form-4 filings remain unverified and materially relevant.',
);
assert.equal(
  realDbShapeModel.thesisImpact.unresolvedUncertainties[0].uncertainty,
  'Primary 8-K / 10-Q / Form-4 filings remain unverified and materially relevant.',
);
