const DECISION_JSON_PATTERN = /BEGIN_DECISION_JSON\s*([\s\S]*?)\s*END_DECISION_JSON/;

const LABELS = {
  action: 'Current action',
  ticker: 'Ticker',
  execution_intent: 'Trigger intent',
  plan_mode: 'Plan mode',
  order_type: 'Order type',
  time_in_force: 'Time in force',
  limit_price: 'Limit price',
  stop_price: 'Stop price',
  stop_loss: 'Watch stop',
  take_profit: 'Target',
  position_size_pct: 'Position size',
  time_horizon: 'Time horizon',
  confidence: 'Confidence',
  rationale: 'Rationale',
};

const moneyFields = new Set(['limit_price', 'stop_price', 'stop_loss', 'take_profit', 'trail_price']);

const formatKey = (key) => (
  LABELS[key] || String(key).replace(/_/g, ' ').replace(/\b\w/g, (char) => char.toUpperCase())
);

const formatIntent = (value) => (
  String(value).replace(/_/g, ' ').replace(/^\w/, (char) => char.toUpperCase())
);

const asObject = (value) => (
  value && typeof value === 'object' && !Array.isArray(value) ? value : {}
);

const asArray = (value) => (Array.isArray(value) ? value : []);

const catalystHasDecisionUsefulEvents = (structured) => {
  const score = Number(structured.catalyst_score);
  return (
    Number.isFinite(score) && score >= 0.45
  ) || asArray(structured.near_term_catalysts).length > 0
    || asArray(structured.recent_material_events).length > 0
    || asArray(structured.thesis_supporting_events).length > 0
    || asArray(structured.thesis_breaking_events).length > 0;
};

const collectMissingCatalystSources = (bundle) => (
  Object.entries(asObject(bundle.source_quality))
    .filter(([, source]) => asObject(source).status === 'missing')
    .map(([name]) => name)
);

const getCatalystDiagnosis = ({ telemetry, structured, bundle, evidenceText }) => {
  const bundleQuality = asObject(bundle.bundle_quality);
  const missingSources = collectMissingCatalystSources(bundle);
  const fallbackMode = String(structured.fallback_mode || '');
  const qualityGate = String(bundleQuality.quality_gate || '');
  const acceptedEventCount = Number(bundleQuality.accepted_event_count);
  const evidenceRows = asArray(structured.evidence_table);
  const dataQualityNotes = asArray(structured.data_quality_notes);
  const hasSparseEvidence = evidenceRows.length === 0 && !String(evidenceText || '').trim();
  const parseFailed = telemetry.parse_ok === false || fallbackMode.includes('parse_failed');
  const sparseFetch = (
    qualityGate === 'failed'
    || qualityGate === 'sparse'
    || (!Number.isNaN(acceptedEventCount) && acceptedEventCount === 0)
    || missingSources.length >= 2
    || fallbackMode === 'insufficient_data'
  );
  const noDecisionUsefulCatalyst = (
    fallbackMode === 'valid_low_materiality'
    || (!catalystHasDecisionUsefulEvents(structured) && (hasSparseEvidence || evidenceRows.length > 0))
  );
  const shouldShow = (
    parseFailed
    || sparseFetch
    || noDecisionUsefulCatalyst
    || Boolean(fallbackMode)
    || dataQualityNotes.length > 0
  );

  if (!shouldShow) return null;
  if (parseFailed) return 'Parse failure';
  if (sparseFetch) return 'Fetch/source data sparse';
  if (noDecisionUsefulCatalyst) return 'No decision-useful catalyst';
  return 'Data-quality note';
};

export const buildCatalystDiagnosticsData = (reports = {}) => {
  const hasCatalystFields = [
    'catalyst_report',
    'catalyst_event_bundle',
    'catalyst_event_report_structured',
    'catalyst_parse_telemetry',
    'catalyst_evidence',
  ].some((key) => reports[key] !== undefined && reports[key] !== null && reports[key] !== '');

  if (!hasCatalystFields) return null;

  const telemetry = asObject(reports.catalyst_parse_telemetry);
  const structured = asObject(reports.catalyst_event_report_structured);
  const bundle = asObject(reports.catalyst_event_bundle);
  const evidenceText = reports.catalyst_evidence;
  const diagnosis = getCatalystDiagnosis({ telemetry, structured, bundle, evidenceText });

  if (!diagnosis) return null;

  const bundleQuality = asObject(bundle.bundle_quality);
  const missingSources = collectMissingCatalystSources(bundle);
  const notes = asArray(structured.data_quality_notes);
  const fallbackMode = structured.fallback_mode;
  const evidenceRows = asArray(structured.evidence_table);

  return {
    summary: {
      primary_diagnosis: diagnosis,
      parse_status: telemetry.parse_ok === true ? 'passed' : telemetry.parse_ok === false ? 'failed' : 'not reported',
      parse_failure_stage: telemetry.failure_stage || null,
      parse_exception: telemetry.exception || null,
      fallback_mode: fallbackMode || null,
      recommended_action: structured.recommended_action || null,
      bundle_quality_gate: bundleQuality.quality_gate || null,
      accepted_catalyst_events: bundleQuality.accepted_event_count ?? null,
      missing_sources: missingSources,
      structured_evidence_rows: evidenceRows.length,
      evidence_summary_present: Boolean(String(evidenceText || '').trim()),
    },
    data_quality_notes: notes,
    raw: {
      catalyst_parse_telemetry: Object.keys(telemetry).length ? telemetry : null,
      catalyst_event_bundle_quality: Object.keys(bundleQuality).length || missingSources.length
        ? {
            bundle_quality: bundleQuality,
            source_quality: asObject(bundle.source_quality),
          }
        : null,
      catalyst_event_report_structured: Object.keys(structured).length ? structured : null,
      catalyst_evidence: String(evidenceText || '').trim() || null,
    },
  };
};

const formatMoney = (value) => {
  const numberValue = Number(value);
  if (!Number.isFinite(numberValue)) return String(value);
  return `$${numberValue.toFixed(2)}`;
};

const formatValue = (key, value) => {
  if (value === null || value === undefined || value === '') return null;
  if (moneyFields.has(key)) return formatMoney(value);
  if (key === 'position_size_pct') return `${(Number(value) * 100).toFixed(1)}%`;
  if (key === 'execution_intent' || key === 'plan_mode') return formatIntent(value);
  if (typeof value === 'boolean') return value ? 'Yes' : 'No';
  if (Array.isArray(value)) return value.join(', ');
  if (typeof value === 'object') return JSON.stringify(value);
  return String(value);
};

const lineFor = (decision, key) => {
  const value = formatValue(key, decision[key]);
  return value ? `- **${formatKey(key)}:** ${value}` : null;
};

const compactCondition = (key, value) => {
  if (value === null || value === undefined) return null;
  if (typeof value !== 'object' || Array.isArray(value)) {
    return `- **${formatKey(key)}:** ${formatValue(key, value) ?? String(value)}`;
  }
  const details = Object.entries(value)
    .map(([childKey, childValue]) => `${formatKey(childKey)} ${formatValue(childKey, childValue) ?? childValue}`)
    .join('; ');
  return `- **${formatKey(key)}:** ${details}`;
};

const formatBranch = (branch, index) => {
  const action = branch.action_template || branch.action || {};
  const heading = branch.branch_id
    ? `### Scenario ${index + 1}: ${formatIntent(branch.branch_id)}`
    : `### Scenario ${index + 1}`;
  const conditionLines = Object.entries(branch.conditions || {})
    .map(([key, value]) => compactCondition(key, value))
    .filter(Boolean);
  const eventLines = Array.isArray(branch.event_conditions)
    ? branch.event_conditions.map((event) => {
        const name = formatIntent(event.event_key || 'event');
        const confirmation = event.requires_confirmation ? 'requires confirmation' : 'no confirmation required';
        const expected = event.expected_value !== undefined ? `, expected ${event.expected_value}` : '';
        return `- **${name}:** ${confirmation}${expected}`;
      })
    : [];
  const actionLines = [
    lineFor(action, 'action'),
    lineFor(action, 'order_type'),
    lineFor(action, 'limit_price'),
    lineFor(action, 'stop_price'),
    lineFor(action, 'stop_loss'),
    lineFor(action, 'take_profit'),
    lineFor(action, 'position_size_pct'),
    lineFor(action, 'time_horizon'),
    lineFor(action, 'confidence'),
    lineFor(action, 'rationale'),
  ].filter(Boolean);

  return [
    heading,
    branch.priority ? `- **Priority:** ${branch.priority}` : null,
    conditionLines.length ? `- **Trigger conditions:**\n${conditionLines.map((line) => `  ${line}`).join('\n')}` : null,
    eventLines.length ? `- **Event confirmations:**\n${eventLines.map((line) => `  ${line}`).join('\n')}` : null,
    actionLines.length ? `- **Planned action:**\n${actionLines.map((line) => `  ${line}`).join('\n')}` : null,
  ].filter(Boolean).join('\n');
};

const formatDecisionMarkdown = (decision) => {
  const baseLines = [
    lineFor(decision, 'action'),
    lineFor(decision, 'ticker'),
    lineFor(decision, 'execution_intent'),
    lineFor(decision, 'plan_mode'),
    lineFor(decision, 'order_type'),
    lineFor(decision, 'time_in_force'),
    lineFor(decision, 'limit_price'),
    lineFor(decision, 'stop_price'),
    lineFor(decision, 'stop_loss'),
    lineFor(decision, 'take_profit'),
    lineFor(decision, 'position_size_pct'),
    lineFor(decision, 'time_horizon'),
    lineFor(decision, 'confidence'),
    lineFor(decision, 'rationale'),
  ].filter(Boolean);

  const branchLines = Array.isArray(decision.execution_plan)
    ? decision.execution_plan.map(formatBranch)
    : [];

  return [
    '## Conditional Trigger Plan',
    baseLines.join('\n'),
    branchLines.join('\n\n'),
  ].filter(Boolean).join('\n\n');
};

export const formatFinalDecisionReport = (content) => {
  if (typeof content !== 'string') {
    return { markdown: content || '', hiddenDecisionJson: null };
  }

  const match = content.match(DECISION_JSON_PATTERN);
  if (!match) {
    return { markdown: content, hiddenDecisionJson: null };
  }

  let hiddenDecisionJson = null;
  let readablePlan;
  try {
    hiddenDecisionJson = JSON.parse(match[1].trim());
    readablePlan = formatDecisionMarkdown(hiddenDecisionJson);
  } catch {
    readablePlan = '## Conditional Trigger Plan\n\nThe structured decision plan is available for monitor wiring, but it could not be parsed for display.';
  }

  const markdown = [
    content.replace(DECISION_JSON_PATTERN, '').trim(),
    readablePlan,
  ].filter(Boolean).join('\n\n');

  return { markdown, hiddenDecisionJson };
};
