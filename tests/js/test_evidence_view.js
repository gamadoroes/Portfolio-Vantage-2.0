// Run: node tests/js/test_evidence_view.js  (also run by tests/test_board_js.py)
const assert = require('assert');
const fs = require('fs');
const path = require('path');
const E = require('../../static/evidence.js');

// A tiny DOM: enough for evidence.js, and it refuses innerHTML outright. Text nodes exist so the marker linking on
// the phase cards can be tested; elements keep every child (text nodes too) in `children`.
class FakeText {
    constructor(text) { this.nodeType = 3; this.nodeValue = String(text); this.children = []; this.className = ''; }
    get textContent() { return this.nodeValue; }
    getAttribute() { return null; }
}
class FakeNode {
    constructor(tag) { this.nodeType = 1; this.tagName = tag.toUpperCase(); this.children = []; this.attributes = {}; this.className = ''; this.ownText = ''; }
    get childNodes() { return this.children; }
    appendChild(child) { child.parentNode = this; this.children.push(child); return child; }
    insertBefore(node, ref) { const at = this.children.indexOf(ref); node.parentNode = this; this.children.splice(at < 0 ? this.children.length : at, 0, node); return node; }
    removeAttribute(name) { delete this.attributes[name]; }
    get firstChild() { return this.children[0] || null; }
    removeChild(child) { this.children = this.children.filter(c => c !== child); child.parentNode = null; return child; }
    after(node) { const siblings = this.parentNode.children; node.parentNode = this.parentNode; siblings.splice(siblings.indexOf(this) + 1, 0, node); }
    setAttribute(name, value) { this.attributes[name] = String(value); }
    getAttribute(name) { return Object.prototype.hasOwnProperty.call(this.attributes, name) ? this.attributes[name] : null; }
    set textContent(value) { this.ownText = String(value); this.children = []; }
    get textContent() { return this.ownText + this.children.map(c => c.textContent).join(''); }
    set innerHTML(value) { throw new Error('evidence.js must never set innerHTML'); }
}
const doc = { createElement: tag => new FakeNode(tag), createTextNode: text => new FakeText(text) };

// A page for the controller: a search input and results box, a slot for each phase, and (optionally) the phase
// cards' rendered write-ups, each a [data-cite-phase] box.
function makePage(phases, summaries) {
    const page = { input: new FakeNode('input'), results: new FakeNode('div'), slots: phases.map(key => { const s = new FakeNode('div'); s.setAttribute('data-evidence-phase', key); return s; }),
                   summaries: summaries || [] };
    page.input.value = 'typed words';
    page.document = {
        createElement: tag => new FakeNode(tag),
        createTextNode: text => new FakeText(text),
        getElementById: id => (id === 'evidence-search-input' ? page.input : id === 'evidence-search-results' ? page.results
            : page.slots.map(slot => all(slot, n => n.getAttribute('id') === id)[0]).find(Boolean) || null),
        querySelectorAll: selector => (selector === '[data-cite-phase]' ? page.summaries : page.slots),
    };
    return page;
}
// Timers you fire by hand, and requests you answer by hand.
function makeEnv(page) {
    const env = { timers: [], requests: [] };
    env.document = page.document;
    env.setTimeout = fn => { env.timers.push(fn); return env.timers.length; };
    env.clearTimeout = id => { if (id) env.timers[id - 1] = null; };
    env.call = (method, url, body) => new Promise((resolve, reject) => env.requests.push({ method, url, body, resolve, reject }));
    return env;
}
const flush = () => new Promise(resolve => setImmediate(resolve));
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

// ---- long fact lists fold to the newest 10 ----
// The server lists facts oldest first (ORDER BY id); a phase with 25 facts has ids 1..25.
const manyFacts = n => Array.from({ length: n }, (_, i) => fact({ id: i + 1, claim: `Claim ${i + 1}` }));
const byClass = (node, cls) => all(node, n => n.className === cls);
const factIds = node => all(node, n => n.tagName === 'LI' && /^ev-fact/.test(n.className)).map(n => Number(n.getAttribute('id').replace('ev-fact-', '')));
const visibleFactIds = node => factIds(node).filter(id => !byClass(node, 'ev-more').some(box => factIds(box).includes(id)));

test('25 facts show the newest 10 first and fold the other 15 under Show 15 more', () => {
    const node = E.phaseEvidenceNode(doc, { facts: manyFacts(25), conclusions: [] });
    assert(node.textContent.includes('Facts (25)'));
    const [more] = byClass(node, 'ev-more');
    assert.strictEqual(more.tagName, 'DETAILS');
    assert.strictEqual(more.children[0].tagName, 'SUMMARY');
    assert.strictEqual(more.children[0].textContent, 'Show 15 more');
    assert.deepStrictEqual(factIds(more), [15, 14, 13, 12, 11, 10, 9, 8, 7, 6, 5, 4, 3, 2, 1]);   // the rest, still newest first
    assert.deepStrictEqual(visibleFactIds(node), [25, 24, 23, 22, 21, 20, 19, 18, 17, 16]);       // the box sits after these
    assert.strictEqual(more.getAttribute('open'), null);                                           // folded by default
});
test('the folded box follows the visible facts, and each fact still has its Reject button', () => {
    const node = E.phaseEvidenceNode(doc, { facts: manyFacts(25), conclusions: [] });
    const [more] = byClass(node, 'ev-more');
    assert.strictEqual(more.parentNode, node);
    assert.strictEqual(node.children[node.children.indexOf(more) - 1].className, 'ev-list');
    assert.strictEqual(tagged(more, 'button').length, 15);
    assert.deepStrictEqual(Array.from(new Set(tagged(more, 'button').map(b => b.getAttribute('data-ev-act')))), ['reject']);
});
test('10 facts or fewer are all shown, newest first, with no folded box', () => {
    [1, 3, 10].forEach(n => {
        const node = E.phaseEvidenceNode(doc, { facts: manyFacts(n), conclusions: [] });
        assert.strictEqual(byClass(node, 'ev-more').length, 0, `${n} facts`);
        assert.deepStrictEqual(factIds(node), Array.from({ length: n }, (_, i) => n - i), `${n} facts`);
    });
    const eleven = E.phaseEvidenceNode(doc, { facts: manyFacts(11), conclusions: [] });
    assert.strictEqual(byClass(eleven, 'ev-more')[0].children[0].textContent, 'Show 1 more');
});
test('newest first goes by fact number, whatever order the list arrives in', () => {
    const shuffled = [fact({ id: 7 }), fact({ id: 30 }), fact({ id: 2 })];
    assert.deepStrictEqual(factIds(E.phaseEvidenceNode(doc, { facts: shuffled, conclusions: [] })), [30, 7, 2]);
});
test('only active facts are counted and folded; rejected ones stay in Show rejected, in their old order', () => {
    const facts = manyFacts(12).concat([fact({ id: 13, claim: 'Old claim', status: 'rejected' }), fact({ id: 14, claim: 'Older claim', status: 'rejected' })]);
    const node = E.phaseEvidenceNode(doc, { facts, conclusions: [] });
    assert(node.textContent.includes('Facts (12)'));
    assert.strictEqual(byClass(node, 'ev-more')[0].children[0].textContent, 'Show 2 more');
    const [rejected] = byClass(node, 'ev-rejected');
    assert(rejected.textContent.includes('Show rejected (2)'));
    assert.deepStrictEqual(factIds(rejected), [13, 14]);
    assert.strictEqual(byClass(node, 'ev-more').filter(m => factIds(m).includes(13)).length, 0);
});
test('conclusions stay fully visible however many facts there are', () => {
    const conclusions = Array.from({ length: 5 }, (_, i) => conclusion({ id: i + 1, text: `Conclusion ${i + 1}` }));
    const node = E.phaseEvidenceNode(doc, { facts: manyFacts(25), conclusions });
    const [more] = byClass(node, 'ev-more');
    assert.strictEqual(all(node, n => n.className === 'ev-concl').length, 5);
    assert.strictEqual(all(more, n => n.className === 'ev-concl').length, 0);
});
test('search results are not folded', () => {
    const node = E.searchResultsNode(doc, manyFacts(25), 'claim');
    assert.strictEqual(byClass(node, 'ev-more').length, 0);
    assert.strictEqual(tagged(node, 'li').length, 25);
});
test('a link to a fact that is folded away opens the folded box (and one to a visible fact leaves it shut)', () => {
    const node = E.phaseEvidenceNode(doc, { facts: manyFacts(25), conclusions: [conclusion({ fact_ids: [3, 25] })] });
    const [more] = byClass(node, 'ev-more');
    const find = id => all(node, n => n.getAttribute('id') === `ev-fact-${id}`)[0];
    E.openFoldedBoxes(find(25));
    assert.notStrictEqual(more.open, true);
    E.openFoldedBoxes(find(3));
    assert.strictEqual(more.open, true);
});
test('a link to a rejected fact still opens Show rejected, and only that box', () => {
    const facts = manyFacts(25).concat([fact({ id: 26, status: 'rejected' })]);
    const node = E.phaseEvidenceNode(doc, { facts, conclusions: [] });
    const [more] = byClass(node, 'ev-more');
    const [rejected] = byClass(node, 'ev-rejected');
    E.openFoldedBoxes(all(node, n => n.getAttribute('id') === 'ev-fact-26')[0]);
    assert.strictEqual(rejected.open, true);
    assert.notStrictEqual(more.open, true);
});
test('Show n more stays open or closed as the person left it, per phase, apart from Show rejected', () => {
    const data = { facts: manyFacts(25).concat([fact({ id: 26, status: 'rejected' })]), conclusions: [] };
    const rejected = E.createOpenState(), more = E.createOpenState();
    const draw = key => E.phaseEvidenceNode(doc, data, rejected.get(key), more.get(key));
    const moreBox = key => byClass(draw(key), 'ev-more')[0];
    assert.strictEqual(moreBox('4').getAttribute('open'), null);        // folded by default
    more.set('4', true);                                                // the person opens it, then rejects something
    assert.notStrictEqual(moreBox('4').getAttribute('open'), null);     // the re-render keeps it open
    assert.strictEqual(byClass(draw('4'), 'ev-rejected')[0].getAttribute('open'), null);   // Show rejected did not follow it
    assert.strictEqual(moreBox('5').getAttribute('open'), null);        // another phase is not affected
    more.set('4', false);
    assert.strictEqual(moreBox('4').getAttribute('open'), null);        // closing is remembered too
});
test("the controller remembers each phase's Show n more box across a re-render and forgets it on a project change", async () => {
    const page = makePage(['4', '5']); const env = makeEnv(page); const screen = E.createController(env);
    const answer = { success: true, phases: { 4: { facts: manyFacts(25), conclusions: [] }, 5: { facts: manyFacts(25), conclusions: [] } } };
    const moreBoxes = () => page.slots.map(slot => byClass(slot, 'ev-more')[0]);
    const isOpen = () => moreBoxes().map(box => box.getAttribute('open') !== null);
    screen.renderAll('P');
    env.requests[0].resolve(answer); await flush();
    assert.deepStrictEqual(isOpen(), [false, false]);
    screen.rememberMoreBox('4', true);                                   // the person opened phase 4's box
    screen.renderAll('P');                                               // a reject or restore re-renders
    env.requests[1].resolve(answer); await flush();
    assert.deepStrictEqual(isOpen(), [true, false]);
    screen.rememberRejectedBox('5', true);                               // Show rejected is a separate memory
    screen.renderAll('P'); env.requests[2].resolve(answer); await flush();
    assert.deepStrictEqual(isOpen(), [true, false]);
    screen.renderAll('Other');                                           // a different project starts folded
    env.requests[3].resolve(answer); await flush();
    assert.deepStrictEqual(isOpen(), [false, false]);
});
test('folded facts are still set as text, not markup', () => {
    const node = E.phaseEvidenceNode(doc, { facts: manyFacts(12).map(f => Object.assign(f, { claim: '<img src=x onerror=alert(1)>' })), conclusions: [] });
    assert.strictEqual(tagged(node, 'img').length, 0);
    assert(byClass(node, 'ev-more')[0].textContent.includes('<img src=x onerror=alert(1)>'));
});

test('a search that finishes after a project change paints nothing', async () => {
    const page = makePage(['4']); const env = makeEnv(page); const screen = E.createController(env);
    screen.renderAll('Old');
    const search = screen.runSearch('deakin');
    assert.strictEqual(env.requests.length, 2);                    // the evidence list and the search, both for Old
    screen.renderAll('New');                                       // the person switches project
    assert.strictEqual(page.input.value, '');                      // search box cleared
    env.requests[1].resolve({ success: true, facts: [fact()] });   // the old search comes back late
    await search; await flush();
    assert.strictEqual(page.results.children.length, 0);
});
test('a search that finishes after the search was reset paints nothing', async () => {
    const page = makePage(['4']); const env = makeEnv(page); const screen = E.createController(env);
    screen.renderAll('P');
    const search = screen.runSearch('deakin');
    screen.resetSearch();
    env.requests[1].resolve({ success: true, facts: [fact()] });
    await search;
    assert.strictEqual(page.results.children.length, 0);
});
test('a search for the current project still paints', async () => {
    const page = makePage(['4']); const env = makeEnv(page); const screen = E.createController(env);
    screen.renderAll('P');
    const search = screen.runSearch('deakin');
    env.requests[1].resolve({ success: true, facts: [fact()] });
    await search;
    assert(page.results.textContent.includes('1 fact matches'));
});
test('a pending search timer is cancelled by a project change and never fires', () => {
    const page = makePage(['4']); const env = makeEnv(page); const screen = E.createController(env);
    screen.renderAll('Old');
    const before = env.requests.length;
    screen.onSearchInput('deak');                                  // the debounce timer is waiting
    const waiting = env.timers[0];
    assert.strictEqual(typeof waiting, 'function');
    screen.renderAll('New');                                       // the project change resets the search
    assert.strictEqual(env.timers[0], null);                       // so the timer was cleared and cannot fire
    assert.strictEqual(env.requests.filter(r => r.url.includes('/search')).length, 0);
    assert.strictEqual(env.requests.length, before + 1);           // only New's evidence list was requested
});
test('typing again replaces the waiting search timer', () => {
    const page = makePage(['4']); const env = makeEnv(page); const screen = E.createController(env);
    screen.renderAll('P'); screen.onSearchInput('d'); screen.onSearchInput('de');
    assert.strictEqual(env.timers[0], null);
    assert.strictEqual(typeof env.timers[1], 'function');
});
test('a failed Reject or Restore shows one error message, replaced on each retry', async () => {
    const page = makePage(['4']); const env = makeEnv(page); const screen = E.createController(env);
    screen.renderAll('P');
    env.requests[0].resolve({ success: true, phases: { 4: { facts: [fact()], conclusions: [] } } });
    await flush();
    const [button] = tagged(page.slots[0], 'button');
    for (const message of ['First failure', 'Second failure']) {
        const done = screen.onAction(button);
        env.requests[env.requests.length - 1].reject(new Error(message));
        await done;
    }
    const errors = all(button.parentNode, n => n.className === 'ev-err');
    assert.strictEqual(errors.length, 1);
    assert.strictEqual(errors[0].textContent, 'Second failure');
    assert.strictEqual(button.disabled, false);
});

// ---- Generate: the facts briefing (spec section 4) ----
const BRIEF = { text: '# CHECKED FACTS FOR PHASE 1 (newest first)\n[F1] A claim', fact_count: 1, conclusion_count: 0, shown_facts: 1, rejected_count: 0 };
test('the briefing goes into the prompt when the phase has checked facts or rejected ones to avoid', () => {
    const added = E.briefPromptAddition(BRIEF);
    assert(added.startsWith('\n\nFACTS BRIEFING'));
    assert(added.endsWith('\n\n' + BRIEF.text));
    assert(added.includes('part of your source data'));   // the prompt's "only use the Source Data files" must not forbid it
    assert.strictEqual(E.briefPromptAddition(Object.assign({}, BRIEF, { fact_count: 0 })), '');
    // every fact rejected: the "do not use" list still goes in
    assert.strictEqual(E.briefPromptAddition(Object.assign({}, BRIEF, { fact_count: 0, rejected_count: 2 })), added);
    [null, undefined, {}, { fact_count: 2, text: '' }, { fact_count: 2, text: '   ' }, { fact_count: 2 }, { fact_count: 'none', text: 'x' },
        { rejected_count: 2, text: ' ' }, { rejected_count: 2 }, { fact_count: 'none', rejected_count: 'many', text: 'x' }]
        .forEach(brief => assert.strictEqual(E.briefPromptAddition(brief), '', JSON.stringify(brief)));
});
test('Generate asks for the briefing of this project and phase', async () => {
    const asked = [];
    const call = (method, url) => { asked.push([method, url]); return Promise.resolve({ success: true, brief: BRIEF }); };
    assert.strictEqual(await E.phaseBriefAddition(call, 'My Project & Co', '4'), E.briefPromptAddition(BRIEF));
    assert.deepStrictEqual(asked, [['GET', '/api/evidence/brief?project=My%20Project%20%26%20Co&phase=4']]);
});
test('a failed, odd or empty briefing leaves the prompt as it was', async () => {
    const answers = [
        () => Promise.reject(new Error('Something went wrong (500).')),
        () => { throw new Error('offline'); },
        () => Promise.resolve(null),
        () => Promise.resolve({ success: true }),
        () => Promise.resolve({ success: true, brief: Object.assign({}, BRIEF, { fact_count: 0 }) }),
    ];
    for (const call of answers) assert.strictEqual(await E.phaseBriefAddition(call, 'P', '4'), '');
    let asked = false;
    assert.strictEqual(await E.phaseBriefAddition(() => { asked = true; return Promise.resolve({}); }, '', '4'), '');
    assert.strictEqual(asked, false);   // no project: nothing to ask for
});

// ---- citation markers on the phase cards (spec section 5) ----
// A rendered write-up: a box like the card's .phase-content, holding paragraphs (text nodes) or ready-made nodes.
function para(...parts) { const p = new FakeNode('p'); parts.forEach(x => p.appendChild(typeof x === 'string' ? new FakeText(x) : x)); return p; }
function wrap(tag, ...parts) { const n = new FakeNode(tag); parts.forEach(x => n.appendChild(typeof x === 'string' ? new FakeText(x) : x)); return n; }
function writeUp(...blocks) {
    const box = new FakeNode('div');
    box.setAttribute('data-cite-phase', '1');
    blocks.forEach(b => box.appendChild(typeof b === 'string' ? para(b) : b));
    return box;
}
const citeLinks = node => all(node, n => n.tagName === 'A' && n.getAttribute('data-cite') !== null);
const PHASES = { 1: { facts: [fact({ id: 12 }), fact({ id: 13, status: 'rejected' })], conclusions: [conclusion({ id: 3 })] },
                 4: { facts: [fact({ id: 40, phase_key: '4' })], conclusions: [conclusion({ id: 9, status: 'rejected' })] } };
const INDEX = E.citationIndex(PHASES);

test('the citation index holds every phase, facts and conclusions apart', () => {
    assert.deepStrictEqual([INDEX.F[12], INDEX.F[13], INDEX.F[40], INDEX.C[3], INDEX.C[9]], ['active', 'rejected', 'active', 'active', 'rejected']);
    assert.strictEqual(INDEX.F[3], undefined);       // C3 is a conclusion, not a fact
    assert.strictEqual(E.citationIndex(undefined).F[1], undefined);
});
test('markers for this project become in-page links, and the text around them stays as it was', () => {
    const box = writeUp('Fees rose [F12] [C3] and [F40].');
    E.linkCitations(doc, box, INDEX);
    assert.deepStrictEqual(citeLinks(box).map(a => [a.textContent, a.getAttribute('href'), a.className, a.getAttribute('target')]), [
        ['[F12]', '#ev-fact-12', 'ev-cite', null], ['[C3]', '#ev-concl-3', 'ev-cite', null], ['[F40]', '#ev-fact-40', 'ev-cite', null]]);
    assert.strictEqual(box.textContent, 'Fees rose [F12] [C3] and [F40].');
});
test('a marker for an id this project does not have stays plain text', () => {
    const box = writeUp('Gone [F999], [C77] and [f12].');
    const before = box.children[0].children[0];
    E.linkCitations(doc, box, INDEX);
    assert.strictEqual(citeLinks(box).length, 0);
    assert.strictEqual(box.children[0].children[0], before);        // the text node was not touched
    assert.strictEqual(box.textContent, 'Gone [F999], [C77] and [f12].');
});
test('a marker is one to nine ASCII digits, exactly as the server reads it; anything else stays plain text', () => {
    const index = E.citationIndex({ 1: { facts: [fact({ id: 12 }), fact({ id: 123456789 }), fact({ id: 123456789012 })], conclusions: [] } });
    const box = writeUp('Wide [F１２], long [F123456789012], longest [F123456789].');
    E.linkCitations(doc, box, index);
    assert.deepStrictEqual(citeLinks(box).map(a => a.textContent), ['[F123456789]']);   // 9 digits is the most the server reads
    assert.strictEqual(box.textContent, 'Wide [F１２], long [F123456789012], longest [F123456789].');
    assert.strictEqual(E.hasCitationMarkers('[F１２]'), false);
    assert.strictEqual(E.hasCitationMarkers('[F123456789012]'), false);
    assert.strictEqual(E.hasCitationMarkers('[F123456789]'), true);
    assert.strictEqual(E.notCitedNote('Only [F123456789012] and [C３] here.'), E.NOT_CITED_NOTE);   // so the card and the Sources list agree
});
test('a marker for a rejected fact or conclusion is struck through with a Rejected tooltip', () => {
    const box = writeUp('Old [F13] and [C9], current [F12].');
    E.linkCitations(doc, box, INDEX);
    assert.deepStrictEqual(citeLinks(box).map(a => [a.textContent, a.className, a.getAttribute('title')]), [
        ['[F13]', 'ev-cite rejected', 'Rejected'], ['[C9]', 'ev-cite rejected', 'Rejected'], ['[F12]', 'ev-cite', null]]);
});
test('markers in code, and inside existing links, are left alone', () => {
    const box = writeUp(wrap('pre', wrap('code', '[F12]')), para('Inline ', wrap('code', '[F12]')), para(wrap('a', '[F12]')));
    E.linkCitations(doc, box, INDEX);
    assert.strictEqual(citeLinks(box).length, 0);
    assert.strictEqual(box.textContent, '[F12]Inline [F12][F12]');
});
test('markers inside bold, lists and tables are linked too', () => {
    const box = writeUp(para('Lead ', wrap('strong', 'Deakin [F12]')), wrap('ul', wrap('li', 'Point [C3]')), wrap('table', wrap('tr', wrap('td', '[F40]'))));
    E.linkCitations(doc, box, INDEX);
    assert.deepStrictEqual(citeLinks(box).map(a => a.textContent), ['[F12]', '[C3]', '[F40]']);
});
test('linking twice does not wrap a link again, and brings the rejected state up to date', () => {
    const box = writeUp('Fees [F12].');
    E.linkCitations(doc, box, INDEX);
    const [link] = citeLinks(box);
    const rejected = E.citationIndex({ 1: { facts: [fact({ id: 12, status: 'rejected' })], conclusions: [] } });
    E.linkCitations(doc, box, rejected);
    assert.deepStrictEqual(citeLinks(box), [link]);
    assert.deepStrictEqual([link.className, link.getAttribute('title')], ['ev-cite rejected', 'Rejected']);
    E.linkCitations(doc, box, INDEX);                       // restored
    assert.deepStrictEqual([link.className, link.getAttribute('title')], ['ev-cite', null]);
    assert.strictEqual(box.textContent, 'Fees [F12].');
});
// A link dressed up as a citation: DOMPurify keeps a data-* attribute, so a write-up can carry one.
function dressedUpLink(text, href, cite) {
    const link = wrap('a', text);
    link.setAttribute('href', href); link.setAttribute('data-cite', cite); link.className = 'theirs';
    return link;
}
test('a link that is not one of ours is never styled as a citation, whatever data-cite says', () => {
    const outside = dressedUpLink('Deakin [F12]', 'https://evil.example/fees', 'F12');
    const rejectedOutside = dressedUpLink('Old', 'https://evil.example/old', 'F13');
    const wrongAnchor = dressedUpLink('Elsewhere', '#ev-fact-13', 'F12');      // in-page, but not the anchor its data-cite names
    const wrongKind = dressedUpLink('Kind', '#ev-concl-12', 'F12');            // F12 is a fact; this is a conclusion's anchor
    const box = writeUp(para(outside), para(rejectedOutside), para(wrongAnchor), para(wrongKind));
    E.linkCitations(doc, box, INDEX);
    assert.deepStrictEqual([outside, rejectedOutside, wrongAnchor, wrongKind].map(a => [a.className, a.getAttribute('title'), a.getAttribute('href')]), [
        ['theirs', null, 'https://evil.example/fees'], ['theirs', null, 'https://evil.example/old'], ['theirs', null, '#ev-fact-13'], ['theirs', null, '#ev-concl-12']]);
    assert.strictEqual(box.textContent, 'Deakin [F12]OldElsewhereKind');   // the marker inside the outside link is not linked either
    assert.strictEqual(all(box, n => /^ev-cite/.test(n.className)).length, 0);
});
test('clicking a link that is not one of ours is left alone: the click is not stopped', () => {
    const outside = dressedUpLink('Deakin', 'https://evil.example/fees', 'F12');
    writeUp(para(outside));
    let stopped = false;
    assert.strictEqual(E.handleCitationClick(doc, { target: outside, stopPropagation: () => { stopped = true; } }), false);
    assert.strictEqual(stopped, false);
});
test('markup-looking text next to a marker stays text', () => {
    const box = writeUp('<img src=x onerror=alert(1)> [F12] <script>x</script>');
    E.linkCitations(doc, box, INDEX);
    assert.strictEqual(tagged(box, 'img').length + tagged(box, 'script').length, 0);
    assert.strictEqual(box.textContent, '<img src=x onerror=alert(1)> [F12] <script>x</script>');
    assert.strictEqual(citeLinks(box).length, 1);
});
test('a conclusion carries the anchor its markers link to', () => {
    assert.strictEqual(E.conclusionNode(doc, conclusion({ id: 3 })).getAttribute('id'), 'ev-concl-3');
});
test('a write-up with no markers gets the Not fact-cited note; one with markers, or none at all, does not', () => {
    assert.strictEqual(E.NOT_CITED_NOTE, 'Not fact-cited — written from the reports only.');
    assert.strictEqual(E.notCitedNote('Written from the reports.'), E.NOT_CITED_NOTE);
    assert.strictEqual(E.notCitedNote('Lowercase [f1] is not a marker.'), E.NOT_CITED_NOTE);
    ['Cited [F1].', 'Concluded [C2].', '', '   ', 'MISSING', null, undefined, 42].forEach(summary =>
        assert.strictEqual(E.notCitedNote(summary), '', String(summary)));
});
test('clicking a marker stops the click reaching the phase card and opens the fold its fact sits in', async () => {
    const box = writeUp('Old fact [F3].');
    const page = makePage(['1'], [box]); const env = makeEnv(page); const screen = E.createController(env);
    screen.renderAll('P');
    env.requests[0].resolve({ success: true, phases: { 1: { facts: manyFacts(25).map(f => Object.assign(f, { phase_key: '1' })), conclusions: [] } } });
    await flush();
    const [link] = citeLinks(box);
    let stopped = false;
    assert.strictEqual(E.handleCitationClick(page.document, { target: link, stopPropagation: () => { stopped = true; } }), true);
    assert.strictEqual(stopped, true);
    assert.strictEqual(byClass(page.slots[0], 'ev-more')[0].open, true);
    let other = false;
    assert.strictEqual(E.handleCitationClick(page.document, { target: box.children[0], stopPropagation: () => { other = true; } }), false);
    assert.strictEqual(other, false);                       // any other click on the card still opens the editor
});
test('the controller links the cards once the facts load, and re-marks them after a reject', async () => {
    const box = writeUp('Fees [F12] and [C3].');
    const page = makePage(['1'], [box]); const env = makeEnv(page); const screen = E.createController(env);
    screen.renderAll('P');
    assert.strictEqual(citeLinks(box).length, 0);           // nothing to link to until the anchors exist
    env.requests[0].resolve({ success: true, phases: PHASES }); await flush();
    assert.deepStrictEqual(citeLinks(box).map(a => a.className), ['ev-cite', 'ev-cite']);
    const rejected = JSON.parse(JSON.stringify(PHASES)); rejected[1].facts[0].status = 'rejected';
    screen.renderAll('P');
    env.requests[1].resolve({ success: true, phases: rejected }); await flush();
    assert.deepStrictEqual(citeLinks(box).map(a => a.className), ['ev-cite rejected', 'ev-cite']);
});
test('a past Insights version (no slots) or a failed load leaves the markers as plain text', async () => {
    const past = writeUp('Fees [F12].');
    const pastPage = makePage([], [past]); const pastEnv = makeEnv(pastPage);
    E.createController(pastEnv).renderAll('P');
    assert.strictEqual(pastEnv.requests.length, 0);
    assert.strictEqual(citeLinks(past).length, 0);
    const box = writeUp('Fees [F12].');
    const page = makePage(['1'], [box]); const env = makeEnv(page);
    E.createController(env).renderAll('P');
    env.requests[0].reject(new Error('Something went wrong (500).')); await flush();
    assert.strictEqual(citeLinks(box).length, 0);
});

// ---- the phase editor's save (Turndown writes \[F12\]; the saved text must keep [F12]) ----

test('escaped citation markers come back as markers', () => {
    assert.strictEqual(E.unescapeCitationMarkers('Fees rose \\[F12\\] and \\[C4\\].'), 'Fees rose [F12] and [C4].');
    assert.strictEqual(E.unescapeCitationMarkers('\\[F1\\]'), '[F1]');
    assert.strictEqual(E.unescapeCitationMarkers('Up \\[F123456789\\]'), 'Up [F123456789]');       // nine digits is the most
});
test('several escaped markers in a row, in lists and in bold, all come back', () => {
    assert.strictEqual(E.unescapeCitationMarkers('Claim \\[F3\\] \\[F12\\]\\[C4\\] \\[F3\\]'), 'Claim [F3] [F12][C4] [F3]');
    assert.strictEqual(E.unescapeCitationMarkers('*   Point \\[F7\\]\n\n**Bold \\[C2\\]**'), '*   Point [F7]\n\n**Bold [C2]**');
});
test('an unescaped marker, and text with no markers, are returned as they were', () => {
    assert.strictEqual(E.unescapeCitationMarkers('Already [F12] fine.'), 'Already [F12] fine.');
    assert.strictEqual(E.unescapeCitationMarkers(''), '');
    assert.strictEqual(E.unescapeCitationMarkers('Plain text.'), 'Plain text.');
});
test('escaped brackets that are not a marker stay escaped', () => {
    const same = [
        '\\[link\\](x)', '\\[F\\]', '\\[f12\\]', '\\[X1\\]', '\\[F12', 'F12\\]', '\\[F1x\\]',
        '\\[F1234567890\\]',            // ten digits: not an id
        '\\[F\uff11\uff12\\]',          // fullwidth digits
        '\\[F\u0663\\]',                // Arabic-Indic digit
        '\\[F-1\\]', '\\[F 12\\]', '\\[F12 \\]', '\\[F12\\\\]',
    ];
    same.forEach(text => assert.strictEqual(E.unescapeCitationMarkers(text), text, text));
    assert.strictEqual(E.unescapeCitationMarkers('1\\[2\\] and \\[F12\\]'), '1\\[2\\] and [F12]');   // only the marker changes
});
test('what Turndown 7.1.2 writes for a card with markers comes back as the markers it was', () => {
    // Strings captured from turndown@7.1.2 (the version index.html loads), run on the editor's HTML.
    const written = 'Fees rose \\[F3\\] and \\[F12\\] \\[C4\\]. A \\[link\\](x) and 1\\[2\\].';
    assert.strictEqual(E.unescapeCitationMarkers(written), 'Fees rose [F3] and [F12] [C4]. A \\[link\\](x) and 1\\[2\\].');
});
test('odd input is returned as an empty string, not an error', () => {
    [null, undefined, 42, {}, []].forEach(x => assert.strictEqual(E.unescapeCitationMarkers(x), ''));
});
test('it undoes exactly what the card reads back: the restored marker is one the card links', () => {
    const restored = E.unescapeCitationMarkers('See \\[F12\\].');
    assert.strictEqual(E.hasCitationMarkers('See \\[F12\\].'), false);   // the escaped form is what broke the card
    assert.strictEqual(E.hasCitationMarkers(restored), true);
});

(async () => {
let failed = 0;
for (const [name, fn] of tests) {
    try { await fn(); console.log('ok -', name); } catch (e) { failed++; console.log('FAIL -', name, '\n ', e.message); }
}
if (failed) { console.log(`${failed} failed`); process.exit(1); }
console.log(`all ${tests.length} passed`);
})();
