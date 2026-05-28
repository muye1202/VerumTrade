const asArray = (value) => (Array.isArray(value) ? value : []);

const normalizeId = (value) => String(value ?? '').trim();

const byId = (items) => {
  const map = new Map();
  asArray(items).forEach((item) => {
    if (item && typeof item === 'object') {
      const id = normalizeId(item.id);
      if (id) map.set(id, item);
    }
  });
  return map;
};

export const countAuditIssuesBySeverity = (issues) => {
  const counts = { high: 0, medium: 0, low: 0 };
  asArray(issues).forEach((issue) => {
    const severity = String(issue?.severity || '').toLowerCase();
    if (severity in counts) counts[severity] += 1;
  });
  return counts;
};

export const getDecisionTraceViewModel = (trace, evidenceGraph) => {
  const safeTrace = trace && typeof trace === 'object' ? trace : {};
  const graph = evidenceGraph && typeof evidenceGraph === 'object' ? evidenceGraph : {};
  const inferenceLookup = byId(graph.inferences);
  const factLookup = byId(graph.facts);

  const decision = safeTrace.decision && typeof safeTrace.decision === 'object'
    ? safeTrace.decision
    : {};
  const thesis = safeTrace.thesis && typeof safeTrace.thesis === 'object'
    ? safeTrace.thesis
    : {};
  const inferenceIds = asArray(safeTrace.inference_ids?.length ? safeTrace.inference_ids : thesis.inference_ids)
    .map(normalizeId)
    .filter(Boolean);
  const factIds = asArray(safeTrace.fact_ids).map(normalizeId).filter(Boolean);
  const auditIssues = asArray(safeTrace.audit_issues);
  const severityCounts = countAuditIssuesBySeverity(auditIssues);

  const inferenceRows = inferenceIds.map((id) => {
    const inference = inferenceLookup.get(id) || null;
    const supportFacts = asArray(inference?.support_fact_ids)
      .map((factId) => factLookup.get(normalizeId(factId)))
      .filter(Boolean);
    const counterFacts = asArray(inference?.counter_fact_ids)
      .map((factId) => factLookup.get(normalizeId(factId)))
      .filter(Boolean);
    return {
      id,
      inference,
      missing: !inference,
      supportFacts,
      counterFacts,
    };
  });

  const factRows = factIds.map((id) => {
    const fact = factLookup.get(id) || null;
    return {
      id,
      fact,
      missing: !fact,
    };
  });

  return {
    decision: {
      action: String(decision.action || ''),
      ticker: String(decision.ticker || ''),
      summary: String(decision.summary || ''),
    },
    thesis: {
      claim: String(thesis.claim || ''),
      inferenceIds,
    },
    inferenceRows,
    factRows,
    sourceLabels: asArray(safeTrace.source_labels).map(String).filter(Boolean),
    auditIssues,
    severityCounts,
    rawTrace: safeTrace,
    summary: {
      linkedInferenceCount: inferenceRows.filter((row) => !row.missing).length,
      missingInferenceCount: inferenceRows.filter((row) => row.missing).length,
      linkedFactCount: factRows.filter((row) => !row.missing).length,
      missingFactCount: factRows.filter((row) => row.missing).length,
      sourceCount: asArray(safeTrace.source_labels).filter(Boolean).length,
      highSeverityAuditCount: severityCounts.high,
    },
  };
};
