// Run: node tests/js/test_evidence_view.js  (also run by tests/test_board_js.py)
const assert = require('assert');
const fs = require('fs');
const path = require('path');
const E = require('../../static/evidence.js');

// A tiny DOM: enough for evidence.js, and it refuses innerHTML outright.
class FakeNode {
    constructor(tag) { this.tagName = tag.toUpperCase(); this.children = []; this.attributes = {}; this.className = ''; this.ownText = ''; }
    appendChild(child) { this.children.push(child); return child; }
    setAttribute(name, value) { this.attributes[name] = String(value); }
    getAttribute(name) { return Object.prototype.hasOwnProperty.call(this.attributes, name) ? this.attributes[name] : null; }
    set textContent(value) { this.ownText = String(value); this.children = []; }
    get textContent() { return this.ownText + this.children.map(c => c.textContent).join(''); }
    set innerHTML(value) { throw new Error('evidence.js must never set innerHTML'); }
}
const doc = { createElement: tag => new FakeNode(tag) };
const all = (node, test, out = []) => { if (test(node)) out.push(node); node.children.forEach(c => all(c, test, out)); return out; };
const tagged = (node, tag) => all(node, n => n.tagName === tag.toUpperCase());

function fact(over) {
    return Object.assign({ id: 12, phase_key: '4', claim: 'Deakin charges $3,000 per unit', quote: '', as_of: '2026', status: 'active',
        source: { url: 'https://deakin.edu.au/fees', title: 'Deakin fees', publisher: '', published_date: '' },
        card_id: 7, card_title: 'Fees', created_at: '2026-10-05T10:00:00' }, over || {});
}
function conclusion(over) {
    return Object.assign({ id: 3, phase_key: '4', text: 'Deakin is the priciest', status: 'active', fact_ids: [2, 5, 7],
        rejected_fact_count: 0, card_id: 7, card_title: 'Fees' }, over || {});
}
const tests = [];
const test = (name, fn) => tests.push([name, fn]);

test('the source never uses HTML-string APIs', () => {
    const source = fs.readFileSync(path.join(__dirname, '..', '..', 'static', 'evidence.js'), 'utf8');
    ['innerHTML', 'outerHTML', 'insertAdjacentHTML', 'document.write'].forEach(api => assert(!source.includes(api), api));
});
test('AI and web text is shown as text, not markup', () => {
    const node = E.phaseEvidenceNode(doc, { facts: [fact({ claim: '<img src=x onerror=alert(1)>', card_title: '<b>Fees</b>' })], conclusions: [] });
    assert.strictEqual(tagged(node, 'img').length, 0);
    assert.strictEqual(tagged(node, 'b').length, 0);
    assert(node.textContent.includes('<img src=x onerror=alert(1)>'));
});
test('a web page becomes a safe link that opens in a new tab', () => {
    const [link] = tagged(E.factNode(doc, fact(), false), 'a');
    assert.strictEqual(link.getAttribute('href'), 'https://deakin.edu.au/fees');
    assert.strictEqual(link.getAttribute('target'), '_blank');
    assert.strictEqual(link.getAttribute('rel'), 'noopener noreferrer');
    assert.strictEqual(link.textContent, 'Deakin fees');
});
test('only http and https addresses become links', () => {
    ['javascript:alert(1)', 'data:text/html,hi', ' JAVASCRIPT:alert(1)', 'ftp://x.example', '//evil.example', ''].forEach(url => {
        const node = E.factNode(doc, fact({ source: { url, title: 'Page' } }), false);
        assert.strictEqual(tagged(node, 'a').length, 0, url);
        assert(node.textContent.includes('Page'), url);
    });
    assert.strictEqual(E.safeUrl('HTTPS://ok.example/x'), 'HTTPS://ok.example/x');
    assert.strictEqual(E.safeUrl('javascript:alert(1)'), null);
    assert.strictEqual(E.safeUrl(null), null);
});
test('a fact without a page says it comes from the report', () => {
    assert(E.factNode(doc, fact({ source: null }), false).textContent.includes('Source: the research report'));
});
test('a fact shows its number, date, research and a Reject button', () => {
    const node = E.factNode(doc, fact(), false);
    assert.strictEqual(node.getAttribute('id'), 'ev-fact-12');
    const text = node.textContent;
    assert(text.includes('#12') && text.includes('As of 2026') && text.includes('From: Fees'));
    const [button] = tagged(node, 'button');
    assert.deepStrictEqual([button.getAttribute('data-ev-act'), button.getAttribute('data-ev-kind'), button.getAttribute('data-ev-id')],
                           ['reject', 'fact', '12']);
});
test('a conclusion links to the facts behind it', () => {
    const node = E.conclusionNode(doc, conclusion());
    assert.deepStrictEqual(tagged(node, 'a').map(a => a.getAttribute('href')), ['#ev-fact-2', '#ev-fact-5', '#ev-fact-7']);
    assert(node.textContent.includes('Based on facts 2, 5, 7'));
    assert(E.conclusionNode(doc, conclusion({ fact_ids: [4] })).textContent.includes('Based on fact 4'));
});
test('a conclusion says when its facts were rejected', () => {
    assert(E.conclusionNode(doc, conclusion({ rejected_fact_count: 1 })).textContent.includes('1 of its facts was rejected'));
    assert(E.conclusionNode(doc, conclusion({ rejected_fact_count: 2 })).textContent.includes('2 of its facts were rejected'));
});
test('rejected items sit under Show rejected, with Restore', () => {
    const node = E.phaseEvidenceNode(doc, { facts: [fact(), fact({ id: 13, claim: 'Old claim', status: 'rejected' })],
                                             conclusions: [conclusion({ status: 'rejected' })] });
    const [box] = tagged(node, 'details');
    assert(box.textContent.includes('Show rejected (2)') && box.textContent.includes('Old claim'));
    assert.deepStrictEqual(tagged(box, 'button').map(b => b.getAttribute('data-ev-act')), ['restore', 'restore']);
    assert(node.textContent.includes('Facts (1)'));
    assert(!node.textContent.includes('Conclusions'));  // its only conclusion was rejected
});
test('a phase with nothing yet says so', () => {
    assert(E.phaseEvidenceNode(doc, undefined).textContent.includes('No facts yet'));
    assert(E.phaseEvidenceNode(doc, { facts: [], conclusions: [] }).textContent.includes('No facts yet'));
});
test('search results show the phase, with no buttons or anchors', () => {
    const node = E.searchResultsNode(doc, [fact()], 'deakin');
    assert(node.textContent.includes('1 fact matches') && node.textContent.includes('Phase 4'));
    assert.strictEqual(tagged(node, 'button').length, 0);
    assert.strictEqual(all(node, n => n.getAttribute('id') !== null).length, 0);
    assert(E.searchResultsNode(doc, [], '<x>').textContent.includes('No facts match “<x>”.'));
});
test('Show rejected stays open or closed as the person left it, per phase, across a re-render', () => {
    const data = { facts: [fact({ id: 13, status: 'rejected' })], conclusions: [] };
    const state = E.createOpenState();
    const draw = key => tagged(E.phaseEvidenceNode(doc, data, state.get(key)), 'details')[0];
    assert.strictEqual(draw('4').getAttribute('open'), null);       // closed by default
    state.set('4', true);                                            // the person opens it, then rejects or restores something
    assert.notStrictEqual(draw('4').getAttribute('open'), null);     // the re-render keeps it open
    assert.strictEqual(draw('5').getAttribute('open'), null);        // another phase is not affected
    state.set('4', false);
    assert.strictEqual(draw('4').getAttribute('open'), null);        // and closing is remembered too
});
test('the open state is kept apart for each phase', () => {
    const state = E.createOpenState();
    state.set('2', true); state.set('3', true); state.set('2', false);
    assert.deepStrictEqual(['1', '2', '3'].map(key => state.get(key)), [false, false, true]);
});

let failed = 0;
for (const [name, fn] of tests) {
    try { fn(); console.log('ok -', name); } catch (e) { failed++; console.log('FAIL -', name, '\n ', e.message); }
}
if (failed) { console.log(`${failed} failed`); process.exit(1); }
console.log(`all ${tests.length} passed`);
