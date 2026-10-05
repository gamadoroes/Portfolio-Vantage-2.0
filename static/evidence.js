// static/evidence.js
// Facts and conclusions on the Insights tab (below each phase write-up), and the fact search box.
// Everything is built as DOM nodes: AI and web text is only ever set with textContent, and links are made
// only for http/https addresses. The builders take the document as an argument so the Node tests in
// tests/js/test_evidence_view.js can run them against a small fake DOM.
(function () {
    'use strict';

    function safeUrl(url) {
        if (typeof url !== 'string') return null;
        const trimmed = url.trim();
        return /^https?:\/\/\S/i.test(trimmed) ? trimmed : null;
    }
    function plural(n, one, many) { return `${n} ${n === 1 ? one : many}`; }
    function el(doc, tag, cls, text) {
        const node = doc.createElement(tag);
        if (cls) node.className = cls;
        if (text != null) node.textContent = String(text);
        return node;
    }
    function actionButton(doc, kind, item) {
        const act = item.status === 'rejected' ? 'restore' : 'reject';
        const button = el(doc, 'button', 'ev-btn', act === 'reject' ? 'Reject' : 'Restore');
        button.setAttribute('type', 'button');
        button.setAttribute('data-ev-act', act);
        button.setAttribute('data-ev-kind', kind);
        button.setAttribute('data-ev-id', String(Number(item.id)));
        return button;
    }
    function sourceNode(doc, source) {
        if (!source) return el(doc, 'span', 'ev-src', 'Source: the research report');
        const label = source.title || source.url || 'Source';
        const href = safeUrl(source.url);
        if (!href) return el(doc, 'span', 'ev-src', label);
        const link = el(doc, 'a', 'ev-src', label);
        link.setAttribute('href', href);
        link.setAttribute('target', '_blank');
        link.setAttribute('rel', 'noopener noreferrer');
        return link;
    }
    // In search results a fact shows its phase and has no button and no anchor id (ids stay unique on the page).
    function factNode(doc, fact, inSearch) {
        const item = el(doc, 'li', fact.status === 'rejected' ? 'ev-fact rejected' : 'ev-fact');
        if (!inSearch) item.setAttribute('id', `ev-fact-${Number(fact.id)}`);
        const head = el(doc, 'div', 'ev-head');
        head.appendChild(el(doc, 'span', 'ev-num', inSearch ? `Phase ${fact.phase_key}` : `#${Number(fact.id)}`));
        head.appendChild(el(doc, 'span', 'ev-claim', fact.claim));
        item.appendChild(head);
        if (fact.quote) item.appendChild(el(doc, 'blockquote', 'ev-quote', fact.quote));
        const meta = el(doc, 'div', 'ev-meta');
        meta.appendChild(sourceNode(doc, fact.source));
        if (fact.as_of) meta.appendChild(el(doc, 'span', 'ev-asof', `As of ${fact.as_of}`));
        if (fact.card_title) meta.appendChild(el(doc, 'span', 'ev-from', `From: ${fact.card_title}`));
        if (!inSearch) meta.appendChild(actionButton(doc, 'fact', fact));
        item.appendChild(meta);
        return item;
    }
    function basedOnNode(doc, conclusion) {
        const ids = (conclusion.fact_ids || []).map(Number);
        const line = el(doc, 'p', 'ev-based', ids.length === 1 ? 'Based on fact ' : 'Based on facts ');
        ids.forEach((id, i) => {
            if (i) line.appendChild(el(doc, 'span', null, ', '));
            const ref = el(doc, 'a', 'ev-ref', String(id));
            ref.setAttribute('href', `#ev-fact-${id}`);
            line.appendChild(ref);
        });
        const rejected = Number(conclusion.rejected_fact_count) || 0;
        if (rejected) {
            line.appendChild(el(doc, 'span', 'ev-warn',
                rejected === 1 ? ' · 1 of its facts was rejected' : ` · ${rejected} of its facts were rejected`));
        }
        return line;
    }
    function conclusionNode(doc, conclusion) {
        const item = el(doc, 'li', conclusion.status === 'rejected' ? 'ev-concl rejected' : 'ev-concl');
        item.appendChild(el(doc, 'p', 'ev-text', conclusion.text));
        const meta = el(doc, 'div', 'ev-meta');
        meta.appendChild(basedOnNode(doc, conclusion));
        meta.appendChild(actionButton(doc, 'conclusion', conclusion));
        item.appendChild(meta);
        return item;
    }
    function listNode(doc, items, build) {
        const list = el(doc, 'ul', 'ev-list');
        items.forEach(x => list.appendChild(build(doc, x)));
        return list;
    }
    const pageFact = (doc, fact) => factNode(doc, fact, false);

    // Whether each phase's "Show rejected" box is open. Kept per phase, outside the DOM, so a reject/restore re-render
    // (which rebuilds the nodes) leaves the box as the person had it.
    function createOpenState() {
        const open = {};
        return { get: phase => open[phase] === true, set: (phase, value) => { open[phase] = value === true; } };
    }

    function phaseEvidenceNode(doc, data, rejectedOpen) {
        const facts = (data && data.facts) || [];
        const conclusions = (data && data.conclusions) || [];
        const wrap = el(doc, 'section', 'ev-phase');
        wrap.setAttribute('aria-label', 'Facts and conclusions');
        if (!facts.length && !conclusions.length) {
            wrap.appendChild(el(doc, 'p', 'ev-empty', 'No facts yet. They are saved here when finished research for this phase is reviewed.'));
            return wrap;
        }
        const live = list => list.filter(x => x.status !== 'rejected');
        const gone = list => list.filter(x => x.status === 'rejected');
        if (live(conclusions).length) {
            wrap.appendChild(el(doc, 'h4', null, 'Conclusions'));
            wrap.appendChild(listNode(doc, live(conclusions), conclusionNode));
        }
        if (live(facts).length) {
            wrap.appendChild(el(doc, 'h4', null, `Facts (${live(facts).length})`));
            wrap.appendChild(listNode(doc, live(facts), pageFact));
        }
        const hidden = gone(conclusions).length + gone(facts).length;
        if (hidden) {
            const box = el(doc, 'details', 'ev-rejected');
            if (rejectedOpen) box.setAttribute('open', '');
            box.appendChild(el(doc, 'summary', null, `Show rejected (${hidden})`));
            if (gone(conclusions).length) box.appendChild(listNode(doc, gone(conclusions), conclusionNode));
            if (gone(facts).length) box.appendChild(listNode(doc, gone(facts), pageFact));
            wrap.appendChild(box);
        }
        return wrap;
    }
    function searchResultsNode(doc, facts, words) {
        const wrap = el(doc, 'div', 'ev-results');
        if (!facts.length) {
            wrap.appendChild(el(doc, 'p', 'ev-empty', `No facts match “${words}”.`));
            return wrap;
        }
        wrap.appendChild(el(doc, 'p', 'ev-count', plural(facts.length, 'fact matches', 'facts match')));
        wrap.appendChild(listNode(doc, facts, (d, f) => factNode(d, f, true)));
        return wrap;
    }

    function clear(node) { while (node.firstChild) node.removeChild(node.firstChild); }

    // The screen's behaviour, with the document, the fetch helper and the timers passed in so the Node tests can
    // drive it with fakes. Every async step checks that the project (and, for search, the search itself) is still
    // the one it started for before it paints anything.
    function createController(env) {
        const doc = env.document;
        const ev = { project: null, seq: 0, searchSeq: 0, timer: null, rejectedOpen: createOpenState() };
        const slots = () => Array.from(doc.querySelectorAll('[data-evidence-phase]'));

        function resetSearch() {
            env.clearTimeout(ev.timer);  // a pending debounce must not fire against whatever project comes next
            ev.searchSeq++;              // and a search already in flight must not paint when it comes back
            const input = doc.getElementById('evidence-search-input');
            const out = doc.getElementById('evidence-search-results');
            if (input) input.value = '';
            if (out) clear(out);
        }

        async function renderAll(project) {
            if (project !== ev.project) { ev.project = project; ev.rejectedOpen = createOpenState(); resetSearch(); }
            const seq = ++ev.seq;
            if (!project || !slots().length) return;
            let data;
            try {
                data = await env.call('GET', `/api/evidence?project=${encodeURIComponent(project)}`);
            } catch (err) {
                if (seq !== ev.seq) return;
                slots().forEach(slot => { clear(slot); slot.appendChild(el(doc, 'p', 'ev-err', err.message)); });
                return;
            }
            if (seq !== ev.seq || project !== ev.project) return;
            slots().forEach(slot => {
                clear(slot);
                const phase = slot.getAttribute('data-evidence-phase');
                slot.appendChild(phaseEvidenceNode(doc, data.phases[phase], ev.rejectedOpen.get(phase)));
            });
        }

        async function runSearch(words) {
            const out = doc.getElementById('evidence-search-results');
            if (!out) return;
            const seq = ++ev.searchSeq;
            const project = ev.project;
            if (!project || !words.trim()) { clear(out); return; }
            let node;
            try {
                const data = await env.call('GET', `/api/evidence/search?project=${encodeURIComponent(project)}&q=${encodeURIComponent(words)}`);
                node = searchResultsNode(doc, data.facts || [], words);
            } catch (err) {
                node = el(doc, 'p', 'ev-err', err.message);
            }
            if (seq !== ev.searchSeq || project !== ev.project) return;
            clear(out);
            out.appendChild(node);
        }

        function onSearchInput(words) {
            env.clearTimeout(ev.timer);
            ev.timer = env.setTimeout(() => runSearch(words), 300);
        }

        // A Reject or Restore button was clicked. One error message at a time: a retry replaces the last one.
        async function onAction(button) {
            if (!button || button.disabled || !ev.project) return;
            const project = ev.project;
            button.disabled = true;
            try {
                await env.call('POST', `/api/evidence/${button.getAttribute('data-ev-kind')}/${button.getAttribute('data-ev-id')}/${button.getAttribute('data-ev-act')}`, { project });
                if (project === ev.project) renderAll(project);
            } catch (err) {
                button.disabled = false;
                const parent = button.parentNode;
                if (parent) Array.from(parent.children).filter(c => c.className === 'ev-err').forEach(c => parent.removeChild(c));
                button.after(el(doc, 'span', 'ev-err', err.message));
            }
        }

        function rememberRejectedBox(phase, open) { ev.rejectedOpen.set(phase, open); }

        return { renderAll, runSearch, resetSearch, onSearchInput, onAction, rememberRejectedBox };
    }

    const pure = { safeUrl, factNode, conclusionNode, phaseEvidenceNode, searchResultsNode, createOpenState, createController };
    if (typeof module !== 'undefined' && module.exports) { module.exports = pure; return; }

    // ---------------- browser glue ----------------
    async function call(method, url, body) {
        const options = { method };
        if (body !== undefined) { options.headers = { 'Content-Type': 'application/json' }; options.body = JSON.stringify(body); }
        const res = await fetch(url, options);
        let data = null;
        try { data = await res.json(); } catch (e) { data = null; }
        if (!res.ok || !data || data.success === false) throw new Error((data && data.error) || `Something went wrong (${res.status}).`);
        return data;
    }
    const screen = createController({
        document, call,
        setTimeout: (fn, ms) => window.setTimeout(fn, ms),
        clearTimeout: id => window.clearTimeout(id),
    });

    document.addEventListener('input', event => {
        if (event.target && event.target.id === 'evidence-search-input') screen.onSearchInput(event.target.value);
    });

    // "toggle" does not bubble, so listen in the capture phase to remember each phase's Show rejected box.
    document.addEventListener('toggle', event => {
        const box = event.target;
        if (!box || !box.classList || !box.classList.contains('ev-rejected')) return;
        const slot = box.closest('[data-evidence-phase]');
        if (slot) screen.rememberRejectedBox(slot.getAttribute('data-evidence-phase'), box.open);
    }, true);

    document.addEventListener('click', event => {
        const target = event.target;
        if (!target || !target.closest) return;
        const ref = target.closest('a.ev-ref');
        if (ref) {  // open "Show rejected" if the fact is in there; the browser then follows the #anchor
            const factEl = document.getElementById((ref.getAttribute('href') || '').slice(1));
            const box = factEl && factEl.closest('details');
            if (box) box.open = true;
            return;
        }
        const button = target.closest('[data-ev-act]');
        if (button) screen.onAction(button);
    });

    window.evidenceRenderAll = screen.renderAll;
    window.evidenceResetSearch = screen.resetSearch;
})();
