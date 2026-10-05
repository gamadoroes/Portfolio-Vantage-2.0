// Run: node tests/js/test_board_view.js  (also run by tests/test_board_js.py)
const assert = require('assert');
const B = require('../../static/board.js');

function card(over) {
    return Object.assign({
        id: 7, phase_key: '4', title: 'Fees', status: 'PROPOSED', research_method: 'TARGETED_WEB',
        method_label: 'Web research', framework_key: 'oes-product-features', framework_label: 'Phase 4: Product Features',
        prompt_text: 'Research the fees of every provider.', needs_prompt_edit: false, focus: ['Fees'],
        rationale: 'Price drives choice', priority: null, suggested_from: null, followups: [], depends_on: [],
        completeness_score: null, evidence_score: null, gaps: [], human_review_required: false,
        retry_count: 0, max_retries: 3, run: null, review_error: null,
        can_extract_facts: false,
    }, over || {});
}
function state(cards, over) {
    return Object.assign({
        project: 'P', objective: 'Assess the market.',
        phases: ['The Landscape', 'The Student', 'Review of Marketing', 'Product Features', 'Academic Content',
                 'Industry Engagement', 'Options for OES'].map((t, i) => ({ key: String(i + 1), title: t, frameworks: [] })),
        cards, phase7: { ready_count: 2, unlocked: false, summaries_stale: false }, activity: [],
    }, over || {});
}
const view = () => B.newView();
const tests = [];
const test = (name, fn) => tests.push([name, fn]);

test('escapes titles', () => {
    const html = B.cardHtml(card({ title: '<script>x</script>' }), state([]), view());
    assert(!html.includes('<script>x'));
    assert(html.includes('&lt;script&gt;'));
});
test('draft card offers edit-and-approve and skip', () => {
    const html = B.cardHtml(card(), state([]), view());
    assert(html.includes('Needs your approval'));
    assert(html.includes('data-act="edit"') && html.includes('Edit and approve'));
    assert(html.includes('data-act="skip"'));
});
test('placeholder prompt is flagged', () => {
    assert(B.cardHtml(card({ needs_prompt_edit: true }), state([]), view()).includes('Edit the prompt before approving'));
});
test('editing shows the form with the exact prompt', () => {
    const v = view(); v.editing[7] = true;
    const html = B.cardHtml(card(), state([]), v);
    assert(html.includes('data-field="prompt_text"'));
    assert(html.includes('This text is exactly what gets sent'));
    assert(html.includes('Drafted from the Phase 4: Product Features framework'));
    assert(html.includes('data-act="approve"'));
});
test('approved card can go back to draft', () => {
    const html = B.cardHtml(card({ status: 'READY' }), state([]), view());
    assert(html.includes('Approved, waiting for Run') && html.includes('data-act="unapprove"'));
});
test('running card shows elapsed time, not a percentage', () => {
    const html = B.cardHtml(card({ status: 'RUNNING', run: { elapsed_seconds: 360 } }), state([]), view());
    assert(html.includes('6 min'));
    assert(!html.includes('%'));
});
test('finished card shows the review', () => {
    const html = B.cardHtml(card({ status: 'COMPLETE', completeness_score: 0.8, evidence_score: 0.7, gaps: ['No dates'],
                                  run: { has_report: true, report_filename: 'Research P4 - Fees (2026-10-05).md' } }), state([]), view());
    assert(html.includes('0.80') && html.includes('0.70') && html.includes('No dates'));
    assert(html.includes('data-act="report"'));
    assert(html.includes('Research P4 - Fees (2026-10-05).md'));
});
test('needs-your-review card offers three choices', () => {
    const html = B.cardHtml(card({ status: 'WAITING_FOR_HUMAN' }), state([]), view());
    ['accept', 'needs-followup', 'mark-failed'].forEach(a => assert(html.includes(`data-act="${a}"`)));
});
test('retry is disabled at the retry limit', () => {
    const html = B.cardHtml(card({ status: 'FAILED', retry_count: 3, max_retries: 3, run: { error: 'OpenAI is down' } }), state([]), view());
    assert(/data-act="retry"[^>]*disabled/.test(html));
    assert(html.includes('OpenAI is down'));
});
test('follow-up says where it came from', () => {
    assert(B.cardHtml(card({ suggested_from: { id: 1, title: 'Overview' } }), state([]), view()).includes('Suggested after reviewing'));
});
test('chips hide review and failed when empty', () => {
    const html = B.chipsHtml([card()], view());
    assert(!html.includes('Needs your review') && !html.includes('>Failed'));
    assert(B.chipsHtml([card({ status: 'FAILED' })], view()).includes('Failed'));
});
test('filters', () => {
    assert(B.filterMatches(card({ status: 'REVIEWING' }), 'running'));
    assert(B.filterMatches(card({ status: 'FOLLOW_UP_REQUIRED' }), 'finished'));
    assert(!B.filterMatches(card({ status: 'SKIPPED' }), 'all'));
});
test('phase 7 lock shows progress', () => {
    const s = state([]);
    assert(B.phaseHtml(s.phases[6], s, view()).includes('2 of 6'));
});
test('phase 7 unlocked offers the options report and warns when stale', () => {
    const s = state([], { phase7: { ready_count: 6, unlocked: true, summaries_stale: true } });
    const html = B.phaseHtml(s.phases[6], s, view());
    assert(html.includes('data-act="draft-options"'));
    assert(html.includes('Refresh Insights first'));
});
test('run box is disabled with nothing approved', () => {
    assert(/data-act="run-open"[^>]*disabled/.test(B.runBoxHtml(state([card()]), view())));
    assert(B.runBoxHtml(state([card({ status: 'READY' })]), view()).includes('Run 1 approved research'));
});
test('confirm warns about OpenAI billing for web research', () => {
    const v = view(); v.confirming = true;
    const html = B.runBoxHtml(state([card({ status: 'READY' })]), v);
    assert(html.includes('bills your OpenAI account') && html.includes('data-act="run-start"'));
});
test('payload helpers', () => {
    assert.deepStrictEqual(B.editPayload({ title: 'T', focus: 'a, , b ' }), { title: 'T', focus: ['a', 'b'] });
    const add = B.addPayload('P', { phase_key: '2', title: ' Personas ', focus: 'x' });
    assert.deepStrictEqual(add, { project: 'P', phase_key: '2', title: 'Personas', research_method: 'TARGETED_WEB',
        focus: ['x'], rationale: '', draft_prompt: true, framework_key: null, prompt_text: '' });
});
test('cardEdits lists only fields that really change', () => {
    const c = card({ status: 'READY' });
    assert.deepStrictEqual(B.cardEdits(c, undefined), []);
    assert.deepStrictEqual(B.cardEdits(c, {}), []);
    // Typed and put back, or only whitespace the server trims: no change.
    assert.deepStrictEqual(B.cardEdits(c, { title: ' Fees ', prompt_text: c.prompt_text, focus: 'Fees, ',
                                            rationale: 'Price drives choice ', research_method: 'TARGETED_WEB' }), []);
    assert.deepStrictEqual(B.cardEdits(c, { title: 'Fees 2026' }), ['title']);
    assert.deepStrictEqual(B.cardEdits(c, { prompt_text: c.prompt_text + ' ' }), ['prompt_text']);
    assert.deepStrictEqual(B.cardEdits(c, { focus: 'Fees, FEE-HELP' }), ['focus']);
    assert.deepStrictEqual(B.cardEdits(c, { research_method: 'FILE_ANALYSIS' }), ['research_method']);
    assert.deepStrictEqual(B.cardEdits(card({ rationale: null }), { rationale: '  ' }), []);
});
test('objective editor shows the unsaved draft, escaped, in preference to the saved objective', () => {
    const v = view(); v.objEdit = true;
    assert(B.objectiveHtml(state([]), v).includes('>Assess the market.</textarea>'));
    v.objDraft = 'Half <typed> & more';
    const html = B.objectiveHtml(state([]), v);
    assert(html.includes('>Half &lt;typed&gt; &amp; more</textarea>') && !html.includes('Assess the market.'));
});
test('whole board renders', () => {
    const html = B.renderBoard(state([card(), card({ id: 8, status: 'SKIPPED', title: 'Old' })]), view());
    assert(html.includes('Research plan') && html.includes('Draft next researches') && html.includes('Skipped (1)'));
});

test('a finished card with no facts yet offers Extract facts, warning that it is a paid call', () => {
    const c = card({ status: 'COMPLETE', can_extract_facts: true, run: { has_report: true } });
    assert(B.cardHtml(c, state([]), view()).includes('data-act="extract-open"'));
    const v = view(); v.confirmExtract = 7;
    const html = B.cardHtml(c, state([]), v);
    assert(html.includes('one paid Claude call') && html.includes('data-act="extract-start"') && html.includes('data-act="extract-cancel"'));
    v.extracting[7] = true;
    const busy = B.cardHtml(c, state([]), v);
    assert(busy.includes('Extracting facts') && !busy.includes('data-act="extract-start"'));
});
test('no Extract facts once the facts were taken', () => {
    const html = B.cardHtml(card({ status: 'COMPLETE', can_extract_facts: false, run: { has_report: true } }), state([]), view());
    assert(!html.includes('extract-open'));
});
test('a card waiting for your review can extract facts too', () => {
    assert(B.cardHtml(card({ status: 'WAITING_FOR_HUMAN', can_extract_facts: true }), state([]), view()).includes('data-act="extract-open"'));
});
test('the report reader shows Extract facts only when facts can still be taken', () => {
    const shown = B.readerHeadHtml({ id: 7, title: 'Fees', can_extract_facts: true });
    assert(shown.includes('data-reader-extract') && shown.includes('data-reader-close'));
    const hidden = B.readerHeadHtml({ id: 7, title: 'Fees', can_extract_facts: false });
    assert(!hidden.includes('data-reader-extract') && hidden.includes('data-reader-close'));
    assert(!B.readerHeadHtml({ id: 7, title: 'Fees' }).includes('data-reader-extract'));
});
test('the report reader escapes the title', () => {
    const html = B.readerHeadHtml({ id: 7, title: '<img src=x onerror=alert(1)>', can_extract_facts: true });
    assert(!html.includes('<img') && html.includes('&lt;img'));
});

test('a poll re-render while facts are being extracted keeps the Extracting placeholder', () => {
    // the server has already claimed the report, so it reports can_extract_facts false while the call runs
    const c = card({ status: 'COMPLETE', can_extract_facts: false, run: { has_report: true } });
    const v = view(); v.extracting[7] = true;
    const html = B.cardHtml(c, state([]), v);
    assert(html.includes('Extracting facts') && !html.includes('data-act="extract-open"'));
    assert(!B.cardHtml(c, state([]), view()).includes('Extracting facts'));
});
test('the reader keeps its Extract facts button unless facts were actually saved', () => {
    assert(B.extractSaved({ result: { fact_ids: [4, 5], conclusion_ids: [] } }) === true);
    assert(B.extractSaved({ result: { fact_ids: [], conclusion_ids: [] } }) === false);
    assert(B.extractSaved({ result: {} }) === false);
    assert(B.extractSaved({}) === false);
    assert(B.extractSaved(null) === false);
});

let failed = 0;
for (const [name, fn] of tests) {
    try { fn(); console.log('ok -', name); } catch (e) { failed++; console.log('FAIL -', name, '\n ', e.message); }
}
if (failed) { console.log(`${failed} failed`); process.exit(1); }
console.log(`all ${tests.length} passed`);
