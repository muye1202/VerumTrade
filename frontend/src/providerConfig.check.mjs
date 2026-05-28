import assert from 'node:assert/strict';

import {
  buildProviderSettingsPayload,
  getProviderBaseUrl,
  normalizeProviderEndpoints,
} from './providerConfig.js';

const endpoints = normalizeProviderEndpoints({
  openai: ' https://proxy.example.test/v1 ',
  anthropic: '',
});

assert.equal(endpoints.openai, 'https://proxy.example.test/v1');
assert.equal(endpoints.anthropic, 'https://api.anthropic.com');
assert.equal(getProviderBaseUrl('openai', endpoints), 'https://proxy.example.test/v1');
assert.equal(getProviderBaseUrl('deepseek', endpoints), 'https://api.deepseek.com/v1');

assert.deepEqual(
  buildProviderSettingsPayload({
    apiKeys: { openai: ' sk-local ', glm: '' },
    providerEndpoints: endpoints,
  }).openai,
  {
    api_key: 'sk-local',
    base_url: 'https://proxy.example.test/v1',
  },
);

assert.equal(
  buildProviderSettingsPayload({
    apiKeys: {},
    providerEndpoints: { openai: 'https://api.openai.com/v1' },
  }).openai.api_key,
  undefined,
);
