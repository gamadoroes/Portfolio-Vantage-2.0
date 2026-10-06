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
        item.setAttribute('id', `ev-concl-${Number(conclusion.id)}`);  // the target of a [C#] marker on a phase card
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

    // Facts are shown newest first, and only this many at a time: the rest are folded under "Show n more".
    const FACTS_SHOWN = 10;

    // A link to a fact that is folded away ("Show rejected", "Show n more") opens every folded box the fact sits in,
    // so the browser's jump to the #anchor lands on something visible.
    function openFoldedBoxes(node) {
        for (let box = node && node.parentNode; box; box = box.parentNode) {
            if (box.tagName && box.tagName.toUpperCase() === 'DETAILS') box.open = true;
        }
    }

    // Whether each phase's "Show rejected" box (and, in a second map, its "Show n more" box) is open. Kept per phase,
    // outside the DOM, so a reject/restore re-render (which rebuilds the nodes) leaves the box as the person had it.
    function createOpenState() {
        const open = {};
        return { get: phase => open[phase] === true, set: (phase, value) => { open[phase] = value === true; } };
    }

    function phaseEvidenceNode(doc, data, rejectedOpen, moreOpen) {
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
            const newestFirst = live(facts).slice().sort((a, b) => Number(b.id) - Number(a.id));
            wrap.appendChild(el(doc, 'h4', null, `Facts (${newestFirst.length})`));
            wrap.appendChild(listNode(doc, newestFirst.slice(0, FACTS_SHOWN), pageFact));
            if (newestFirst.length > FACTS_SHOWN) {
                const older = newestFirst.slice(FACTS_SHOWN);
                const more = el(doc, 'details', 'ev-more');
                if (moreOpen) more.setAttribute('open', '');
                more.appendChild(el(doc, 'summary', null, `Show ${older.length} more`));
                more.appendChild(listNode(doc, older, pageFact));
                wrap.appendChild(more);
            }
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

    // ---- Generate: the phase's facts briefing (spec section 4) ----
    // The sentence "part of your source data" is there because the prompt says to use ONLY the Source Data files.
    const BRIEF_INTRO = 'FACTS BRIEFING — the checked facts and conclusions saved for this phase. This briefing is part of your source data, so its facts may be used and cited. Build the write-up on these first and cite them as the rules below say; use the attached source files for colour and context.';

    // What Generate adds to a phase's prompt: the briefing when the phase has checked facts, or has rejected ones
    // the briefing says not to use; otherwise nothing, so a phase with neither is written exactly as before.
    function briefPromptAddition(brief) {
        if (!brief || typeof brief.text !== 'string' || !brief.text.trim()) return '';
        if (!(Number(brief.fact_count) > 0) && !(Number(brief.rejected_count) > 0)) return '';
        return `\n\n${BRIEF_INTRO}\n\n${brief.text.trim()}`;
    }
    // Fetches the phase's briefing and returns the prompt addition. Any failure returns '', so Generate never
    // fails because of the facts.
    async function phaseBriefAddition(call, project, phaseKey) {
        if (!project) return '';
        try {
            const data = await call('GET', `/api/evidence/brief?project=${encodeURIComponent(project)}&phase=${encodeURIComponent(phaseKey)}`);
            return briefPromptAddition(data && data.brief);
        } catch (err) {
            return '';
        }
    }

    // ---- Citation markers on the phase cards (spec section 5) ----
    const NOT_CITED_NOTE = 'Not fact-cited — written from the reports only.';

    // A marker is [F<id>] or [C<id>] with one to nine ASCII digits: the same rule as MARKER_RE in
    // services/evidence_service.py, so the card links and the Word report's Sources list always agree.
    function hasCitationMarkers(text) { return typeof text === 'string' && /\[([FC])([0-9]{1,9})\]/.test(text); }
    // The note a phase card shows above a write-up that cites no facts ('' when it needs none).
    function notCitedNote(summary) {
        const text = typeof summary === 'string' ? summary.trim() : '';
        return text && text !== 'MISSING' && !hasCitationMarkers(text) ? NOT_CITED_NOTE : '';
    }
    // {F: {id: status}, C: {id: status}} for every fact and conclusion of the project, from the same /api/evidence
    // answer that fills the phase slots. A phase card may cite another phase's facts (Phase 7 does).
    function citationIndex(phases) {
        const index = { F: Object.create(null), C: Object.create(null) };
        Object.keys(phases || {}).forEach(key => {
            const phase = phases[key] || {};
            (phase.facts || []).forEach(f => { index.F[Number(f.id)] = f.status; });
            (phase.conclusions || []).forEach(c => { index.C[Number(c.id)] = c.status; });
        });
        return index;
    }
    const citeHref = (kind, id) => `#${kind === 'F' ? 'ev-fact' : 'ev-concl'}-${id}`;
    function setCiteState(link, status) {
        if (status === 'rejected') {
            link.className = 'ev-cite rejected';
            link.setAttribute('title', 'Rejected');
        } else {
            link.className = 'ev-cite';
            link.removeAttribute('title');
        }
    }
    function citeLink(doc, kind, id, label, status) {
        const link = el(doc, 'a', null, label);
        link.setAttribute('href', citeHref(kind, id));
        link.setAttribute('data-cite', `${kind}${id}`);
        setCiteState(link, status);
        return link;
    }
    // Whether an <a> is a citation link of ours: its data-cite names a marker and its href is exactly that marker's
    // in-page anchor. Any other link (say an outside one that came with a data-cite attribute, which the sanitiser
    // keeps) is not a citation and is never restyled or treated as one. Returns {kind, id} or null.
    function ownCitation(node) {
        const cite = /^([FC])([0-9]{1,9})$/.exec(node.getAttribute('data-cite') || '');
        if (!cite) return null;
        const kind = cite[1];
        const id = Number(cite[2]);
        return cite[0] === `${kind}${id}` && node.getAttribute('href') === citeHref(kind, id) ? { kind, id } : null;
    }
    // One text node: each marker for a fact or conclusion this project has becomes an in-page link; a marker for
    // an id the project does not have stays plain text.
    function linkTextNode(doc, node, index) {
        const value = node.nodeValue || '';
        const markers = /\[([FC])([0-9]{1,9})\]/g;
        const parts = [];
        let last = 0;
        let match;
        while ((match = markers.exec(value)) !== null) {
            const id = Number(match[2]);
            const status = index[match[1]][id];
            if (status === undefined) continue;
            if (match.index > last) parts.push(doc.createTextNode(value.slice(last, match.index)));
            parts.push(citeLink(doc, match[1], id, match[0], status));
            last = match.index + match[0].length;
        }
        if (!parts.length) return;
        if (last < value.length) parts.push(doc.createTextNode(value.slice(last)));
        const parent = node.parentNode;
        parts.forEach(part => parent.insertBefore(part, node));
        parent.removeChild(node);
    }
    // Walks a rendered (and sanitised) write-up. Code and existing links are left alone. A marker linked by an
    // earlier pass (the card is not rebuilt when a fact is rejected or restored) only has its rejected state updated.
    function linkCitations(doc, root, index) {
        Array.from(root.childNodes || []).forEach(node => {
            if (node.nodeType === 3) { linkTextNode(doc, node, index); return; }
            if (node.nodeType !== 1) return;
            const tag = String(node.tagName || '').toUpperCase();
            if (tag === 'A') {
                const own = ownCitation(node);
                const status = own ? index[own.kind][own.id] : undefined;
                if (status !== undefined) setCiteState(node, status);
                return;
            }
            if (tag === 'CODE' || tag === 'PRE') return;
            linkCitations(doc, node, index);
        });
    }
    // The phase editor turns its HTML back into Markdown with Turndown, which escapes every square bracket: [F12]
    // comes out as \[F12\]. A marker written that way is no marker (not on the card, not in the Word report's
    // Sources), so the editor's save puts the brackets of real markers back (digits 1-9 only, as everywhere else).
    // Every other escaped bracket is left as Turndown wrote it.
    function unescapeCitationMarkers(markdown) {
        if (typeof markdown !== 'string') return '';
        return markdown.replace(/\\\[([FC])([0-9]{1,9})\\\]/g, '[$1$2]');
    }
    function citationFromTarget(node) {
        for (let n = node; n; n = n.parentNode) {
            if (n.nodeType === 1 && String(n.tagName || '').toUpperCase() === 'A' && ownCitation(n)) return n;
        }
        return null;
    }
    // A click on a marker: keep it from reaching the phase card (whose own click opens the editor) and open the
    // folded box its fact or conclusion sits in. The browser then follows the #anchor.
    function handleCitationClick(doc, event) {
        const link = citationFromTarget(event && event.target);
        if (!link) return false;
        event.stopPropagation();
        openFoldedBoxes(doc.getElementById((link.getAttribute('href') || '').slice(1)));
        return true;
    }

    function clear(node) { while (node.firstChild) node.removeChild(node.firstChild); }

    // The screen's behaviour, with the document, the fetch helper and the timers passed in so the Node tests can
    // drive it with fakes. Every async step checks that the project (and, for search, the search itself) is still
    // the one it started for before it paints anything.
    function createController(env) {
        const doc = env.document;
        const ev = { project: null, seq: 0, searchSeq: 0, timer: null, rejectedOpen: createOpenState(), moreOpen: createOpenState() };
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
            if (project !== ev.project) { ev.project = project; ev.rejectedOpen = createOpenState(); ev.moreOpen = createOpenState(); resetSearch(); }
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
                slot.appendChild(phaseEvidenceNode(doc, data.phases[phase], ev.rejectedOpen.get(phase), ev.moreOpen.get(phase)));
            });
            // The anchors exist now, so the markers on the phase cards can link to them.
            const index = citationIndex(data.phases);
            Array.from(doc.querySelectorAll('[data-cite-phase]')).forEach(box => linkCitations(doc, box, index));
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
        function rememberMoreBox(phase, open) { ev.moreOpen.set(phase, open); }

        return { renderAll, runSearch, resetSearch, onSearchInput, onAction, rememberRejectedBox, rememberMoreBox };
    }

    const pure = {
        safeUrl, factNode, conclusionNode, phaseEvidenceNode, searchResultsNode, createOpenState, createController, openFoldedBoxes,
        briefPromptAddition, phaseBriefAddition,
        NOT_CITED_NOTE, hasCitationMarkers, notCitedNote, citationIndex, linkCitations, handleCitationClick,
        unescapeCitationMarkers,
    };
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

    // "toggle" does not bubble, so listen in the capture phase to remember each phase's Show rejected and Show n more boxes.
    document.addEventListener('toggle', event => {
        const box = event.target;
        if (!box || !box.classList) return;
        const remember = box.classList.contains('ev-rejected') ? screen.rememberRejectedBox
            : box.classList.contains('ev-more') ? screen.rememberMoreBox : null;
        if (!remember) return;
        const slot = box.closest('[data-evidence-phase]');
        if (slot) remember(slot.getAttribute('data-evidence-phase'), box.open);
    }, true);

    document.addEventListener('click', event => {
        const target = event.target;
        if (!target || !target.closest) return;
        const ref = target.closest('a.ev-ref');
        if (ref) {  // open "Show rejected" or "Show n more" if the fact is folded in one; the browser then follows the #anchor
            openFoldedBoxes(document.getElementById((ref.getAttribute('href') || '').slice(1)));
            return;
        }
        const button = target.closest('[data-ev-act]');
        if (button) screen.onAction(button);
    });

    // Capture phase: this runs before the phase card's own onclick, so stopping the click here keeps the editor shut.
    document.addEventListener('click', event => { handleCitationClick(document, event); }, true);

    window.evidenceRenderAll = screen.renderAll;
    window.evidenceResetSearch = screen.resetSearch;
    window.evidencePhaseBriefAddition = (project, phaseKey) => phaseBriefAddition(call, project, phaseKey);
    window.evidenceNotCitedNote = notCitedNote;
    window.evidenceUnescapeCitationMarkers = unescapeCitationMarkers;
})();
