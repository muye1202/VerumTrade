import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

import {
  countAuditIssuesBySeverity,
  getDecisionTraceViewModel,
} from './DecisionTracePanel.helpers.js';

const trace = {
  decision: {
    action: 'BUY',
    ticker: 'MU',
    summary: 'Buy because the thesis is supported by price action and earnings revisions.',
  },
  thesis: {
    claim: 'Momentum and earnings revisions support a long setup.',
    inference_ids: ['inf_market_001', 'inf_news_missing'],
  },
  inference_ids: ['inf_market_001', 'inf_news_missing'],
  fact_ids: ['fact_price_001', 'fact_missing'],
  source_labels: ['price_action_summary', 'earnings_call'],
  audit_issues: [
    { code: 'unsupported_hypothesis_evidence', severity: 'high', message: 'Missing support link.' },
    { code: 'trace_missing_fact_links', severity: 'medium', message: 'Partial fact coverage.' },
  ],
};

const evidenceGraph = {
  inferences: [
    {
      id: 'inf_market_001',
      domain: 'market',
      analyst: 'market',
      stance: 'bullish',
      confidence: 0.82,
      claim: 'Price trend confirms the breakout.',
      support_fact_ids: ['fact_price_001'],
      counter_fact_ids: ['fact_counter_001'],
    },
  ],
  facts: [
    {
      id: 'fact_price_001',
      domain: 'market',
      source: 'price_action_summary',
      claim: 'Price closed above the 20-day moving average.',
      confidence: 0.9,
      source_type: 'vendor',
    },
    {
      id: 'fact_counter_001',
      domain: 'market',
      source: 'price_action_summary',
      claim: 'Volume confirmation is not yet decisive.',
      confidence: 0.55,
      source_type: 'vendor',
    },
  ],
};

const viewModel = getDecisionTraceViewModel(trace, evidenceGraph);

assert.equal(viewModel.decision.action, 'BUY');
assert.equal(viewModel.decision.ticker, 'MU');
assert.equal(viewModel.inferenceRows.length, 2);
assert.equal(viewModel.inferenceRows[0].id, 'inf_market_001');
assert.equal(viewModel.inferenceRows[0].inference.claim, 'Price trend confirms the breakout.');
assert.equal(viewModel.inferenceRows[0].missing, false);
assert.equal(viewModel.inferenceRows[0].supportFacts[0].id, 'fact_price_001');
assert.equal(viewModel.inferenceRows[0].counterFacts[0].id, 'fact_counter_001');
assert.equal(viewModel.inferenceRows[1].id, 'inf_news_missing');
assert.equal(viewModel.inferenceRows[1].missing, true);

assert.equal(viewModel.factRows.length, 2);
assert.equal(viewModel.factRows[0].fact.claim, 'Price closed above the 20-day moving average.');
assert.equal(viewModel.factRows[0].missing, false);
assert.equal(viewModel.factRows[1].id, 'fact_missing');
assert.equal(viewModel.factRows[1].missing, true);

assert.deepEqual(viewModel.sourceLabels, ['price_action_summary', 'earnings_call']);
assert.equal(viewModel.summary.linkedInferenceCount, 1);
assert.equal(viewModel.summary.missingInferenceCount, 1);
assert.equal(viewModel.summary.linkedFactCount, 1);
assert.equal(viewModel.summary.missingFactCount, 1);
assert.equal(viewModel.summary.sourceCount, 2);
assert.equal(viewModel.summary.highSeverityAuditCount, 1);

assert.deepEqual(countAuditIssuesBySeverity(trace.audit_issues), {
  high: 1,
  medium: 1,
  low: 0,
});

const emptyViewModel = getDecisionTraceViewModel({}, null);
assert.equal(emptyViewModel.decision.action, '');
assert.equal(emptyViewModel.inferenceRows.length, 0);
assert.equal(emptyViewModel.factRows.length, 0);
assert.equal(emptyViewModel.summary.highSeverityAuditCount, 0);

const here = dirname(fileURLToPath(import.meta.url));
const panelSource = readFileSync(join(here, 'DecisionTracePanel.jsx'), 'utf8');
const stylesSource = readFileSync(join(here, 'index.css'), 'utf8');

assert.match(panelSource, /import ReactMarkdown from 'react-markdown';/);
assert.match(panelSource, /import remarkGfm from 'remark-gfm';/);
assert.match(
  panelSource,
  /<ReactMarkdown\s+remarkPlugins=\{\[remarkGfm\]\}>\s*\{truncate\(decision\.summary\)/s,
  'decision summary markdown should be rendered, not displayed as literal markdown text',
);
assert.match(
  stylesSource,
  /\.dtp-decision-summary h1,\s*\.dtp-decision-summary h2,\s*\.dtp-decision-summary h3,\s*\.dtp-decision-summary h4\s*\{\s*margin: 8px 0 6px;/s,
  'decision summary headings should use compact report-card spacing',
);
assert.match(
  stylesSource,
  /\.dtp-decision-summary ul,\s*\.dtp-decision-summary ol\s*\{\s*margin: 4px 0 8px;/s,
  'decision summary lists should not inherit large global markdown spacing',
);
assert.match(
  stylesSource,
  /\.dtp-decision-summary li\s*\{\s*margin-bottom: 4px;/s,
  'decision summary list items should stay compact',
);
