import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

const appSource = readFileSync(new URL('./App.jsx', import.meta.url), 'utf8');
const indexHtml = readFileSync(new URL('../index.html', import.meta.url), 'utf8');

assert.match(appSource, /<strong>Verumtrade<\/strong>/);
assert.doesNotMatch(appSource, /<strong>Boolean Trader<\/strong>/);
assert.match(indexHtml, /<title>Verumtrade · Research Desk<\/title>/);
assert.doesNotMatch(indexHtml, /<title>frontend<\/title>/);
