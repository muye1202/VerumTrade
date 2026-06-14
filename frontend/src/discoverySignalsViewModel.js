export const DISCOVERY_SIGNAL_STAGES = [
  { stage: -1, label: 'Theme Engine' },
  { stage: 0, label: 'Universe Screen' },
  { stage: 1, label: 'Enrichment' },
  { stage: 2, label: 'Inflection' },
  { stage: 3, label: 'Attention Gap' },
];

export function getSignalRows(payload) {
  if (Array.isArray(payload)) return payload;
  if (Array.isArray(payload?.signals)) return payload.signals;
  return [];
}

export function getDecisionRows(payload) {
  if (Array.isArray(payload)) return payload;
  if (Array.isArray(payload?.candidates)) return payload.candidates;
  return [];
}

export function getEvidencePackRows(payload) {
  if (Array.isArray(payload)) return payload;
  if (Array.isArray(payload?.packs)) return payload.packs;
  return [];
}

export function getThesisCardRows(payload) {
  if (Array.isArray(payload)) return payload;
  if (Array.isArray(payload?.cards)) return payload.cards;
  return [];
}

export function hasSignalRows(payload) {
  return getSignalRows(payload).length > 0;
}

export function hasDecisionRows(payload) {
  return getDecisionRows(payload).length > 0;
}

export function hasThesisCardRows(payload) {
  return getThesisCardRows(payload).length > 0;
}

export function hasDiscoverySignalData(reports = {}) {
  return (
    hasSignalRows(reports.business_inflection_json)
    || hasSignalRows(reports.attention_gap_json)
  );
}

export function hasDiscoveryDecisionData(reports = {}) {
  return (
    hasDecisionRows(reports.two_layer_scoring_json)
    || hasThesisCardRows(reports.thesis_cards_json)
  );
}

export function topSignalRows(payload, scoreKey, limit = 6) {
  return [...getSignalRows(payload)]
    .sort((a, b) => Number(b?.[scoreKey] || 0) - Number(a?.[scoreKey] || 0))
    .slice(0, limit);
}

export function topDecisionRows(payload, limit = 6) {
  return [...getDecisionRows(payload)]
    .sort((a, b) => Number(b?.discovery_score || 0) - Number(a?.discovery_score || 0))
    .slice(0, limit);
}

export function formatPercentScore(value) {
  const numeric = Number(value || 0);
  const normalized = Math.abs(numeric) <= 1 ? numeric * 100 : numeric;
  return `${Math.round(normalized)}%`;
}

export function formatDiscoveryScore(value) {
  const numeric = Number(value || 0);
  return Math.round(numeric).toString();
}
