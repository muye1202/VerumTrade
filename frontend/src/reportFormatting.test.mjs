import assert from 'node:assert/strict';

import {
  buildCatalystDiagnosticsData,
  formatFinalDecisionReport,
} from './reportFormatting.js';

const report = `## Final Recommendation

Wait for a cleaner entry.

BEGIN_DECISION_JSON {"action":"HOLD","ticker":"INTC","quantity":null,"order_type":"MARKET","time_in_force":"GTC","extended_hours":false,"limit_price":null,"stop_price":null,"trail_percent":null,"trail_price":null,"stop_loss":84.5,"take_profit":115.0,"position_size_pct":null,"time_horizon":"2-3 months","confidence":"MEDIUM","rationale":"Preserving capital to wait for a pullback to ~$90 support level before initiating a long position.","decision_version":"v1","execution_intent":"wait_for_trigger","override_reason":null} END_DECISION_JSON`;

const formatted = formatFinalDecisionReport(report);

assert.equal(formatted.hiddenDecisionJson.action, 'HOLD');
assert.equal(formatted.hiddenDecisionJson.execution_intent, 'wait_for_trigger');
assert.equal(
  formatted.markdown.includes('BEGIN_DECISION_JSON'),
  false,
  'raw decision marker should be hidden from displayed markdown',
);
assert.match(formatted.markdown, /## Conditional Trigger Plan/);
assert.match(formatted.markdown, /\*\*Current action:\*\* HOLD/);
assert.match(formatted.markdown, /\*\*Trigger intent:\*\* Wait for trigger/);
assert.match(formatted.markdown, /\*\*Watch stop:\*\* \$84\.50/);
assert.match(formatted.markdown, /\*\*Target:\*\* \$115\.00/);
assert.match(formatted.markdown, /Preserving capital to wait for a pullback/);

const parseFailureDiagnostics = buildCatalystDiagnosticsData({
  catalyst_parse_telemetry: {
    parse_ok: false,
    failure_stage: 'missing_marker',
    exception: 'BEGIN_CATALYST_EVENT_REPORT_JSON not found',
  },
  catalyst_event_report_structured: {
    fallback_mode: 'parse_failed_clean_bundle',
    recommended_action: 'rerun_full_analysis',
    data_quality_notes: ['fallback report generated from validated bundle'],
    evidence_table: [],
  },
  catalyst_evidence: '',
});

assert.equal(parseFailureDiagnostics.summary.primary_diagnosis, 'Parse failure');
assert.equal(parseFailureDiagnostics.summary.parse_status, 'failed');
assert.equal(parseFailureDiagnostics.summary.parse_failure_stage, 'missing_marker');
assert.equal(parseFailureDiagnostics.summary.fallback_mode, 'parse_failed_clean_bundle');
assert.deepEqual(parseFailureDiagnostics.data_quality_notes, ['fallback report generated from validated bundle']);
assert.equal(parseFailureDiagnostics.raw.catalyst_parse_telemetry.parse_ok, false);

const sparseFetchDiagnostics = buildCatalystDiagnosticsData({
  catalyst_event_bundle: {
    bundle_quality: {
      quality_gate: 'sparse',
      accepted_event_count: 0,
    },
    source_quality: {
      sec: { status: 'missing' },
      news: { status: 'missing' },
    },
  },
  catalyst_event_report_structured: {
    fallback_mode: 'insufficient_data',
    recommended_action: 'rerun_full_analysis',
    data_quality_notes: [],
    evidence_table: [],
  },
});

assert.equal(sparseFetchDiagnostics.summary.primary_diagnosis, 'Fetch/source data sparse');
assert.equal(sparseFetchDiagnostics.summary.bundle_quality_gate, 'sparse');
assert.deepEqual(sparseFetchDiagnostics.summary.missing_sources, ['sec', 'news']);

const lowMaterialityDiagnostics = buildCatalystDiagnosticsData({
  catalyst_parse_telemetry: { parse_ok: true },
  catalyst_event_bundle: {
    bundle_quality: {
      quality_gate: 'passed',
      accepted_event_count: 4,
    },
  },
  catalyst_event_report_structured: {
    fallback_mode: 'valid_low_materiality',
    catalyst_score: 0.08,
    near_term_catalysts: [],
    recent_material_events: [],
    evidence_table: [{ claim: 'Routine industry headline' }],
  },
});

assert.equal(lowMaterialityDiagnostics.summary.primary_diagnosis, 'No decision-useful catalyst');
assert.equal(lowMaterialityDiagnostics.summary.fallback_mode, 'valid_low_materiality');

const healthyDiagnostics = buildCatalystDiagnosticsData({
  catalyst_parse_telemetry: { parse_ok: true },
  catalyst_event_bundle: {
    bundle_quality: {
      quality_gate: 'passed',
      accepted_event_count: 3,
    },
  },
  catalyst_event_report_structured: {
    catalyst_score: 0.71,
    recent_material_events: ['Guidance raise'],
    evidence_table: [{ claim: 'Guidance raise confirmed' }],
  },
});

assert.equal(healthyDiagnostics, null);
assert.equal(buildCatalystDiagnosticsData({ market_report: '## Market' }), null);
