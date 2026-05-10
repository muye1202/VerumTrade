import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';

const here = dirname(fileURLToPath(import.meta.url));
const appSource = readFileSync(join(here, 'App.jsx'), 'utf8');

assert.match(
  appSource,
  /const ReportMarkdown = memo\(/,
  'report markdown rendering should be isolated in a memoized component',
);

assert.match(
  appSource,
  /const ReportSection = memo\(/,
  'each report accordion row should be isolated in a memoized component',
);

const sectionComponentStart = appSource.indexOf('const ReportSection = memo(');
const expandedBodyStart = appSource.indexOf('if (!isExpanded)', sectionComponentStart);
const reportTextCompute = appSource.indexOf('const reportText = renderReportText', sectionComponentStart);

assert.ok(sectionComponentStart >= 0, 'ReportSection component should exist');
assert.ok(expandedBodyStart >= 0, 'ReportSection should return early for collapsed sections');
assert.ok(reportTextCompute > expandedBodyStart, 'report text should only be computed after expansion');
