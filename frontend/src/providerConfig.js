export const PROVIDER_DEFAULTS = {
  openai: {
    name: 'OpenAI',
    apiKeyUrl: 'https://platform.openai.com/api-keys',
    baseUrl: 'https://api.openai.com/v1',
  },
  'azure-foundry': {
    name: 'Azure Foundry',
    apiKeyUrl: 'https://ai.azure.com/',
    baseUrl: '',
  },
  anthropic: {
    name: 'Anthropic',
    apiKeyUrl: 'https://console.anthropic.com/',
    baseUrl: 'https://api.anthropic.com',
  },
  'qwen3-cn': {
    name: 'Qwen (DashScope)',
    apiKeyUrl: 'https://dashscope.console.aliyun.com/',
    baseUrl: 'https://dashscope.aliyuncs.com/compatible-mode/v1',
  },
  deepseek: {
    name: 'DeepSeek',
    apiKeyUrl: 'https://platform.deepseek.com/',
    baseUrl: 'https://api.deepseek.com/v1',
  },
  glm: {
    name: 'GLM (ZhipuAI)',
    apiKeyUrl: 'https://open.bigmodel.cn/',
    baseUrl: 'https://open.bigmodel.cn/api/paas/v4',
  },
  openrouter: {
    name: 'OpenRouter',
    apiKeyUrl: 'https://openrouter.ai/settings/keys',
    baseUrl: 'https://openrouter.ai/api/v1',
  },
};

export const AZURE_FOUNDRY_REASONING_EFFORTS = [
  { value: 'low', label: 'Low' },
  { value: 'medium', label: 'Medium' },
  { value: 'high', label: 'High' },
];

export const getAzureFoundryReasoningMode = (modelName) => {
  const name = String(modelName || '').trim().toLowerCase();
  if (!name) return 'none';
  if (name.includes('deepseek') || name.includes('r1')) return 'native';
  if (name.startsWith('gpt-5') || name.startsWith('o1') || name.startsWith('o3') || name.startsWith('o4')) {
    return 'effort';
  }
  return 'none';
};

export const createDefaultProviderEndpoints = () => (
  Object.fromEntries(
    Object.entries(PROVIDER_DEFAULTS).map(([id, provider]) => [id, provider.baseUrl]),
  )
);

export const normalizeProviderEndpoints = (saved = {}) => {
  const defaults = createDefaultProviderEndpoints();
  const normalized = { ...defaults };
  Object.keys(defaults).forEach((id) => {
    const value = typeof saved[id] === 'string' ? saved[id].trim() : '';
    normalized[id] = value || defaults[id];
  });
  return normalized;
};

export const getProviderBaseUrl = (providerId, providerEndpoints = {}) => {
  const configured = typeof providerEndpoints[providerId] === 'string'
    ? providerEndpoints[providerId].trim()
    : '';
  return configured || PROVIDER_DEFAULTS[providerId]?.baseUrl || null;
};

export const buildProviderSettingsPayload = ({ apiKeys = {}, providerEndpoints = {} } = {}) => {
  const payload = {};
  Object.keys(PROVIDER_DEFAULTS).forEach((id) => {
    const apiKey = typeof apiKeys[id] === 'string' ? apiKeys[id].trim() : '';
    const baseUrl = getProviderBaseUrl(id, providerEndpoints);
    payload[id] = {};
    if (apiKey) payload[id].api_key = apiKey;
    if (baseUrl) payload[id].base_url = baseUrl;
  });
  return payload;
};
