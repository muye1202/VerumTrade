import assert from 'node:assert/strict';

import { formatFinalDecisionReport } from './reportFormatting.js';

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
