import assert from 'node:assert/strict';

import {
  getRetrievedInfoTitle,
  getTranscriptMessagePresentation,
  groupTranscriptLogs,
} from './transcriptDisplay.js';

assert.deepEqual(getTranscriptMessagePresentation('user'), {
  side: 'outgoing',
  label: 'You',
  avatar: 'user',
});

assert.deepEqual(getTranscriptMessagePresentation('agent'), {
  side: 'incoming',
  label: 'Agent',
  avatar: 'agent',
});

assert.deepEqual(getTranscriptMessagePresentation('tool_output'), {
  side: 'incoming',
  label: 'Tool',
  avatar: 'tool',
});

assert.equal(getTranscriptMessagePresentation('unknown').side, 'incoming');

const sampleLogs = [
  { id: 'a', type: 'agent', content: 'Thinking' },
  { id: 't1', type: 'tool', content: 'get_income_statement: {"symbol":"INTC"}' },
  { id: 't2', type: 'tool', content: 'get_balance_sheet: {"symbol":"INTC"}' },
  { id: 'r1', type: 'tool_output', content: '{"symbol":"INTC","annualReports":[{"totalRevenue":"52853000000"}]}' },
  { id: 'r2', type: 'tool_output', content: '{"symbol":"INTC","annualReports":[{"totalAssets":"211429000000"}]}' },
  { id: 'b', type: 'agent', content: 'Summary' },
];

assert.deepEqual(groupTranscriptLogs(sampleLogs), [
  sampleLogs[0],
  { id: 't1', type: 'tool_group', tools: [sampleLogs[1], sampleLogs[2]] },
  { id: 'r1', type: 'retrieved_info_group', items: [sampleLogs[3], sampleLogs[4]] },
  sampleLogs[5],
]);

assert.equal(
  getRetrievedInfoTitle(`{
    "symbol": "INTC",
    "annualReports": [{
      "fiscalDateEnding": "2025-12-31",
      "grossProfit": "18375000000",
      "totalRevenue": "52853000000"
    }]
  }`, 0),
  'INTC Income Statement - Annual Reports through 2025-12-31',
);

assert.equal(
  getRetrievedInfoTitle('```json\n{"symbol":"INTC","annualReports":[{"fiscalDateEnding":"2025-12-31","totalAssets":"211429000000"}]}\n```', 1),
  'INTC Balance Sheet - Annual Reports through 2025-12-31',
);

assert.equal(
  getRetrievedInfoTitle('{"symbol":"INTC","annualReports":[{"fiscalDateEnding":"2025-12-31","operatingCashflow":"9697000000"}]}', 2),
  'INTC Cash Flow - Annual Reports through 2025-12-31',
);
