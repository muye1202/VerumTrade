import assert from 'node:assert/strict';

import {
  DEFAULT_ANALYSTS,
  REPORT_SECTIONS,
  ANALYST_SUMMARY_LABEL,
} from './analysisConfig.js';

assert.deepEqual(DEFAULT_ANALYSTS, [
  'catalyst',
  'market',
  'social',
  'news',
  'fundamentals',
]);

assert.deepEqual(REPORT_SECTIONS.slice(0, 5), [
  ['catalyst_report', 'Catalyst'],
  ['market_report', 'Market'],
  ['sentiment_report', 'Sentiment'],
  ['news_report', 'News'],
  ['fundamentals_report', 'Fundamentals'],
]);

assert.equal(ANALYST_SUMMARY_LABEL, 'Catalyst, Market, Social, News, Fundamentals');
