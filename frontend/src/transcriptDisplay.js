const PRESENTATION = {
  user: {
    side: 'outgoing',
    label: 'You',
    avatar: 'user',
  },
  agent: {
    side: 'incoming',
    label: 'Agent',
    avatar: 'agent',
  },
  tool_output: {
    side: 'incoming',
    label: 'Tool',
    avatar: 'tool',
  },
};

export const getTranscriptMessagePresentation = (type) => (
  PRESENTATION[type] || {
    side: 'incoming',
    label: 'Agent',
    avatar: 'agent',
  }
);

const REPORT_LABELS = {
  annualReports: 'Annual Reports',
  quarterlyReports: 'Quarterly Reports',
};

const stripJsonWrapper = (content) => {
  const trimmed = String(content || '').trim();
  const withoutFence = trimmed
    .replace(/^```(?:json)?\s*/i, '')
    .replace(/\s*```$/i, '')
    .trim();
  const start = withoutFence.indexOf('{');
  const end = withoutFence.lastIndexOf('}');
  if (start === -1 || end === -1 || end <= start) return withoutFence;
  return withoutFence.slice(start, end + 1);
};

const parseRetrievedInfo = (content) => {
  try {
    return JSON.parse(stripJsonWrapper(content));
  } catch {
    return null;
  }
};

const getStatementType = (firstReport) => {
  if (!firstReport || typeof firstReport !== 'object') return 'Retrieved Data';
  if ('grossProfit' in firstReport || 'totalRevenue' in firstReport || 'netIncome' in firstReport) {
    return 'Income Statement';
  }
  if ('totalAssets' in firstReport || 'totalLiabilities' in firstReport || 'totalShareholderEquity' in firstReport) {
    return 'Balance Sheet';
  }
  if ('operatingCashflow' in firstReport || 'cashflowFromInvestment' in firstReport || 'capitalExpenditures' in firstReport) {
    return 'Cash Flow';
  }
  return 'Retrieved Data';
};

export const getRetrievedInfoTitle = (content, index = 0) => {
  const parsed = parseRetrievedInfo(content);
  if (parsed && typeof parsed === 'object') {
    const reportKey = ['annualReports', 'quarterlyReports'].find((key) => Array.isArray(parsed[key]));
    const firstReport = reportKey ? parsed[reportKey][0] : null;
    const symbol = parsed.symbol ? `${parsed.symbol} ` : '';
    const reportLabel = reportKey ? REPORT_LABELS[reportKey] : 'Data';
    const date = firstReport?.fiscalDateEnding ? ` through ${firstReport.fiscalDateEnding}` : '';
    return `${symbol}${getStatementType(firstReport)} - ${reportLabel}${date}`;
  }

  const firstLine = String(content).split('\n').find((line) => line.trim()) || '';
  const title = firstLine.trim().replace(/^[{[]+\s*/, '').slice(0, 72);
  return title || `Retrieved info item ${index + 1}`;
};

export const groupTranscriptLogs = (logs) => {
  const result = [];
  let toolBatch = [];
  let retrievedInfoBatch = [];

  const flushToolBatch = () => {
    if (toolBatch.length > 0) {
      result.push({ id: toolBatch[0].id, type: 'tool_group', tools: [...toolBatch] });
      toolBatch = [];
    }
  };

  const flushRetrievedInfoBatch = () => {
    if (retrievedInfoBatch.length > 0) {
      result.push({
        id: retrievedInfoBatch[0].id,
        type: 'retrieved_info_group',
        items: [...retrievedInfoBatch],
      });
      retrievedInfoBatch = [];
    }
  };

  for (const log of logs) {
    if (log.type === 'tool') {
      flushRetrievedInfoBatch();
      toolBatch.push(log);
      continue;
    }

    if (log.type === 'tool_output') {
      flushToolBatch();
      retrievedInfoBatch.push(log);
      continue;
    }

    flushToolBatch();
    flushRetrievedInfoBatch();
    result.push(log);
  }

  flushToolBatch();
  flushRetrievedInfoBatch();
  return result;
};
