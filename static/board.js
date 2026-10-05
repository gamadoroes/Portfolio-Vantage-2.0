// static/board.js
// Research tab ("Research Board"). Renders GET /api/board and sends the user's actions to /api/board/*.
// The render functions are pure (state -> HTML string) and are exported for the Node tests in tests/js/.
(function () {
    'use strict';

    const STATUS = {
        PROPOSED: { label: 'Needs your approval', cls: 'rb-s-needs' },
        READY: { label: 'Approved, waiting for Run', cls: 'rb-s-appr' },
        RUNNING: { label: 'Running', cls: 'rb-s-run' },
        REVIEWING: { label: 'Being reviewed', cls: 'rb-s-run' },
        COMPLETE: { label: 'Finished', cls: 'rb-s-done' },
        FOLLOW_UP_REQUIRED: { label: 'Finished, follow-up suggested', cls: 'rb-s-fu' },
        WAITING_FOR_HUMAN: { label: 'Needs your review', cls: 'rb-s-needs' },
        FAILED: { label: 'Failed', cls: 'rb-s-fail' },
        SKIPPED: { label: 'Skipped', cls: 'rb-s-skip' },
    };
    const METHOD_LABEL = { TARGETED_WEB: 'Web research', FILE_ANALYSIS: 'My files', SYNTHESIS: 'Options report' };
    const USER_METHODS = ['TARGETED_WEB', 'FILE_ANALYSIS'];
    const ACTIVE = ['RUNNING', 'REVIEWING'];
    const FILTERS = [
        { key: 'all', label: 'All', test: c => c.status !== 'SKIPPED' },
        { key: 'draft', label: 'Needs approval', test: c => c.status === 'PROPOSED' },
        { key: 'approved', label: 'Approved', test: c => c.status === 'READY' },
        { key: 'running', label: 'Running', test: c => ACTIVE.includes(c.status) },
        { key: 'finished', label: 'Finished', test: c => c.status === 'COMPLETE' || c.status === 'FOLLOW_UP_REQUIRED' },
        { key: 'review', label: 'Needs your review', test: c => c.status === 'WAITING_FOR_HUMAN', onlyWhenAny: true },
        { key: 'failed', label: 'Failed', test: c => c.status === 'FAILED', onlyWhenAny: true },
    ];

    function esc(value) {
        return String(value == null ? '' : value).replace(/[&<>"']/g,
            ch => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[ch]));
    }
    function newView() {
        return { filter: 'all', closedPhases: {}, editing: {}, drafts: {}, cardErrors: {}, adding: null,
                 confirming: false, busy: false, supervisorNote: '', message: '', objEdit: false, objDraft: null };
    }
    function pluralise(n, one, many) { return `${n} ${n === 1 ? one : many}`; }
    function filterMatches(card, key) { return (FILTERS.find(f => f.key === key) || FILTERS[0]).test(card); }
    function formatElapsed(seconds) {
        if (seconds == null) return '';
        const minutes = Math.floor(seconds / 60);
        if (minutes < 1) return 'under a minute';
        if (minutes < 60) return `${minutes} min`;
        return `${Math.floor(minutes / 60)} h ${minutes % 60} min`;
    }
    function formatClock(iso) { const m = /T(\d{2}):(\d{2})/.exec(iso || ''); return m ? `${m[1]}:${m[2]}` : ''; }
    function splitFocus(text) { return String(text || '').split(',').map(s => s.trim()).filter(Boolean); }

    function editPayload(draft) {
        const out = {};
        ['title', 'prompt_text', 'research_method', 'rationale'].forEach(k => { if (draft && draft[k] !== undefined) out[k] = draft[k]; });
        if (draft && draft.focus !== undefined) out.focus = splitFocus(draft.focus);
        return out;
    }
    function addPayload(project, a) {
        return {
            project, phase_key: a.phase_key, title: (a.title || '').trim(),
            research_method: a.research_method || 'TARGETED_WEB', focus: splitFocus(a.focus),
            rationale: (a.rationale || '').trim(), draft_prompt: a.draft_prompt !== false,
            framework_key: a.framework_key || null, prompt_text: a.prompt_text || '',
        };
    }

    function stepsHtml(state) {
        const live = state.cards.filter(c => c.status !== 'SKIPPED');
        const any = list => live.some(c => list.includes(c.status));
        const steps = [
            ['Objective', !!(state.objective || '').trim()],
            ['Plan', any(['READY', 'RUNNING', 'REVIEWING', 'COMPLETE', 'FOLLOW_UP_REQUIRED', 'WAITING_FOR_HUMAN', 'FAILED'])],
            ['Run', any(['RUNNING', 'REVIEWING', 'COMPLETE', 'FOLLOW_UP_REQUIRED', 'WAITING_FOR_HUMAN', 'FAILED'])],
            ['Findings', any(['COMPLETE', 'FOLLOW_UP_REQUIRED'])],
        ];
        let now = steps.findIndex(s => !s[1]);
        if (now < 0) now = steps.length - 1;
        return '<ol class="rb-steps" aria-label="Progress">' + steps.map((s, i) =>
            `<li class="${s[1] ? 'done' : (i === now ? 'now' : '')}"><span class="n">${i + 1}</span>${s[0]}</li>`).join('') + '</ol>';
    }

    function objectiveHtml(state, view) {
        const body = view.objEdit
            ? `<label class="rb-lbl" for="rb-obj-input">What should this research achieve?</label><textarea id="rb-obj-input" class="rb-obj-input">${esc(typeof view.objDraft === 'string' ? view.objDraft : state.objective)}</textarea><div class="rb-row"><button type="button" class="rb-btn primary" data-act="obj-save">Save objective</button><button type="button" class="rb-btn" data-act="obj-cancel">Cancel</button></div>`
            : `<p class="rb-obj-text">${state.objective ? esc(state.objective) : '<span class="rb-muted">No objective yet. The Supervisor needs one before it can draft researches.</span>'}</p><div class="rb-row"><button type="button" class="rb-btn" data-act="obj-edit">Edit objective</button></div>`;
        const draftBtn = view.busy
            ? '<button type="button" class="rb-btn" disabled>Drafting…</button>'
            : `<button type="button" class="rb-btn" data-act="draft"${state.objective ? '' : ' disabled'}>Draft next researches</button>`;
        const note = view.supervisorNote ? `<br><span class="rb-note">${esc(view.supervisorNote)}</span>` : '';
        return `<section class="rb-panel rb-objective" aria-label="Objective"><h3>Objective</h3>${body}<div class="rb-sup"><p class="rb-small rb-muted"><b>Supervisor.</b> It reads the objective, drafts researches and reviews finished ones. It never starts research by itself.${note}</p>${draftBtn}</div></section>`;
    }

    function chipsHtml(cards, view) {
        return '<div class="rb-chips" role="group" aria-label="Filter researches">' + FILTERS.map(f => {
            const n = cards.filter(f.test).length;
            if (f.onlyWhenAny && n === 0) return '';
            return `<button type="button" class="rb-chip" data-act="filter" data-val="${f.key}" aria-pressed="${view.filter === f.key}">${f.label} <span class="c">${n}</span></button>`;
        }).join('') + '</div>';
    }

    function metaHtml(card) {
        const method = card.method_label || METHOD_LABEL[card.research_method] || 'No method yet';
        const focus = (card.focus || []).map(f => `<span class="rb-tag">${esc(f)}</span>`).join('');
        return `<div class="rb-meta"><span class="rb-tag method">${esc(method)}</span>${focus}</div>`;
    }
    function previewHtml(card) {
        if (!card.prompt_text) return '<p class="rb-preview rb-muted">No prompt yet. Edit this research to write one.</p>';
        return `<p class="rb-preview">${esc(card.prompt_text)}</p>`;
    }
    function linksHtml(card) {
        let html = '';
        if (card.suggested_from) html += `<p class="rb-from">Suggested after reviewing “${esc(card.suggested_from.title)}”.</p>`;
        if ((card.followups || []).length) {
            html += '<p class="rb-from">Follow-up: ' + card.followups.map(f =>
                `“${esc(f.title)}” (${esc((STATUS[f.status] || {}).label || f.status)})`).join(', ') + '</p>';
        }
        const waiting = (card.depends_on || []).filter(d => d.status !== 'COMPLETE' && d.status !== 'SKIPPED');
        if (waiting.length) html += '<p class="rb-from">Waits for: ' + waiting.map(d => `“${esc(d.title)}”`).join(', ') + '</p>';
        return html;
    }

    function formHtml(card, view) {
        const id = card.id;
        const d = view.drafts[id] || {};
        const val = k => (d[k] !== undefined ? d[k] : card[k]);
        const focus = d.focus !== undefined ? d.focus : (card.focus || []).join(', ');
        const method = val('research_method');
        const methodField = card.research_method === 'SYNTHESIS'
            ? '<p class="rb-hint">Runs as the options report for Phase 7.</p>'
            : '<div class="rb-seg" role="radiogroup" aria-label="How it runs">' + USER_METHODS.map(m =>
                `<label><input type="radio" name="rb-method-${id}" value="${m}" data-field="research_method" data-id="${id}"${method === m ? ' checked' : ''}> ${METHOD_LABEL[m]}</label>`).join('') + '</div>';
        const from = card.framework_label ? `Drafted from the ${esc(card.framework_label)} framework. ` : '';
        return `<div class="rb-form">`
            + `<div><label class="rb-lbl" for="rb-title-${id}">Title</label><input type="text" id="rb-title-${id}" data-field="title" data-id="${id}" value="${esc(val('title'))}"></div>`
            + `<div><span class="rb-lbl">How it runs</span>${methodField}</div>`
            + `<div><label class="rb-lbl" for="rb-prompt-${id}">Research prompt</label><textarea id="rb-prompt-${id}" data-field="prompt_text" data-id="${id}" spellcheck="false">${esc(val('prompt_text') || '')}</textarea><p class="rb-hint">${from}Change anything. This text is exactly what gets sent.</p></div>`
            + `<div><label class="rb-lbl" for="rb-focus-${id}">Focus (separate with commas)</label><input type="text" id="rb-focus-${id}" data-field="focus" data-id="${id}" value="${esc(focus)}"></div>`
            + `<div><label class="rb-lbl" for="rb-why-${id}">Why this research</label><input type="text" id="rb-why-${id}" data-field="rationale" data-id="${id}" value="${esc(val('rationale') || '')}"></div>`
            + `<div class="rb-row"><button type="button" class="rb-btn primary" data-act="approve" data-id="${id}">Approve</button>`
            + `<button type="button" class="rb-btn" data-act="save" data-id="${id}">Save as draft</button>`
            + `<button type="button" class="rb-btn" data-act="redraft" data-id="${id}"${card.framework_key ? '' : ' disabled'}>Re-draft from framework</button>`
            + `<button type="button" class="rb-btn quiet" data-act="close" data-id="${id}">Cancel</button></div>`
            + `</div>`;
    }

    function meter(label, score) {
        if (score == null) return '';
        const pct = Math.round(Math.max(0, Math.min(1, Number(score))) * 100);
        return `<div class="rb-meter"><div class="top"><span>${label}</span><span class="rb-mono">${Number(score).toFixed(2)}</span></div><div class="track"><span style="width:${pct}%"></span></div></div>`;
    }
    function reportButton(card) {
        return card.run && card.run.has_report ? `<button type="button" class="rb-btn" data-act="report" data-id="${card.id}">Read report</button>` : '';
    }
    function reviewHtml(card) {
        const meters = meter('Completeness', card.completeness_score) + meter('Evidence', card.evidence_score);
        const gaps = (card.gaps || []).length ? `<div><h5>Gaps the Supervisor found</h5><ul>${card.gaps.map(g => `<li>${esc(g)}</li>`).join('')}</ul></div>` : '';
        const file = card.run && card.run.report_filename ? `<p class="rb-hint">Saved to your sources as “${esc(card.run.report_filename)}” and linked to this phase.</p>` : '';
        return `<div class="rb-review"><h5>Supervisor review</h5>${meters ? `<div class="rb-meters">${meters}</div>` : ''}${gaps}${file}<div class="rb-row">${reportButton(card)}</div></div>`;
    }
    function runningText(card) {
        if (card.research_method === 'TARGETED_WEB') return 'Searching and reading sources';
        if (card.research_method === 'SYNTHESIS') return 'Writing the options report';
        return 'Reading your files';
    }

    function cardBody(card, state, view) {
        const id = card.id;
        const btn = (act, label, cls, disabled) => `<button type="button" class="rb-btn${cls ? ' ' + cls : ''}" data-act="${act}" data-id="${id}"${disabled ? ' disabled' : ''}>${label}</button>`;
        switch (card.status) {
            case 'PROPOSED':
                return previewHtml(card) + (card.needs_prompt_edit ? '<p class="rb-warn">Edit the prompt before approving.</p>' : '')
                    + linksHtml(card) + `<div class="rb-row">${btn('edit', 'Edit and approve', 'primary')}${btn('skip', 'Skip', 'quiet')}</div>`;
            case 'READY':
                return previewHtml(card) + linksHtml(card)
                    + `<div class="rb-row">${btn('edit', 'Edit')}${btn('unapprove', 'Back to draft', 'quiet')}</div><p class="rb-hint">Editing sends it back for approval.</p>`;
            case 'RUNNING':
                return `<p class="rb-progress"><span class="rb-pulse" aria-hidden="true"></span>${runningText(card)} · <span class="rb-mono">${esc(formatElapsed(card.run ? card.run.elapsed_seconds : null))}</span></p>`
                    + (card.review_error ? '<p class="rb-warn">The review didn\'t complete. It will be retried.</p>' : '');
            case 'REVIEWING':
                return '<p class="rb-progress"><span class="rb-pulse" aria-hidden="true"></span>The Supervisor is reviewing this research.</p>';
            case 'COMPLETE':
            case 'FOLLOW_UP_REQUIRED':
                if (card.research_method === 'SYNTHESIS') {
                    return `<p class="rb-hint">Saved to Insights, Phase 7.</p><div class="rb-row">${reportButton(card)}</div>`;
                }
                return linksHtml(card) + reviewHtml(card);
            case 'WAITING_FOR_HUMAN':
                return reviewHtml(card) + '<p class="rb-hint">The Supervisor wasn\'t sure about this one. What do you think?</p>'
                    + `<div class="rb-row">${btn('accept', 'Accept as finished', 'primary')}${btn('needs-followup', 'Needs follow-up')}${btn('mark-failed', 'Mark failed', 'quiet')}</div>`;
            case 'FAILED': {
                const atLimit = card.retry_count >= card.max_retries;
                const error = (card.run && card.run.error) || 'This research did not produce a usable result.';
                return `<p class="rb-err">${esc(error)}</p>${(card.gaps || []).length ? reviewHtml(card) : ''}`
                    + `<div class="rb-row">${btn('retry', 'Retry', 'primary', atLimit)}${btn('back-to-draft', 'Back to draft')}${btn('skip', 'Skip', 'quiet')}</div>`
                    + (atLimit ? `<p class="rb-hint">It has failed ${card.retry_count} times. Move it back to draft to change it, or skip it.</p>` : '');
            }
            default:
                return '';
        }
    }

    function cardHtml(card, state, view) {
        const st = STATUS[card.status] || { label: card.status, cls: '' };
        const editing = !!view.editing[card.id] && (card.status === 'PROPOSED' || card.status === 'READY');
        const top = `<div class="rb-card-top"><h4>${esc(card.title)}</h4><span class="rb-pill ${st.cls}"><i></i>${esc(st.label)}</span></div>${metaHtml(card)}`;
        const error = view.cardErrors && view.cardErrors[card.id] ? `<p class="rb-err" role="alert">${esc(view.cardErrors[card.id])}</p>` : '';
        const stale = card.phase_key === '7' && state.phase7 && state.phase7.summaries_stale && (card.status === 'PROPOSED' || card.status === 'READY')
            ? '<p class="rb-warn">Your Insights summaries are older than your newest research. Refresh Insights first to include it.</p>' : '';
        const body = editing ? formHtml(card, view) : cardBody(card, state, view);
        return `<article class="rb-card${editing ? ' open' : ''}" id="rb-card-${card.id}">${top}${stale}${error}${body}</article>`;
    }

    function addFormHtml(state, view) {
        const a = view.adding;
        const phases = state.phases.filter(p => p.key !== '7');
        const phase = phases.find(p => p.key === a.phase_key) || phases[0];
        const frameworks = phase.frameworks || [];
        const method = a.research_method || 'TARGETED_WEB';
        const drafting = a.draft_prompt !== false;
        const frameworkPick = drafting && frameworks.length > 1
            ? `<select data-add="framework_key" aria-label="Framework">${frameworks.map(f => `<option value="${f.key}"${a.framework_key === f.key ? ' selected' : ''}>${esc(f.label)}</option>`).join('')}</select>` : '';
        const promptField = drafting ? ''
            : `<div><label class="rb-lbl" for="rb-add-prompt">Research prompt</label><textarea id="rb-add-prompt" data-add="prompt_text" spellcheck="false">${esc(a.prompt_text || '')}</textarea></div>`;
        return `<div class="rb-card open rb-add"><h4>Add research</h4><div class="rb-form">`
            + `<div><label class="rb-lbl" for="rb-add-phase">Phase</label><select id="rb-add-phase" data-add="phase_key">${phases.map(p => `<option value="${p.key}"${p.key === phase.key ? ' selected' : ''}>Phase ${p.key}: ${esc(p.title)}</option>`).join('')}</select></div>`
            + `<div><label class="rb-lbl" for="rb-add-title">Title</label><input type="text" id="rb-add-title" data-add="title" value="${esc(a.title || '')}"></div>`
            + `<div><span class="rb-lbl">How it runs</span><div class="rb-seg">${USER_METHODS.map(m => `<label><input type="radio" name="rb-add-method" value="${m}" data-add="research_method"${method === m ? ' checked' : ''}> ${METHOD_LABEL[m]}</label>`).join('')}</div></div>`
            + `<div><label class="rb-lbl" for="rb-add-focus">Focus (separate with commas)</label><input type="text" id="rb-add-focus" data-add="focus" value="${esc(a.focus || '')}"></div>`
            + `<div><label class="rb-lbl" for="rb-add-why">Why this research</label><input type="text" id="rb-add-why" data-add="rationale" value="${esc(a.rationale || '')}"></div>`
            + `<div class="rb-row"><label class="rb-check"><input type="checkbox" data-add="draft_prompt"${drafting ? ' checked' : ''}> Draft the prompt for me from the framework</label>${frameworkPick}</div>`
            + promptField
            + `<div class="rb-row"><button type="button" class="rb-btn primary" data-act="add-save"${view.busy ? ' disabled' : ''}>${view.busy ? 'Adding…' : 'Add research'}</button><button type="button" class="rb-btn quiet" data-act="add-cancel">Cancel</button></div>`
            + `</div></div>`;
    }

    function phaseSummary(cards) {
        if (!cards.length) return 'None drafted yet';
        const need = cards.filter(c => c.status === 'PROPOSED').length;
        return pluralise(cards.length, 'research', 'researches') + (need ? ` · ${need} need${need === 1 ? 's' : ''} approval` : '');
    }
    function phase7Html(state, view, cards) {
        const p7 = state.phase7 || {};
        if (!p7.unlocked) {
            return `<div class="rb-locked"><b>Opens when Phases 1 to 6 each have finished research.</b><br>${p7.ready_count || 0} of 6 are ready. Then you can draft the options report.</div>`;
        }
        let html = '';
        if (p7.summaries_stale) html += '<p class="rb-warn">Your Insights summaries are older than your newest research. Refresh Insights first to include it.</p>';
        const open = cards.some(c => ['PROPOSED', 'READY', 'RUNNING', 'REVIEWING'].includes(c.status));
        if (!open) html += `<div class="rb-row"><button type="button" class="rb-btn primary" data-act="draft-options"${view.busy ? ' disabled' : ''}>Draft options report</button></div>`;
        return html;
    }
    function phaseHtml(phase, state, view) {
        const isOpen = !view.closedPhases[phase.key];
        const all = state.cards.filter(c => c.phase_key === phase.key && c.status !== 'SKIPPED');
        const shown = all.filter(c => filterMatches(c, view.filter));
        let body = '';
        if (isOpen) {
            if (phase.key === '7') body += phase7Html(state, view, all);
            if (view.adding && view.adding.phase_key === phase.key) body += addFormHtml(state, view);
            if (shown.length) body += shown.map(c => cardHtml(c, state, view)).join('');
            else if (all.length) body += '<div class="rb-empty">Nothing here for this filter.</div>';
            else if (phase.key !== '7') body += '<div class="rb-empty">No research drafted for this phase yet. Ask the Supervisor, or add one yourself.</div>';
            if (phase.key !== '7') body += `<div class="rb-row"><button type="button" class="rb-btn quiet" data-act="add" data-phase="${phase.key}">+ Add research</button></div>`;
        }
        const summary = phase.key === '7' ? '' : phaseSummary(all);
        return `<div class="rb-phase"><button type="button" class="rb-phase-head" data-act="phase" data-phase="${phase.key}" aria-expanded="${isOpen}"><span class="rb-chev" aria-hidden="true">▾</span><span class="rb-phase-num">PHASE ${phase.key}</span><span class="rb-phase-name">${esc(phase.title)}</span><span class="rb-phase-sum">${summary}</span></button>${isOpen ? `<div class="rb-phase-body">${body}</div>` : ''}</div>`;
    }

    function skippedHtml(cards) {
        const skipped = cards.filter(c => c.status === 'SKIPPED');
        if (!skipped.length) return '';
        return `<div class="rb-panel rb-skipped"><h3>Skipped (${skipped.length})</h3><ul>${skipped.map(c =>
            `<li><span>${esc(c.title)}</span><button type="button" class="rb-btn quiet" data-act="restore" data-id="${c.id}">Restore</button></li>`).join('')}</ul></div>`;
    }

    function runBoxHtml(state, view) {
        const approved = state.cards.filter(c => c.status === 'READY');
        const running = state.cards.filter(c => ACTIVE.includes(c.status)).length;
        const n = approved.length;
        const list = n ? `<ul class="rb-runlist">${approved.map(c => `<li><span>${esc(c.title)}</span><span class="rb-muted rb-small">${esc(c.method_label || METHOD_LABEL[c.research_method] || '')}</span></li>`).join('')}</ul>`
            : '<p class="rb-muted rb-small">Approve a research to add it here.</p>';
        const web = approved.filter(c => c.research_method === 'TARGETED_WEB').length;
        let action;
        if (view.confirming && n) {
            action = `<div class="rb-confirm" role="group" aria-label="Confirm run"><p><b>Start ${pluralise(n, 'research', 'researches')}?</b></p>`
                + `<p class="rb-small rb-muted">They run in the background, so you can leave this page.${web ? ` Web research bills your OpenAI account (${pluralise(web, 'research', 'researches')}).` : ''}</p>`
                + `<div class="rb-row"><button type="button" class="rb-btn primary" data-act="run-start">Start ${n}</button><button type="button" class="rb-btn" data-act="run-cancel">Not yet</button></div></div>`;
        } else {
            action = `<button type="button" class="rb-btn primary wide" data-act="run-open"${n ? '' : ' disabled'}>${n ? `Run ${n} approved ${n === 1 ? 'research' : 'researches'}` : 'Nothing approved to run'}</button>`;
        }
        return `<section class="rb-panel rb-runbox" aria-label="Run"><h3>Run</h3><p class="rb-small rb-muted">Only approved researches run, and only when you press Run.${running ? ` ${running} running now.` : ''}</p>${list}${action}</section>`;
    }

    function activityHtml(state) {
        const items = state.activity || [];
        const list = items.length
            ? `<ol>${items.map(a => `<li><time>${esc(formatClock(a.at))}</time><span>${esc(a.text)}</span></li>`).join('')}</ol>`
            : '<p class="rb-muted rb-small">Nothing yet.</p>';
        return `<section class="rb-panel rb-activity" aria-label="Activity"><h3>Activity</h3>${list}</section>`;
    }

    function renderBoard(state, view) {
        const message = view.message ? `<div class="rb-message" role="status"><span>${esc(view.message)}</span><button type="button" class="rb-btn quiet" data-act="dismiss">Dismiss</button></div>` : '';
        const plan = chipsHtml(state.cards, view) + state.phases.map(p => phaseHtml(p, state, view)).join('') + skippedHtml(state.cards);
        return `<header class="rb-head"><div><h2>Research plan</h2><p class="rb-proj">Project <b>${esc(state.project)}</b></p></div>${stepsHtml(state)}</header>${message}`
            + `<div class="rb-grid">${objectiveHtml(state, view)}<section class="rb-plan" aria-label="Plan">${plan}</section>${runBoxHtml(state, view)}${activityHtml(state)}</div>`;
    }

    const pure = { STATUS, esc, newView, filterMatches, formatElapsed, pluralise, cardHtml, phaseHtml,
                   runBoxHtml, chipsHtml, renderBoard, editPayload, addPayload, objectiveHtml };
    if (typeof module !== 'undefined' && module.exports) { module.exports = pure; return; }

    // ---------------- browser glue ----------------
    const board = { project: null, state: null, view: newView(), timer: null, loadSeq: 0 };

    function activeProject() { return typeof currentProject !== 'undefined' ? currentProject : null; }
    function root() { return document.getElementById('research-board'); }
    function tabVisible() { const tab = document.getElementById('research-tab'); return !!(tab && tab.classList.contains('active')); }

    async function call(method, url, body) {
        const options = { method };
        if (body !== undefined) { options.headers = { 'Content-Type': 'application/json' }; options.body = JSON.stringify(body); }
        const res = await fetch(url, options);
        let data = null;
        try { data = await res.json(); } catch (e) { data = null; }
        if (!res.ok || !data || data.success === false) {
            const err = new Error((data && data.error) || `Something went wrong (${res.status}).`);
            err.status = res.status;
            throw err;
        }
        return data;
    }

    function render() {
        const el = root();
        if (!el) return;
        if (!activeProject()) { el.innerHTML = '<div class="rb-empty">Select a project to see its research plan.</div>'; return; }
        if (!board.state) {
            el.innerHTML = board.view.message
                ? `<div class="rb-message" role="alert"><span>${esc(board.view.message)}</span><button type="button" class="rb-btn" data-act="reload">Try again</button></div>`
                : '<div class="rb-empty">Loading the research plan…</div>';
            return;
        }
        el.innerHTML = renderBoard(board.state, board.view);
    }

    function syncProject() {
        const project = activeProject();
        if (project !== board.project) {
            board.project = project;
            board.state = null;
            board.view = newView();
            clearTimeout(board.timer);
            board.timer = null;
        }
        return project;
    }

    async function loadBoard() {
        const project = syncProject();
        board.view.message = '';
        render();
        if (!project) return;
        const seq = ++board.loadSeq;
        try {
            const data = await call('GET', `/api/board?project=${encodeURIComponent(project)}`);
            if (seq !== board.loadSeq || project !== activeProject()) return;
            board.state = data.board;
        } catch (e) {
            if (project !== activeProject()) return;
            board.view.message = e.message;
        }
        render();
    }

    // ---- interactions ----

    function isEditing() {
        const v = board.view;
        return v.objEdit || !!v.adding || Object.keys(v.editing).some(k => v.editing[k]);
    }

    function schedulePoll() {
        clearTimeout(board.timer);
        board.timer = null;
        if (!board.state || !tabVisible()) return;
        if (!board.state.cards.some(c => ACTIVE.includes(c.status))) return;
        board.timer = setTimeout(() => { if (tabVisible()) refreshBoard(); }, 15000);
    }

    function generationRunning(project) {
        return typeof getActiveGenerationForProject === 'function' && !!getActiveGenerationForProject(project);
    }

    async function refreshBoard() {
        const project = syncProject();
        if (!project) { render(); return; }
        const firstLoad = !board.state;
        if (firstLoad) render();
        const seq = ++board.loadSeq;
        try {
            const data = await call('POST', '/api/board/refresh', { project, defer_linking: generationRunning(project) });
            if (seq !== board.loadSeq || project !== activeProject()) return;
            board.state = data.board;
        } catch (e) {
            if (project !== activeProject()) return;
            board.view.message = e.message;
        }
        // Don't redraw under someone who is typing; the next action or refresh will.
        if (firstLoad || !isEditing()) render();
        schedulePoll();
    }

    // An action's result only counts if the user is still on the project it started on.
    function takeState(data) {
        if (!data || !data.board || data.board.project !== board.project) return;
        board.loadSeq++;  // anything still in flight is now stale
        board.state = data.board;
        render();
        schedulePoll();
    }

    function handleError(err, cardId, project) {
        if (project !== board.project) return;
        if (cardId != null) board.view.cardErrors[cardId] = err.message;
        else board.view.message = err.message;
        render();
        // The message stays; the refresh just brings the board back in line with the server.
        if (err.status === 404 || err.status === 409) refreshBoard();
    }

    // One action at a time per card (or per board), so a double-click can't post twice.
    const inFlight = new Set();

    async function attempt(cardId, fn) {
        const project = board.project;
        const key = `${project}|${cardId == null ? 'board' : cardId}`;
        if (inFlight.has(key)) return;
        inFlight.add(key);
        if (cardId != null) delete board.view.cardErrors[cardId];
        try { await fn(project); } catch (err) { handleError(err, cardId, project); } finally { inFlight.delete(key); }
    }

    function closeEditor(id) {
        delete board.view.editing[id];
        delete board.view.drafts[id];
    }

    function cardAction(project, id, action) {
        return call('POST', `/api/board/cards/${id}/${action}`, { project });
    }

    async function saveDraftEdits(project, id) {
        const payload = editPayload(board.view.drafts[id]);
        if (Object.keys(payload).length) {
            await call('PATCH', `/api/board/cards/${id}`, Object.assign({ project }, payload));
        }
    }

    function simple(action) {
        return el => {
            const id = Number(el.dataset.id);
            attempt(id, async project => takeState(await cardAction(project, id, action)));
        };
    }

    function focusLater(elementId) {
        const el = document.getElementById(elementId);
        if (el) el.focus();
    }

    const ACTIONS = {
        reload() { board.view.message = ''; refreshBoard(); },
        dismiss() { board.view.message = ''; render(); },
        filter(el) { board.view.filter = el.dataset.val; render(); },
        phase(el) { const k = el.dataset.phase; board.view.closedPhases[k] = !board.view.closedPhases[k]; render(); },
        edit(el) { const id = Number(el.dataset.id); board.view.editing[id] = true; render(); focusLater(`rb-prompt-${id}`); },
        close(el) { closeEditor(Number(el.dataset.id)); render(); },
        save(el) {
            const id = Number(el.dataset.id);
            attempt(id, async project => {
                await saveDraftEdits(project, id);
                const data = await call('GET', `/api/board?project=${encodeURIComponent(project)}`);
                if (project !== board.project) return;
                closeEditor(id);
                takeState(data);
            });
        },
        approve(el) {
            const id = Number(el.dataset.id);
            attempt(id, async project => {
                await saveDraftEdits(project, id);
                const data = await cardAction(project, id, 'approve');
                if (project !== board.project) return;
                closeEditor(id);
                takeState(data);
            });
        },
        redraft(el) {
            const id = Number(el.dataset.id);
            attempt(id, async project => {
                el.disabled = true;
                el.textContent = 'Drafting…';
                await saveDraftEdits(project, id);
                const data = await cardAction(project, id, 'redraft');
                if (project !== board.project) return;
                if (board.view.drafts[id]) delete board.view.drafts[id].prompt_text;
                takeState(data);
            });
        },
        unapprove: simple('unapprove'),
        skip: simple('skip'),
        restore: simple('restore'),
        retry: simple('retry'),
        'back-to-draft': simple('back-to-draft'),
        accept: simple('accept'),
        'needs-followup': simple('needs-followup'),
        'mark-failed': simple('mark-failed'),
        add(el) {
            board.view.adding = { phase_key: el.dataset.phase, draft_prompt: true, research_method: 'TARGETED_WEB' };
            render();
            focusLater('rb-add-title');
        },
        'add-cancel'() { board.view.adding = null; render(); },
        'add-save'() {
            const adding = board.view.adding;
            if (!adding) return;
            attempt(null, async project => {
                const view = board.view;
                view.busy = true;
                render();
                let data;
                try { data = await call('POST', '/api/board/cards', addPayload(project, adding)); }
                finally { view.busy = false; }
                if (project !== board.project) return;
                view.adding = null;
                takeState(data);
            });
        },
        draft() {
            attempt(null, async project => {
                const view = board.view;
                view.busy = true;
                view.supervisorNote = '';
                render();
                let data;
                try { data = await call('POST', '/api/board/draft', { project }); }
                finally { view.busy = false; }
                if (project !== board.project) return;
                view.supervisorNote = data.note || '';
                takeState(data);
            });
        },
        'draft-options'() {
            attempt(null, async project => {
                const view = board.view;
                view.busy = true;
                render();
                let data;
                try { data = await call('POST', '/api/board/phase7/draft', { project }); }
                finally { view.busy = false; }
                if (project !== board.project) return;
                takeState(data);
            });
        },
        'run-open'() { board.view.confirming = true; render(); },
        'run-cancel'() { board.view.confirming = false; render(); },
        'run-start'() {
            const ids = board.state.cards.filter(c => c.status === 'READY').map(c => c.id);
            board.view.confirming = false;
            render();  // the confirm panel goes away now, so it can't be clicked twice
            attempt(null, async project => {
                const data = await call('POST', '/api/board/run', { project, card_ids: ids });
                if (project !== board.project) return;
                const result = data.result || {};
                const notes = [];
                if ((result.not_ready || []).length) {
                    notes.push(`${pluralise(result.not_ready.length, 'research was', 'researches were')} no longer approved and did not start.`);
                }
                if ((result.failed || []).length) {
                    notes.push(`${pluralise(result.failed.length, 'research', 'researches')} could not start: ${result.failed.map(f => f.error).join(' ')}`);
                }
                board.view.message = notes.join(' ');
                takeState(data);
            });
        },
        'obj-edit'() {
            board.view.objEdit = true;
            board.view.objDraft = board.state ? board.state.objective || '' : '';
            render();
            focusLater('rb-obj-input');
        },
        'obj-cancel'() { board.view.objEdit = false; board.view.objDraft = null; render(); },
        'obj-save'() {
            const input = document.getElementById('rb-obj-input');
            const value = typeof board.view.objDraft === 'string' ? board.view.objDraft : (input ? input.value : '');
            attempt(null, async project => {
                const data = await call('PUT', '/api/board/objective', { project, objective: value });
                if (project !== board.project) return;
                board.view.objEdit = false;
                board.view.objDraft = null;
                const objectiveTab = document.getElementById('project-prompt');
                if (objectiveTab) objectiveTab.value = value;
                if (typeof projectData === 'object' && projectData) projectData.project_prompt = value;
                takeState(data);
            });
        },
        report(el) {
            const id = Number(el.dataset.id);
            attempt(id, async project => {
                const data = await call('GET', `/api/board/cards/${id}/report?project=${encodeURIComponent(project)}`);
                if (project !== board.project) return;
                showReader(data.report);
            });
        },
    };

    function showReader(report) {
        closeReader();
        const overlay = document.createElement('div');
        overlay.id = 'rb-reader';
        overlay.className = 'rb-reader';
        overlay.setAttribute('role', 'dialog');
        overlay.setAttribute('aria-modal', 'true');
        overlay.setAttribute('aria-label', report.title);
        const body = typeof renderMarkdown === 'function' ? renderMarkdown(report.text) : `<pre>${esc(report.text)}</pre>`;
        const file = report.filename ? `<p class="rb-hint">Saved in your sources as “${esc(report.filename)}”.</p>` : '';
        overlay.innerHTML = `<div class="rb-reader-box"><div class="rb-reader-head"><h3>${esc(report.title)}</h3><button type="button" class="rb-btn" data-reader-close>Close</button></div><div class="rb-reader-body markdown-body">${file}${body}</div></div>`;
        overlay.addEventListener('click', e => { if (e.target === overlay || e.target.closest('[data-reader-close]')) closeReader(); });
        document.body.appendChild(overlay);
        overlay.querySelector('[data-reader-close]').focus();
    }
    function closeReader() { const el = document.getElementById('rb-reader'); if (el) el.remove(); }

    function onField(ev) {
        const el = ev.target;
        if (!el.closest || !el.closest('#research-board')) return;
        if (el.id === 'rb-obj-input') {
            board.view.objDraft = el.value;
        } else if (el.dataset.field && el.dataset.id) {
            const id = Number(el.dataset.id);
            board.view.drafts[id] = board.view.drafts[id] || {};
            if (el.type !== 'radio' || el.checked) board.view.drafts[id][el.dataset.field] = el.value;
        } else if (el.dataset.add && board.view.adding) {
            if (el.type === 'radio' && !el.checked) return;
            board.view.adding[el.dataset.add] = el.type === 'checkbox' ? el.checked : el.value;
            if (ev.type === 'change' && (el.dataset.add === 'draft_prompt' || el.dataset.add === 'phase_key')) render();
        }
    }

    document.addEventListener('click', ev => {
        const el = ev.target.closest && ev.target.closest('#research-board [data-act]');
        if (!el || el.disabled) return;
        const handler = ACTIONS[el.dataset.act];
        if (handler) handler(el);
    });
    document.addEventListener('input', onField);
    document.addEventListener('change', onField);
    document.addEventListener('keydown', ev => { if (ev.key === 'Escape') closeReader(); });

    window.boardOnTabShown = function () { refreshBoard(); };
    window.boardOnProjectLoaded = function () {
        if (activeProject() === board.project) return;
        if (tabVisible()) refreshBoard(); else syncProject();
    };
    window.boardOpenAddResearch = function (title) {
        syncProject();
        board.view.adding = { phase_key: '1', title: title || '', draft_prompt: true, research_method: 'TARGETED_WEB' };
        if (board.state) { render(); focusLater('rb-add-title'); }
    };
})();
