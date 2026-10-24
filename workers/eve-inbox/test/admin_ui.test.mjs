import test from 'node:test';
import assert from 'node:assert/strict';
import vm from 'node:vm';
import { renderAdminHtml } from '../src/admin_ui.mjs';

const script = () => renderAdminHtml({adminEmail: 'admin@example.test'}).match(/<script>([\s\S]*?)<\/script>/)[1];

test('rendered admin JavaScript parses and loads all queue panels', async () => {
  const elements = new Map();
  const calls = [];
  const document = { getElementById(id) {
    if (!elements.has(id)) elements.set(id, {innerHTML: '', textContent: '', style: {}, value: ''});
    return elements.get(id);
  }};
  const context = vm.createContext({document, setTimeout, fetch: async (url, options) => {
    calls.push({url, options});
    return {ok: true, json: async () => ({items: [{id:'test-item', subject:'Fixture', extracted_urls:'["https://example.test"]'}], x_drafts:[{id:'test-draft', body:'Fixture'}], reading_list:[]})};
  }});
  new vm.Script(script()).runInContext(context);
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(elements.get('queue-count').textContent, '1');
  assert.equal(elements.get('x-drafts-count').textContent, '1');
  assert.equal(elements.get('reading-count').textContent, '0');
  assert.match(elements.get('queue-list').innerHTML, /Fixture/);
  assert.match(elements.get('x-drafts-list').innerHTML, /disabled/);
  assert.deepEqual(calls.map(x => x.url), ['/admin/api/queue']);
  assert.equal(calls[0].options.credentials, 'same-origin');
  assert.equal(context.safeHref('https://example.test/a'), 'https://example.test/a');
  assert.equal(context.safeHref('javascript:alert(1)'), '#');
});
