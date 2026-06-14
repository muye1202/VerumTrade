import assert from 'node:assert/strict';

import {
  DISCOVERY_SIGNAL_STAGES,
  getDecisionRows,
  getEvidencePackRows,
  getSignalRows,
  getThesisCardRows,
  hasDiscoveryDecisionData,
  hasDiscoverySignalData,
  topSignalRows,
} from './discoverySignalsViewModel.js';

const mixedPayload = {
  signals: [
    { ticker: 'AAA', attention_gap_score: 0.42 },
    { ticker: 'BBB', attention_gap_score: 0.91 },
    { ticker: 'CCC', attention_gap_score: 0.73 },
  ],
};

assert.deepEqual(
  DISCOVERY_SIGNAL_STAGES.map((item) => item.label),
  ['Theme Engine', 'Universe Screen', 'Enrichment', 'Inflection', 'Attention Gap'],
);

assert.equal(getSignalRows(mixedPayload).length, 3);
assert.equal(getSignalRows([{ ticker: 'ZZZ' }]).length, 1);
assert.deepEqual(getSignalRows(null), []);

assert.deepEqual(
  topSignalRows(mixedPayload, 'attention_gap_score', 2).map((item) => item.ticker),
  ['BBB', 'CCC'],
);

assert.equal(
  hasDiscoverySignalData({
    business_inflection_json: { signals: [{ ticker: 'AAA' }] },
    attention_gap_json: { signals: [] },
  }),
  true,
);

assert.equal(
  hasDiscoverySignalData({
    business_inflection_json: { signals: [] },
    attention_gap_json: { signals: [] },
  }),
  false,
);

const decisionPayload = {
  candidates: [
    { ticker: 'OLD', discovery_score: 48, tier: 'theme_candidate' },
    { ticker: 'NEW', discovery_score: 73, tier: 'actionable' },
  ],
};

assert.deepEqual(
  getDecisionRows(decisionPayload).map((item) => item.ticker),
  ['OLD', 'NEW'],
);
assert.deepEqual(getDecisionRows(null), []);
assert.equal(getEvidencePackRows({ packs: [{ ticker: 'NEW' }] }).length, 1);
assert.equal(getThesisCardRows({ cards: [{ ticker: 'NEW' }] }).length, 1);
assert.equal(
  hasDiscoveryDecisionData({
    two_layer_scoring_json: decisionPayload,
    thesis_cards_json: { cards: [] },
  }),
  true,
);
assert.equal(
  hasDiscoveryDecisionData({
    two_layer_scoring_json: { candidates: [] },
    thesis_cards_json: { cards: [] },
  }),
  false,
);

console.log('discoverySignalsViewModel checks passed');
