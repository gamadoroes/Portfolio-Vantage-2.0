// ── Tab-level session isolation ──────────────────────────────
// sessionStorage is per-tab by design — each browser tab/window gets its own
// copy, surviving refreshes but not shared across tabs.  This prevents the
// "last tab wins" race condition on the Flask session cookie.
if (!sessionStorage.getItem('tabId')) {
    sessionStorage.setItem('tabId', 'tab_' + Date.now() + '_' + Math.random().toString(36).substr(2, 6));
}
function _saveTabState() {
    sessionStorage.setItem('currentProject', currentProject || '');
    sessionStorage.setItem('currentChat', currentChat || '');
}
function _restoreTabState() {
    return {
        project: sessionStorage.getItem('currentProject') || '',
        chat: sessionStorage.getItem('currentChat') || '',
    };
}

// Global state
let currentProject = null;
let currentChat = null;
let projectData = {};
let chapterMode = false;
let currentAbortController = null;
let lastSelection = null;
let lastRange = null;
let lastSelectionMarkdown = null;
let currentArtifactId = null; // Track currently open artifact for editing
let outputPreview = true;
let currentOutputRaw = "";
let turndownService = null;
let editingFile = null;
let fileEditorRaw = "";
let fileEditorWysiwyg = false;
let currentSourceFilename = null;
let outputDefaultFontSize = null;
let pendingChatCreation = null;   // Reserved for future use
let activeResearchRuns = {};      // {run_id: {intervalId, responseId, project, chatId}}
let currentPhaseKey = null;       // Track phase being edited in output overlay
let activeInsightGenerations = {}; // {genId: {type, project, overrideFileIds, overrideFiles, insightsDataSnapshot, abortController, insightsContext}}
let latestProjectLoadId = 0;      // Ignore stale load responses after fast project switching
let activeTableEditorState = null; // { tableId, hasHeader, matrix }

function newGenerationId() {
    return 'gen_' + Date.now() + '_' + Math.random().toString(36).substr(2, 6);
}
function getActiveGenerationForProject(project) {
    return Object.keys(activeInsightGenerations).find(id => activeInsightGenerations[id].project === project) || null;
}
function isAnyInsightGenerationActive() {
    return Object.keys(activeInsightGenerations).length > 0;
}
function getGenerationContext(genId) {
    return activeInsightGenerations[genId] || null;
}

function syncInsightsControlButtons() {
    const activeForCurrent = !!getActiveGenerationForProject(currentProject);
    const stopBtn = document.getElementById('insight-stop-btn');
    if (stopBtn) {
        stopBtn.style.display = activeForCurrent ? 'inline-block' : 'none';
        stopBtn.disabled = !activeForCurrent;
    }
}

const PHASE_DEFINITIONS = {
    "1": {
        title: "The Landscape",
        description: "Comprehensive market review using HEIMS and publicly available data — program types, top-ranked offerings, enrolment trends, provider landscape, and general sentiment analysis around program value. Focused on current data (last 2 years)."
    },
    "2": {
        title: "The Student",
        description: "Target student demographics, motivations, pain points, decision drivers, career stage profiles, and enrolment pathway analysis across competitor programs."
    },
    "3": {
        title: "Review of Marketing",
        description: "Competitor scoping, website UX/messaging review, sentiment analysis via social listening, paid and organic channel strategy, and brand positioning analysis."
    },
    "4": {
        title: "Product Features",
        description: "Granular course-level scraping — delivery modes, unit structures, specialisations, pricing, duration, flexibility options, technology platforms, and student experience features."
    },
    "5": {
        title: "Academic Content",
        description: "Deep curriculum analysis for priority competitors — learning outcomes, assessment design, accreditation, faculty profiles, academic partnerships, and pedagogical approach."
    },
    "6": {
        title: "Industry Engagement",
        description: "Industry partnerships, employer connections, placement programs, advisory boards, professional body affiliations, and work-integrated learning arrangements."
    },
    "7": {
        title: "Options for OES",
        description: "White space analysis and strategic options using the SO WHAT / NOW WHAT framework — interrogating each key finding across all phases to identify actionable opportunities for OES."
    }
};

function createEmptyInsightsData() {
    const data = {
        generated_at: new Date().toISOString(),
        competitors: [],
        competitor_landscape_markdown: "",
        phases: {}
    };
    Object.entries(PHASE_DEFINITIONS).forEach(([key, def]) => {
        data.phases[key] = {
            title: def.title,
            summary: "MISSING",
            confidence: "none",
            evidence_sources: [],
            gaps: [],
            suggested_topics: [],
            linked_files: [],
            linked_file_ids: []
        };
    });
    return data;
}

function hasMeaningfulInsightsContent(data) {
    if (!data) return false;
    if (Array.isArray(data.competitors) && data.competitors.length > 0) return true;
    if (typeof data.competitor_landscape_markdown === 'string' && data.competitor_landscape_markdown.trim()) return true;
    if (!data.phases) return false;
    return Object.values(data.phases).some(phase => {
        const summary = (phase && typeof phase.summary === 'string') ? phase.summary.trim() : '';
        return !!summary && summary !== 'MISSING';
    });
}

function getFileIndexIdToName() {
    const idx = projectData && projectData.file_index;
    return (idx && typeof idx === 'object') ? idx : {};
}

function getFileNameById(fileId) {
    if (!fileId || typeof fileId !== 'string') return null;
    const idToName = getFileIndexIdToName();
    return idToName[fileId] || null;
}

function getFileIdByName(filename) {
    if (!filename || typeof filename !== 'string') return null;
    const idToName = getFileIndexIdToName();
    for (const [fileId, name] of Object.entries(idToName)) {
        if (name === filename) return fileId;
    }
    return null;
}

// Phase 7 draws on the phase write-ups, which cite checked facts as [F#] / [C#]. Kept word for word in step with
// evidence_service.KEEP_MARKERS_INSTRUCTION (tests/test_routes_evidence.py checks).
const KEEP_CITATION_MARKERS = 'Keep the [F#] and [C#] citation markers from the phase write-ups exactly as written, next to each point you take from them.';

function buildPriorPhaseContextForPhase7() {
    if (!currentInsightsData || !currentInsightsData.phases) return '';

    const sections = [];
    for (let i = 1; i <= 6; i++) {
        const key = String(i);
        const phase = currentInsightsData.phases[key];
        if (!phase) continue;
        const summary = typeof phase.summary === 'string' ? phase.summary.trim() : '';
        if (!summary || summary === 'MISSING') continue;

        const title = phase.title || PHASE_DEFINITIONS[key]?.title || `Phase ${key}`;
        sections.push(`## Phase ${key}: ${title}\n\n${summary}`);
    }

    if (sections.length === 0) return '';
    const merged = sections.join('\n\n---\n\n');
    const MAX_CONTEXT_CHARS = 120000;
    const clipped = merged.length > MAX_CONTEXT_CHARS ? merged.slice(0, MAX_CONTEXT_CHARS) : merged;
    return `${KEEP_CITATION_MARKERS}\n\n${clipped}`;
}

function inferFieldConfidence(value) {
    const v = (value || '').toString().trim();
    if (!v) return 'none';
    const lv = v.toLowerCase();
    if (
        lv === 'missing' ||
        lv === 'n/a' ||
        lv === 'na' ||
        lv.includes('not stated') ||
        lv.includes('not provided') ||
        lv.includes('not available') ||
        lv.includes('unknown')
    ) {
        return 'none';
    }
    return 'medium';
}

function parseSuggestedTopicsFromSection(section) {
    if (!section || typeof section !== 'string') return [];
    const marker = /(?:^|\n)\s*(?:[-*]\s*)?(?:\*\*)?Suggested Topics(?:\*\*)?\s*:?\s*/i;
    const mm = marker.exec(section);
    if (!mm) return [];

    const after = section.slice(mm.index + mm[0].length);
    const topics = [];
    const lines = after.split('\n');
    for (const raw of lines) {
        const line = raw.trim();
        if (!line) {
            if (topics.length > 0) break;
            continue;
        }
        if (/^##+/.test(line)) break;
        if (/^[-*]\s*(?:\*\*)?(?:Price|Fee|Tuition|Duration|USP|Differentiator|Details)(?:\*\*)?\s*:/i.test(line)) break;
        const m = line.match(/^[-*]\s+(.+)$/);
        if (!m) continue;
        const topic = m[1].replace(/\*\*/g, '').trim();
        if (!topic) continue;
        if (!topics.includes(topic)) topics.push(topic);
        if (topics.length >= 3) break;
    }
    return topics;
}

function parseCompetitorsFromMarkdown(text) {
    if (!text || typeof text !== 'string') return [];
    const md = text.replace(/\r\n/g, '\n');
    const competitors = [];
    const seen = new Set();

    const addCompetitor = (comp) => {
        if (!comp || !comp.name) return;
        const key = comp.name.trim().toLowerCase();
        if (!key || key === 'missing' || seen.has(key)) return;
        seen.add(key);
        competitors.push(comp);
    };

    const headingRegex = /^##+\s*(?:Competitor\s*:?\s*)?(.+?)\s*$/gim;
    const sections = [];
    let m;
    while ((m = headingRegex.exec(md)) !== null) {
        const name = (m[1] || '').replace(/\*\*/g, '').trim();
        if (!name) continue;
        sections.push({ name, start: m.index + m[0].length });
    }

    if (sections.length > 0) {
        for (let i = 0; i < sections.length; i++) {
            const current = sections[i];
            const end = (i + 1 < sections.length) ? sections[i + 1].start : md.length;
            const section = md.slice(current.start, end).trim();

            const getField = (pattern) => {
                const re = new RegExp(`(?:^|\\n)\\s*[-*]\\s*(?:\\*\\*)?(?:${pattern})(?:\\*\\*)?\\s*:\\s*(.+)`, 'i');
                const fm = section.match(re);
                return fm ? fm[1].trim() : 'MISSING';
            };

            const price = getField('Price|Fee|Tuition');
            const duration = getField('Duration|Length');
            const usp = getField('USP|Differentiator|Positioning');
            const suggestedTopics = parseSuggestedTopicsFromSection(section);

            addCompetitor({
                name: current.name,
                price: price || 'MISSING',
                price_confidence: inferFieldConfidence(price),
                duration: duration || 'MISSING',
                duration_confidence: inferFieldConfidence(duration),
                usp: usp || 'MISSING',
                usp_confidence: inferFieldConfidence(usp),
                suggested_topics: suggestedTopics,
                details: section
            });
        }
    }

    if (competitors.length > 0) return competitors;

    // Fallback: parse markdown table rows.
    const tableLines = md
        .split('\n')
        .map(line => line.trim())
        .filter(line => line.startsWith('|') && line.endsWith('|'));

    if (tableLines.length >= 3) {
        const parseRow = (row) => row.split('|').slice(1, -1).map(cell => cell.trim());
        const header = parseRow(tableLines[0]).map(h => h.toLowerCase());
        const isSep = (cells) => cells.every(c => /^:?-{3,}:?$/.test(c));
        const sepCells = parseRow(tableLines[1]);
        if (isSep(sepCells)) {
            const idxName = header.findIndex(h => /name|competitor|provider/.test(h));
            const idxPrice = header.findIndex(h => /price|fee|tuition/.test(h));
            const idxDuration = header.findIndex(h => /duration|length/.test(h));
            const idxUsp = header.findIndex(h => /usp|differ|position/.test(h));
            const idxDetails = header.findIndex(h => /detail|notes|summary/.test(h));

            for (let i = 2; i < tableLines.length; i++) {
                const cells = parseRow(tableLines[i]);
                if (cells.length === 0 || isSep(cells)) continue;
                const name = idxName >= 0 ? (cells[idxName] || '').trim() : '';
                if (!name) continue;
                const price = idxPrice >= 0 ? (cells[idxPrice] || 'MISSING').trim() : 'MISSING';
                const duration = idxDuration >= 0 ? (cells[idxDuration] || 'MISSING').trim() : 'MISSING';
                const usp = idxUsp >= 0 ? (cells[idxUsp] || 'MISSING').trim() : 'MISSING';
                const details = idxDetails >= 0 ? (cells[idxDetails] || '').trim() : '';

                addCompetitor({
                    name,
                    price,
                    price_confidence: inferFieldConfidence(price),
                    duration,
                    duration_confidence: inferFieldConfidence(duration),
                    usp,
                    usp_confidence: inferFieldConfidence(usp),
                    suggested_topics: [],
                    details
                });
            }
        }
    }

    return competitors;
}

document.addEventListener('DOMContentLoaded', () => {
    initSidebarResize();
    const selector = document.getElementById('project-select');

    // Restore per-tab state from sessionStorage (survives refresh, isolated per tab)
    const saved = _restoreTabState();
    if (saved.project && selector) {
        // Verify the saved project still exists in the dropdown options
        const opts = Array.from(selector.options).map(o => o.value);
        if (opts.includes(saved.project)) {
            selector.value = saved.project;
        }
    }
    currentProject = selector ? selector.value : null;
    currentChat = saved.chat || null;
    _saveTabState();

    if (currentProject) loadProject();

    setupSelectionCapture();
    setupOutputEditor();
    const decBtn = document.querySelector("button[onclick*='decreaseFontSize']");
    if (decBtn) decBtn.textContent = "A-";
});

// ---------------------------------------------------------
// CORE DISPLAY FUNCTIONS
// ---------------------------------------------------------

function renderChat() {
    const chatMessages = document.getElementById('chat-messages');
    chatMessages.innerHTML = '';

    if (!currentChat || !projectData.chats[currentChat]) {
        chatMessages.innerHTML = '<div style="text-align:center; color:#888; margin-top:2rem;">Start a new session or select one from the left.</div>';
        return;
    }

    const messages = projectData.chats[currentChat].messages || [];
    
    messages.forEach((msg, index) => {
        // Check if this message is linked to an artifact (simple heuristic: exact content match)
        // In a production app, we'd store the artifact ID in the message metadata.
        const linkedArtifact = findLinkedArtifact(msg);

        if (msg.role === 'assistant' && linkedArtifact) {
            appendArtifactCard(linkedArtifact.id, linkedArtifact.name, linkedArtifact.content);
        } else {
            const msgDiv = appendMessage(msg.role, msg.content);
            
            // Add "Convert to Artifact" button for assistant messages that aren't yet cards
            if (msg.role === 'assistant') {
                const actionsDiv = document.createElement('div');
                actionsDiv.className = 'message-actions';
                actionsDiv.style.marginTop = "0.5rem";
                actionsDiv.style.display = "flex";
                actionsDiv.style.gap = "0.5rem";
                actionsDiv.style.justifyContent = "flex-end";
                
                actionsDiv.innerHTML = `
                    <button class="small-btn" onclick="convertMessageToArtifact(${index})">📄 Convert to Artifact</button>
                    <button class="small-btn" onclick="copyToClipboard(this.parentElement.parentElement.querySelector('.message-content').innerText)">📋 Copy</button>
                `;
                msgDiv.parentElement.appendChild(actionsDiv);
            }
        }
    });
}

function findLinkedArtifact(msg) {
    if (!projectData.artifacts) return null;
    if (msg && msg.artifact_id && projectData.artifacts[msg.artifact_id]) {
        return projectData.artifacts[msg.artifact_id];
    }
    if (!msg || !msg.content) return null;
    return Object.values(projectData.artifacts).find(a => a.content.trim() === msg.content.trim());
}

function appendMessage(role, text) {
    const chatMessages = document.getElementById('chat-messages');
    const div = document.createElement('div'); 
    div.className = `message ${role}`;
    const safeText = text ? text : "";
    
    div.innerHTML = `
        <div class="message-role">${role === 'user' ? 'You' : 'Architect'}</div>
        <div class="message-content markdown-body">${renderMarkdown(safeText)}</div>
    `;
    
    chatMessages.appendChild(div); 
    chatMessages.scrollTop = chatMessages.scrollHeight;
    return div.querySelector('.message-content');
}

function appendArtifactCard(id, name, content) {
    const chatMessages = document.getElementById('chat-messages');
    const div = document.createElement('div');
    div.className = 'message assistant';
    
    const preview = content ? content.substring(0, 150).replace(/\n/g, ' ') + (content.length > 150 ? '...' : '') : 'Empty artifact';
    const previewHtml = renderMarkdownInline(preview);
    
    div.innerHTML = `
        <div class="message-role">Architect</div>
        <div class="artifact-card" onclick="openArtifactEditor('${id}')">
            <div class="artifact-card-header">
                <span class="artifact-icon">📄</span>
                <span class="artifact-name">${escapeHtml(name)}</span>
            </div>
            <div class="artifact-card-preview markdown-body">${previewHtml}</div>
            <div class="artifact-card-footer">
                <span class="artifact-hint">Click to edit</span>
                <span class="artifact-size">${content ? content.length : 0} chars</span>
            </div>
        </div>
    `;
    
    chatMessages.appendChild(div);
    chatMessages.scrollTop = chatMessages.scrollHeight;
}

// ---------------------------------------------------------
// ARTIFACT MANAGEMENT
// ---------------------------------------------------------

async function convertMessageToArtifact(index) {
    const message = projectData.chats[currentChat]?.messages?.[index];
    if (!message) return;
    const messageContent = message.content;
    let title = "Generated Artifact";
    
    // Attempt to extract a title from the first line (common in markdown)
    const lines = messageContent.split('\n');
    const firstLine = lines[0].replace(/^[#\s]+/, '').trim();
    if (firstLine.length > 0 && firstLine.length < 60) {
        title = firstLine;
    }
    
    const name = prompt("Name this artifact:", title);
    if (!name) return;

    // Save
    const artifactId = await saveArtifactToServer(name, messageContent);
    if (!artifactId) return;
    await linkArtifactToMessage(index, artifactId);
    // Reload chat to show card
    renderChat();
}

async function linkArtifactToMessage(index, artifactId) {
    try {
        await fetch(`/api/chats/${currentChat}/link-artifact`, {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({ project: currentProject, index, artifact_id: artifactId })
        });
        if (projectData?.chats?.[currentChat]?.messages?.[index]) {
            projectData.chats[currentChat].messages[index].artifact_id = artifactId;
        }
    } catch (e) {
        console.error("Failed to link artifact to message", e);
    }
}

async function saveArtifactToServer(name, content, id = null, targetProject = null, createdAt = null) {
    const payload = {
        project: targetProject || currentProject,
        name: name,
        content: content,
        created_at: createdAt || new Date().toISOString()
    };
    if (id) payload.id = id;

    try {
        const res = await fetch('/api/artifacts', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify(payload)
        });
        if (!res.ok) {
            let errMsg = `Server error (${res.status})`;
            try { const err = await res.json(); if (err.error) errMsg = err.error; } catch (_) {}
            throw new Error(errMsg);
        }
        const data = await res.json();

        if (data.success) {
            await loadProject({reloadInsights: false});
            if (document.getElementById('compiled-tab').classList.contains('active')) {
                renderArtifactsTab();
            }
            return data.artifact_id;
        }
    } catch (e) {
        alert("Failed to save artifact: " + e.message);
        return null;
    }
}

async function deleteArtifact(id) {
    if (!confirm("Are you sure you want to delete this artifact?")) return;
    try {
        await fetch(`/api/artifacts/${id}?project=${encodeURIComponent(currentProject)}`, { method: 'DELETE' });
        await loadProject({reloadInsights: false});
        renderArtifactsTab();
        if (currentArtifactId === id) closeOutputView();
        renderChat(); // Refresh chat to remove card view if needed
    } catch (e) {
        console.error(e);
    }
}

function renderArtifactsTab() {
    const container = document.getElementById('compiled-content');
    if (!projectData.artifacts || Object.keys(projectData.artifacts).length === 0) {
        container.innerHTML = `
            <div class="empty-state">
                <p>No artifacts created yet.</p>
                <p>Turn on "Artifact Mode" in the Builder or click "Convert to Artifact" on any chat message.</p>
            </div>`;
        return;
    }

    let html = `<div class="competitor-grid">`;
    const sorted = Object.values(projectData.artifacts).sort((a,b) => new Date(b.created_at) - new Date(a.created_at));

    sorted.forEach(art => {
        const preview = art.content.substring(0, 150) + (art.content.length > 150 ? '...' : '');
        html += `
            <div class="competitor-card" onclick="openArtifactEditor('${art.id}')" style="cursor:pointer">
                <div class="comp-header">
                    <h4>${escapeHtml(art.name)}</h4>
                    <div class="comp-duration" style="color:rgba(255,255,255,0.8); font-size:0.75rem;">
                        ${new Date(art.created_at).toLocaleDateString()}
                    </div>
                </div>
                <div class="comp-body">
                    <div style="font-family:'JetBrains Mono', monospace; font-size:0.8rem; color:#666; height:80px; overflow:hidden;">
                        ${escapeHtml(preview)}
                    </div>
                    <div style="margin-top:auto; padding-top:1rem; display:flex; justify-content:space-between; align-items:center; border-top:1px solid #eee;">
                        <span class="artifact-hint">Click to edit</span>
                        <button class="icon-btn" onclick="event.stopPropagation(); deleteArtifact('${art.id}')" title="Delete">🗑️</button>
                    </div>
                </div>
            </div>
        `;
    });
    html += `</div>`;
    container.innerHTML = html;
}

function setupOutputEditor() {
    const editor = document.getElementById('output-editor');
    if (!editor) return;
    if (!outputDefaultFontSize) {
        outputDefaultFontSize = window.getComputedStyle(editor).fontSize;
    }
    editor.addEventListener('input', () => {
        currentOutputRaw = outputPreview ? htmlToMarkdown(editor.innerHTML) : editor.innerText;
    });
    editor.addEventListener('click', (event) => {
        if (!outputPreview) return;
        const link = event.target.closest('a[href]');
        if (!link || !editor.contains(link)) return;
        event.preventDefault();
        event.stopPropagation();
        const href = link.getAttribute('href');
        if (href) {
            window.open(href, '_blank', 'noopener,noreferrer');
        }
    });
}

function isMarkdownFile(name) {
    if (!name) return false;
    const lower = name.toLowerCase();
    return lower.endsWith(".md") || lower.endsWith(".markdown") || lower.endsWith(".txt");
}

function setFileEditorContent(name, content) {
    const editor = document.getElementById('file-editor-content');
    if (!editor) return;
    fileEditorWysiwyg = isMarkdownFile(name);
    fileEditorRaw = content || "";
    if (fileEditorWysiwyg) {
        editor.contentEditable = "true";
        editor.classList.add('markdown-body');
        editor.innerHTML = renderMarkdown(fileEditorRaw);
    } else {
        editor.contentEditable = "true";
        editor.classList.remove('markdown-body');
        editor.innerText = fileEditorRaw;
    }
}

function getFileEditorContent() {
    const editor = document.getElementById('file-editor-content');
    if (!editor) return fileEditorRaw || "";
    if (fileEditorWysiwyg) {
        fileEditorRaw = htmlToMarkdown(editor.innerHTML);
    } else {
        fileEditorRaw = editor.innerText;
    }
    return fileEditorRaw || "";
}

function updateOutputPreviewButton() {
    const btn = document.getElementById('output-preview-toggle');
    if (!btn) return;
    btn.innerText = outputPreview ? "Raw" : "Render";
}

function updateOutputEditorView() {
    const editor = document.getElementById('output-editor');
    if (!editor) return;
    if (outputPreview) {
        editor.contentEditable = "true";
        editor.classList.add('markdown-body');
        editor.innerHTML = renderMarkdown(currentOutputRaw);
        decorateOutputTables(editor);
    } else {
        editor.contentEditable = "true";
        editor.classList.remove('markdown-body');
        editor.innerText = currentOutputRaw;
    }
    const markBtn = document.getElementById('mark-btn');
    if (markBtn) {
        markBtn.disabled = false;
        markBtn.title = "Edit Selection";
    }
    updateOutputPreviewButton();
}

function setOutputEditorContent(content) {
    currentOutputRaw = content || "";
    updateOutputEditorView();
}

function toggleOutputPreview() {
    outputPreview = !outputPreview;
    updateOutputEditorView();
}

function decorateOutputTables(editor) {
    if (!editor || !outputPreview) return;
    const tables = Array.from(editor.querySelectorAll('table'));
    tables.forEach((table, idx) => {
        if (!table.dataset.tableEditorId) {
            table.dataset.tableEditorId = `table_${Date.now()}_${idx}`;
        }
        table.setAttribute('contenteditable', 'false');
        table.classList.add('table-edit-locked');

        let wrapper = table.parentElement;
        if (!wrapper || !wrapper.classList.contains('table-edit-wrapper')) {
            wrapper = document.createElement('div');
            wrapper.className = 'table-edit-wrapper';
            table.parentNode.insertBefore(wrapper, table);
            wrapper.appendChild(table);
        }
        wrapper.setAttribute('contenteditable', 'false');

        let btn = wrapper.querySelector('.table-edit-btn');
        if (!btn) {
            btn = document.createElement('button');
            btn.type = 'button';
            btn.className = 'small-btn table-edit-btn';
            btn.textContent = 'Edit Table';
            wrapper.insertBefore(btn, table);
        }
        btn.setAttribute('contenteditable', 'false');
        btn.onclick = (e) => {
            e.preventDefault();
            e.stopPropagation();
            openTableEditor(table.dataset.tableEditorId);
        };
    });
}

function getOutputTableById(tableId) {
    const editor = document.getElementById('output-editor');
    if (!editor || !tableId) return null;
    return editor.querySelector(`table[data-table-editor-id="${tableId}"]`);
}

function getTableMatrixAndHeader(tableEl) {
    if (!tableEl) return { matrix: [['']], hasHeader: true };
    const rows = Array.from(tableEl.querySelectorAll('tr'));
    const matrix = normalizeTableMatrix(
        rows.map(row => Array.from(row.querySelectorAll('th, td')).map(extractCellText))
    );
    const hasHeader = !!tableEl.querySelector('thead tr') || !!tableEl.querySelector('th');
    if (!matrix.length) return { matrix: [['']], hasHeader: true };
    return { matrix, hasHeader };
}

function ensureTableEditorModal() {
    let modal = document.getElementById('table-editor-modal');
    if (modal) return modal;

    modal = document.createElement('div');
    modal.id = 'table-editor-modal';
    modal.className = 'modal';
    modal.innerHTML = `
        <div class="modal-content large-modal table-editor-modal-content">
            <div class="modal-header">
                <h3>Edit Table</h3>
                <button class="icon-btn" id="table-editor-close-btn">&#x2715;</button>
            </div>
            <div class="table-editor-toolbar">
                <label class="table-header-toggle">
                    <input type="checkbox" id="table-editor-has-header" checked>
                    Use first row as header
                </label>
                <div class="table-editor-actions">
                    <button class="small-btn" id="table-editor-add-row-btn">+ Row</button>
                    <button class="small-btn" id="table-editor-add-col-btn">+ Column</button>
                    <button class="small-btn" id="table-editor-remove-row-btn">- Row</button>
                    <button class="small-btn" id="table-editor-remove-col-btn">- Column</button>
                </div>
            </div>
            <div id="table-editor-grid-host" class="table-editor-grid-host"></div>
            <div class="button-group">
                <button class="btn-primary" id="table-editor-apply-btn">Apply Table</button>
                <button class="btn-secondary" id="table-editor-cancel-btn">Cancel</button>
            </div>
        </div>
    `;
    document.body.appendChild(modal);

    const close = () => closeTableEditor();
    modal.querySelector('#table-editor-close-btn').addEventListener('click', close);
    modal.querySelector('#table-editor-cancel-btn').addEventListener('click', close);

    modal.querySelector('#table-editor-has-header').addEventListener('change', (e) => {
        if (!activeTableEditorState) return;
        activeTableEditorState.hasHeader = !!e.target.checked;
    });
    modal.querySelector('#table-editor-add-row-btn').addEventListener('click', addTableEditorRow);
    modal.querySelector('#table-editor-add-col-btn').addEventListener('click', addTableEditorColumn);
    modal.querySelector('#table-editor-remove-row-btn').addEventListener('click', removeTableEditorRow);
    modal.querySelector('#table-editor-remove-col-btn').addEventListener('click', removeTableEditorColumn);
    modal.querySelector('#table-editor-apply-btn').addEventListener('click', applyTableEditorChanges);

    return modal;
}

function readTableEditorMatrixFromGrid() {
    const host = document.getElementById('table-editor-grid-host');
    if (!host) return [];
    const rows = Array.from(host.querySelectorAll('tr'));
    return rows.map(row => Array.from(row.querySelectorAll('input')).map(input => input.value || ''));
}

function renderTableEditorGrid() {
    const host = document.getElementById('table-editor-grid-host');
    if (!host || !activeTableEditorState) return;
    host.innerHTML = '';

    const matrix = normalizeTableMatrix(activeTableEditorState.matrix);
    activeTableEditorState.matrix = matrix;

    const table = document.createElement('table');
    table.className = 'table-editor-grid';

    matrix.forEach((row, rIdx) => {
        const tr = document.createElement('tr');
        if (activeTableEditorState.hasHeader && rIdx === 0) tr.className = 'table-editor-header-row';

        row.forEach((cell, cIdx) => {
            const td = document.createElement('td');
            const input = document.createElement('input');
            input.type = 'text';
            input.value = cell || '';
            input.dataset.row = String(rIdx);
            input.dataset.col = String(cIdx);
            td.appendChild(input);
            tr.appendChild(td);
        });
        table.appendChild(tr);
    });

    host.appendChild(table);
}

function openTableEditor(tableId) {
    const table = getOutputTableById(tableId);
    if (!table) return;

    const parsed = getTableMatrixAndHeader(table);
    activeTableEditorState = {
        tableId,
        hasHeader: parsed.hasHeader,
        matrix: parsed.matrix
    };

    const modal = ensureTableEditorModal();
    const headerToggle = modal.querySelector('#table-editor-has-header');
    if (headerToggle) headerToggle.checked = !!activeTableEditorState.hasHeader;
    renderTableEditorGrid();
    modal.classList.add('active');
}

function closeTableEditor() {
    const modal = document.getElementById('table-editor-modal');
    if (modal) modal.classList.remove('active');
    activeTableEditorState = null;
}

function addTableEditorRow() {
    if (!activeTableEditorState) return;
    const matrix = readTableEditorMatrixFromGrid();
    const colCount = matrix.length ? matrix[0].length : 1;
    matrix.push(new Array(colCount).fill(''));
    activeTableEditorState.matrix = matrix;
    renderTableEditorGrid();
}

function addTableEditorColumn() {
    if (!activeTableEditorState) return;
    let matrix = readTableEditorMatrixFromGrid();
    if (!matrix.length) matrix = [['']];
    matrix.forEach(row => row.push(''));
    activeTableEditorState.matrix = matrix;
    renderTableEditorGrid();
}

function removeTableEditorRow() {
    if (!activeTableEditorState) return;
    const matrix = readTableEditorMatrixFromGrid();
    if (matrix.length <= 1) return;
    matrix.pop();
    activeTableEditorState.matrix = matrix;
    renderTableEditorGrid();
}

function removeTableEditorColumn() {
    if (!activeTableEditorState) return;
    const matrix = readTableEditorMatrixFromGrid();
    const colCount = matrix.length ? matrix[0].length : 0;
    if (colCount <= 1) return;
    matrix.forEach(row => row.pop());
    activeTableEditorState.matrix = matrix;
    renderTableEditorGrid();
}

function buildHtmlTableFromState(matrix, hasHeader, tableId) {
    const normalized = normalizeTableMatrix(matrix);
    const table = document.createElement('table');
    table.dataset.tableEditorId = tableId;

    const addRow = (rowValues, isHeader) => {
        const tr = document.createElement('tr');
        rowValues.forEach(value => {
            const cell = document.createElement(isHeader ? 'th' : 'td');
            cell.textContent = value || '';
            tr.appendChild(cell);
        });
        return tr;
    };

    if (hasHeader && normalized.length > 0) {
        const thead = document.createElement('thead');
        thead.appendChild(addRow(normalized[0], true));
        table.appendChild(thead);
    }

    const tbody = document.createElement('tbody');
    const startIdx = (hasHeader && normalized.length > 0) ? 1 : 0;
    for (let i = startIdx; i < normalized.length; i++) {
        tbody.appendChild(addRow(normalized[i], false));
    }
    if (tbody.children.length === 0) {
        const colCount = normalized[0] ? normalized[0].length : 1;
        tbody.appendChild(addRow(new Array(colCount).fill(''), false));
    }
    table.appendChild(tbody);
    return table;
}

function applyTableEditorChanges() {
    if (!activeTableEditorState) return;
    const editor = document.getElementById('output-editor');
    if (!editor) return;

    const matrix = readTableEditorMatrixFromGrid();
    activeTableEditorState.matrix = matrix;

    const oldTable = getOutputTableById(activeTableEditorState.tableId);
    if (!oldTable) {
        closeTableEditor();
        return;
    }

    const newTable = buildHtmlTableFromState(
        activeTableEditorState.matrix,
        !!activeTableEditorState.hasHeader,
        activeTableEditorState.tableId
    );
    oldTable.replaceWith(newTable);

    decorateOutputTables(editor);
    currentOutputRaw = htmlToMarkdown(editor.innerHTML);
    closeTableEditor();
}

function openArtifactEditor(id) {
    const art = projectData.artifacts[id];
    if (!art) return;

    // Switch to Builder tab first — the output overlay lives inside write-tab
    showTab('write');

    currentArtifactId = id;
    currentSourceFilename = null;
    outputPreview = true;
    setOutputEditorContent(art.content);
    document.getElementById('chapter-nav-title').textContent = art.name;
    document.getElementById('output-view').classList.add('active');
    
    // IMPORTANT: Update the "Save" button to be "Update Artifact"
    const saveBtn = document.querySelector('.editor-panel .button-group-inline button:last-child');
    if (saveBtn) {
        saveBtn.innerText = "💾 Update Artifact";
        saveBtn.onclick = updateCurrentArtifact;
    }
}

function openOutputWithContent(title, content) {
    currentArtifactId = null;
    currentSourceFilename = null;
    outputPreview = true;
    setOutputEditorContent(content || "");
    document.getElementById('chapter-nav-title').textContent = title || "Output";
    document.getElementById('output-view').classList.add('active');

    const saveBtn = document.querySelector('.editor-panel .button-group-inline button:last-child');
    if (saveBtn) {
        saveBtn.innerText = "Save to Files";
        saveBtn.onclick = saveOutput;
    }
}

async function updateCurrentArtifact() {
    if (!currentArtifactId) {
        // Fallback to old "Save to File" behavior if no artifact is loaded
        saveOutput(); 
        return;
    }
    
    const content = getOutputContent();
    const name = document.getElementById('chapter-nav-title').textContent;
    
    const btn = document.querySelector('.editor-panel .button-group-inline button:last-child');
    const originalText = btn.innerText;
    btn.innerText = "Saving...";
    
    await saveArtifactToServer(name, content, currentArtifactId);
    
    btn.innerText = "✅ Saved";
    setTimeout(() => { btn.innerText = originalText; }, 1500);
}

function openSourceFileInArtifactView(filename) {
    if (!filename || !projectData?.files?.[filename]) return;
    showTab('write');
    currentArtifactId = null;
    currentSourceFilename = filename;
    outputPreview = true;
    setOutputEditorContent(projectData.files[filename] || "");
    document.getElementById('chapter-nav-title').textContent = filename;
    document.getElementById('output-view').classList.add('active');

    const saveBtn = document.querySelector('.editor-panel .button-group-inline button:last-child');
    if (saveBtn) {
        saveBtn.innerText = "Update Source File";
        saveBtn.onclick = updateCurrentSourceFile;
    }
}

async function updateCurrentSourceFile() {
    if (!currentSourceFilename) {
        saveOutput();
        return;
    }
    const content = getOutputContent();
    const btn = document.querySelector('.editor-panel .button-group-inline button:last-child');
    const originalText = btn.innerText;
    btn.innerText = "Saving...";
    try {
        await fetch('/api/files', {
            method:'POST',
            headers:{'Content-Type':'application/json'},
            body: JSON.stringify({ filename: currentSourceFilename, content, project: currentProject })
        });
        await loadProject({reloadInsights: false});
        btn.innerText = "✅ Saved";
    } catch (e) {
        btn.innerText = "Save Failed";
    } finally {
        setTimeout(() => { btn.innerText = originalText; }, 1500);
    }
}

async function migrateArtifactsToFiles() {
    try {
        const res = await fetch('/api/artifacts/migrate', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({ project: currentProject })
        });
        const data = await res.json();
        if (data.success) {
            await loadProject({reloadInsights: false});
            alert(`Migrated ${data.migrated} artifacts to files.`);
        } else {
            alert("Migration failed.");
        }
    } catch (e) {
        alert("Migration failed: " + e.message);
    }
}

// ---------------------------------------------------------
// INSIGHTS DASHBOARD LOGIC 
// ---------------------------------------------------------
let currentInsightsData = null;
let insightsHistory = [];
let currentInsightsVersion = null;
let insightsCache = {}; // Per-project insights state: { projectName: { data, history, version, statusHtml, viewingHistoricalVersion } }
// Single source of truth for whether the insights UI is currently showing a
// frozen historical version (vs. the live/current data).
let viewingHistoricalVersion = false;

// --- Per-Project Insights Cache ---
function saveInsightsToCache(project) {
    if (!project) return;
    const statusEl = document.getElementById('insights-status');
    insightsCache[project] = {
        data: currentInsightsData,
        history: insightsHistory,
        version: currentInsightsVersion,
        statusHtml: statusEl ? statusEl.innerHTML : '',
        viewingHistoricalVersion: viewingHistoricalVersion
    };
}

function restoreInsightsFromCache(project) {
    const cached = insightsCache[project];
    if (!cached || !cached.data) return false;
    currentInsightsData = cached.data;
    insightsHistory = cached.history || [];
    currentInsightsVersion = cached.version || null;
    // Restore whatever historical/live view state this project was left in,
    // so switching projects mid-history-browse doesn't leak the live task
    // panel onto still-historical data (or vice versa).
    viewingHistoricalVersion = !!cached.viewingHistoricalVersion;
    renderInsights(currentInsightsData);
    const statusEl = document.getElementById('insights-status');
    if (statusEl && cached.statusHtml) statusEl.innerHTML = cached.statusHtml;
    updateInsightsVersionNav();
    return true;
}

function updateCacheForProject(project, data) {
    if (!project) return;
    const existing = insightsCache[project] || {};
    existing.data = data;
    existing.statusHtml = `Latest — ${new Date(data.generated_at).toLocaleString()} <button onclick="downloadInsights()" class="small-btn">JSON</button>`;
    insightsCache[project] = existing;
}

function resetInsightsUI() {
    currentInsightsData = null;
    viewingHistoricalVersion = false;
    const grid = document.getElementById('competitor-grid');
    if (grid) {
        grid.innerHTML = `<div class="empty-state">Upload files and click 'Generate Insights' to analyse competitors.</div>`;
    }
    const phasesContainer = document.getElementById('insight-phases');
    if (phasesContainer) phasesContainer.innerHTML = '';
    const statusEl = document.getElementById('insights-status');
    if (statusEl) statusEl.textContent = "No insights generated yet";
    const downloadJsonBtn = document.getElementById('download-json-btn');
    const downloadReportBtn = document.getElementById('download-report-btn');
    if (downloadJsonBtn) downloadJsonBtn.style.display = 'none';
    if (downloadReportBtn) downloadReportBtn.style.display = 'none';
    const nav = document.getElementById('insights-version-nav');
    if (nav) nav.style.display = 'none';
    // The fact search box and its results belong to the project that was showing; clear them on a switch.
    if (typeof evidenceResetSearch === 'function') evidenceResetSearch();
    renderExcludedCompetitors();
    syncInsightsControlButtons();
}

// --- Toast Notifications ---
function showToast(message, switchToProject) {
    const container = document.getElementById('toast-container');
    if (!container) return;
    const toast = document.createElement('div');
    toast.className = 'toast-notification';
    const textSpan = document.createElement('span');
    textSpan.textContent = message;
    toast.appendChild(textSpan);
    if (switchToProject) {
        const btn = document.createElement('button');
        btn.textContent = 'Switch';
        btn.className = 'toast-switch-btn';
        btn.onclick = (e) => {
            e.stopPropagation();
            const selector = document.getElementById('project-select');
            if (selector) { selector.value = switchToProject; switchProject(); }
            toast.remove();
        };
        toast.appendChild(btn);
    }
    const closeBtn = document.createElement('button');
    closeBtn.innerHTML = '&times;';
    closeBtn.className = 'toast-close-btn';
    closeBtn.onclick = () => toast.remove();
    toast.appendChild(closeBtn);
    container.appendChild(toast);
    // Auto-dismiss after 8 seconds
    setTimeout(() => { if (toast.parentNode) toast.remove(); }, 8000);
}

// --- Floating Insights Indicator ---
function showInsightsIndicator(text) {
    const indicator = document.getElementById('insights-indicator');
    if (!indicator) return;
    indicator.style.display = 'block';
    document.getElementById('insights-indicator-text').textContent = text || 'Generating insights...';
}

function hideInsightsIndicator() {
    const indicator = document.getElementById('insights-indicator');
    if (indicator) indicator.style.display = 'none';
}

// --- Insights Version Management ---
async function saveInsightsHistory(data, project) {
    const targetProject = project || currentProject;
    let history = [];
    if (targetProject === currentProject && projectData.files && projectData.files['insights_history.json']) {
        try {
            history = JSON.parse(projectData.files['insights_history.json']);
            if (!Array.isArray(history)) history = [];
        } catch(e) { history = []; }
    } else if (targetProject !== currentProject) {
        // Cross-project: fetch existing history from the API to avoid overwriting it
        try {
            const res = await fetch(`/api/projects/${encodeURIComponent(targetProject)}?set_current=0&t=${Date.now()}`);
            const pd = await res.json();
            if (pd.files && pd.files['insights_history.json']) {
                history = JSON.parse(pd.files['insights_history.json']);
                if (!Array.isArray(history)) history = [];
            }
        } catch(e) { history = []; }
    }

    history.push({
        version: history.length + 1,
        generated_at: data.generated_at || new Date().toISOString(),
        data: data
    });

    try {
        await fetch('/api/files', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({
                project: targetProject,
                filename: 'insights_history.json',
                content: JSON.stringify(history, null, 2)
            })
        });
    } catch(err) { console.error("Failed to save insights history:", err); }

    // Only update local state if saving to the current project
    if (targetProject === currentProject) {
        insightsHistory = history;
        currentInsightsVersion = history.length;
        updateInsightsVersionNav();
    }
}

function loadInsightsHistory() {
    insightsHistory = [];
    currentInsightsVersion = null;

    if (projectData.files && projectData.files['insights_history.json']) {
        try {
            const parsed = JSON.parse(projectData.files['insights_history.json']);
            if (Array.isArray(parsed) && parsed.length > 0) {
                insightsHistory = parsed;
                currentInsightsVersion = parsed.length;
            }
        } catch(e) { console.error("Failed to parse insights history:", e); }
    }

    // Auto-seed history only when we have meaningful generated content
    if (insightsHistory.length === 0 && hasMeaningfulInsightsContent(currentInsightsData)) {
        insightsHistory = [{
            version: 1,
            generated_at: currentInsightsData.generated_at || new Date().toISOString(),
            data: currentInsightsData
        }];
        currentInsightsVersion = 1;
        fetch('/api/files', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({
                project: currentProject,
                filename: 'insights_history.json',
                content: JSON.stringify(insightsHistory, null, 2)
            })
        }).catch(err => console.error("Failed to seed insights history:", err));
    }

    updateInsightsVersionNav();
}

function updateInsightsVersionNav() {
    const nav = document.getElementById('insights-version-nav');
    const label = document.getElementById('insights-version-label');
    const prevBtn = document.getElementById('insights-prev-ver');
    const nextBtn = document.getElementById('insights-next-ver');
    if (!nav) return;

    if (insightsHistory.length <= 0) {
        nav.style.display = 'none';
        return;
    }

    nav.style.display = 'flex';
    label.textContent = `v${currentInsightsVersion} of ${insightsHistory.length}`;
    prevBtn.disabled = currentInsightsVersion <= 1;
    nextBtn.disabled = currentInsightsVersion >= insightsHistory.length;
    prevBtn.style.opacity = prevBtn.disabled ? '0.3' : '1';
    nextBtn.style.opacity = nextBtn.disabled ? '0.3' : '1';
}

function viewPreviousInsightsVersion() {
    if (currentInsightsVersion <= 1) return;
    currentInsightsVersion--;
    const entry = insightsHistory[currentInsightsVersion - 1];
    currentInsightsData = entry.data;
    viewingHistoricalVersion = true;
    renderInsights(entry.data);

    const statusEl = document.getElementById('insights-status');
    if (statusEl) {
        statusEl.innerHTML = `Version ${entry.version} — ${new Date(entry.generated_at).toLocaleString()} (historical)`;
    }
    updateInsightsVersionNav();
}

function viewNextInsightsVersion() {
    if (currentInsightsVersion >= insightsHistory.length) return;
    currentInsightsVersion++;
    const entry = insightsHistory[currentInsightsVersion - 1];
    currentInsightsData = entry.data;
    viewingHistoricalVersion = true;
    renderInsights(entry.data);

    const statusEl = document.getElementById('insights-status');
    const isLatest = currentInsightsVersion === insightsHistory.length;
    if (statusEl) {
        statusEl.innerHTML = `${isLatest ? 'Latest' : 'Version ' + entry.version} — ${new Date(entry.generated_at).toLocaleString()}${isLatest ? '' : ' (historical)'} <button onclick="downloadInsights()" class="small-btn">📥 JSON</button>`;
    }
    updateInsightsVersionNav();
}

async function ensureChatSession() {
    if (currentChat) return true;
    try {
        const res = await fetch('/api/chats', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({project: currentProject})
        });
        const data = await res.json();
        if (data.success) {
            currentChat = data.chat_id;
            await loadProject({reloadInsights: false});
            return true;
        }
    } catch(e) { console.error('[insights] ensureChatSession failed:', e); }
    return false;
}

function ensureInsightsData() {
    if (!currentInsightsData) {
        currentInsightsData = createEmptyInsightsData();
    }
}

function resetInsightGenerationUI() {
    // Only reset indicator/buttons if no other generation is still in flight
    if (!isAnyInsightGenerationActive()) {
        hideInsightsIndicator();
        const mainBtn = document.getElementById('insight-btn');
        if (mainBtn) { mainBtn.innerText = "Refresh All"; mainBtn.disabled = false; }
    }
    syncInsightsControlButtons();
    // Re-render to reset any individual button loading states
    if (currentInsightsData) renderInsights(currentInsightsData);
}

async function generateInsights(userInstructions) {
    const btn = document.getElementById('insight-btn');
    const statusEl = document.getElementById('insights-status');
    if (getActiveGenerationForProject(currentProject)) {
        alert('An insight generation is already running for this project. Please wait for it to complete.');
        return;
    }

    ensureInsightsData();
    const phaseKeys = ['1', '2', '3', '4', '5', '6'];
    const runnablePhaseKeys = phaseKeys.filter(k => {
        const phase = currentInsightsData?.phases?.[k] || {};
        const ids = Array.isArray(phase.linked_file_ids) ? phase.linked_file_ids : [];
        const names = Array.isArray(phase.linked_files) ? phase.linked_files : [];
        return ids.length > 0 || names.length > 0;
    });

    if (runnablePhaseKeys.length === 0) {
        if (statusEl) statusEl.innerHTML = `<span style="color:#dc3545;">Link at least one source file to a phase before running Refresh All.</span>`;
        alert('Refresh All needs linked files. Link source files to one or more phases first.');
        return;
    }

    if (btn) { btn.innerText = "Running..."; btn.disabled = true; }
    showInsightsIndicator('Running linked phases...');
    if (statusEl) statusEl.textContent = `Running linked phases (${runnablePhaseKeys.join(', ')})...`;

    try {
        for (const phaseKey of runnablePhaseKeys) {
            // Do not continue batch if user switched project mid-run.
            if (!currentProject) break;
            await generatePhaseInsight(phaseKey, userInstructions);
            if (statusEl && /cancelled/i.test(statusEl.textContent || statusEl.innerText || '')) {
                return;
            }
        }

        // Run Phase 7 consolidation if there is enough prior phase output.
        if (buildPriorPhaseContextForPhase7()) {
            await generatePhaseInsight('7', userInstructions);
        }

        if (statusEl) statusEl.innerHTML = `Refresh complete — ${new Date().toLocaleString()}`;
    } catch(e) {
        console.error('[insights] refresh-all batch error:', e);
        if (statusEl) statusEl.innerHTML = `<span style="color:#dc3545;">Error: ${e.message}</span>`;
    } finally {
        if (btn) { btn.innerText = "Refresh All"; btn.disabled = false; }
        hideInsightsIndicator();
        syncInsightsControlButtons();
    }
}

async function generateCompetitorLandscape() {
    const statusEl = document.getElementById('insights-status');
    const btn = document.getElementById('competitor-generate-btn');
    if(btn) { btn.innerText = "Analysing..."; btn.disabled = true; }
    showInsightsIndicator('Analysing competitor landscape...');
    if(statusEl) statusEl.textContent = "AI is analysing competitor landscape...";

    const excluded = getExcludedCompetitors();
    const exclusionClause = excluded.length
        ? `\nEXCLUDED — Do NOT include these: ${excluded.join(', ')}.`
        : '';

    const prompt = `
SYSTEM: You are a senior Strategy Consultant specialising in Australian Higher Education. Write in a McKinsey-quality consulting tone. Use Australian English throughout.
CONTEXT: You may ONLY use the provided Source Data files.
TASK: Extract the competitive landscape — competitors, pricing, duration, and USPs.
CRITICAL INSTRUCTIONS:
1. If a piece of information is NOT explicitly stated, write exactly: MISSING.
2. DO NOT hallucinate. DO NOT use general knowledge. DO NOT guess.
3. Return Markdown only (no JSON).
4. Use this structure exactly for each competitor:
## Competitor: <Name>
- Price: <value or MISSING>
- Duration: <value or MISSING>
- USP: <value or MISSING>
- Suggested Topics:
  - <topic 1>
  - <topic 2>
5. If no competitors are found, return exactly: MISSING.${exclusionClause}`;

    if (getActiveGenerationForProject(currentProject)) {
        alert('An insight generation is already running for this project. Please wait for it to complete.');
        if(btn) { btn.innerText = "Generate Competitors"; btn.disabled = false; }
        hideInsightsIndicator();
        return;
    }

    const genId = newGenerationId();
    activeInsightGenerations[genId] = {
        type: 'competitors',
        project: currentProject,
        overrideFiles: null,
        insightsContext: null,
        insightsDataSnapshot: JSON.parse(JSON.stringify(currentInsightsData || {})),
        abortController: null
    };
    syncInsightsControlButtons();

    const input = document.getElementById('chat-input');
    input.value = prompt;
    try {
        await sendMessage(genId);
    } catch(e) {
        console.error('[insights] competitor generation error:', e);
        delete activeInsightGenerations[genId];
        resetInsightGenerationUI();
        if(statusEl) statusEl.innerHTML = `<span style="color:#dc3545;">Error: ${e.message}</span>`;
    }
}

async function generatePhaseInsight(phaseKey, userInstructions) {
    const def = PHASE_DEFINITIONS[phaseKey];
    if (!def) return;
    const statusEl = document.getElementById('insights-status');
    const btn = document.getElementById(`phase-generate-btn-${phaseKey}`);
    if(btn) { btn.innerText = "Analysing..."; btn.disabled = true; }
    showInsightsIndicator(`Analysing Phase ${phaseKey}: ${def.title}...`);
    if(statusEl) statusEl.textContent = `AI is analysing Phase ${phaseKey}: ${def.title}...`;

    const phaseGuidance = {
        "1": `ANALYSIS FOCUS for Phase 1 — The Landscape (HIGH-LEVEL ONLY):
- Market environment overview: size/demand signals, provider categories, high-level competitive structure.
- Programme landscape snapshot: common programme formats and positioning patterns (not deep feature breakdown).
- External context: major regulatory/policy forces shaping the market.
- Price and duration bands: directional ranges only (do not go provider-by-provider in depth).
- Top-line implications: what this environment means strategically for OES.
- Keep this phase as a macro view of the environment, not a tactical deep dive.`,
        "2": `ANALYSIS FOCUS for Phase 2 — The Student:
Synthesise the source data into a STRUCTURED STUDENT PERSONA covering:
- PERSONA ARCHETYPE: Name and describe the typical student for this program type.
- DEMOGRAPHIC PROFILE: Age range, work experience, gender balance, nationality split (domestic vs international + source countries), industry backgrounds. Cite HEIMS, QILT, ABS, GMAC data where present.
- MOTIVATIONS: Career advancement, career pivot, credibility/formalisation, entrepreneurship, personal growth. Rank by prevalence.
- CORE VALUES (Iron Triangle): Flexibility, Practicality, ROI — how students weigh each.
- STUDY BEHAVIOURS: Weekly hours, preferred formats (async/live/modular), collaboration patterns, support needs, digital campus expectations.
- CHALLENGES: Work-life-study strain, digital isolation concerns, quality scepticism, financial pressure.
- CAREER ASPIRATIONS: Target roles, salary benchmarks (AUD), in-demand skills (core, future-focused, soft skills), graduate outcome data.
- PERSONA SNAPSHOT: 2-3 sentence essence summary.
All claims must be grounded in source data. Mark unsupported dimensions as MISSING.`,
        "3": `ANALYSIS FOCUS for Phase 3 — Review of Marketing:
Synthesise across three sub-dimensions:
A) COMPARATIVE MARKETING ANALYSIS:
- Pricing models and cost positioning across competitors.
- Speed vs prestige positioning: accelerated/ROI-focused vs alumni networks/rankings/brand equity.
- Digital marketing channels: SEO, SEM, social media, influencer partnerships, review sites.
- International vs domestic targeting strategies.
- Alumni employability outcomes and career claims.
- Digital content: YouTube, blogs, reports — frequency and quality.
B) WEBSITE REVIEW:
- Page structure, hero section, information hierarchy, CTA placement.
- Positioning language, target audience signals, key differentiators.
- Program features as displayed: duration, fees, entry requirements, specialisations, delivery mode.
- Credibility signals: accreditations, rankings, QILT/GOS data, alumni outcomes.
- Transparency: what's public vs gated, ease of finding key information.
- Feature comparison grid across providers.
C) SENTIMENT ANALYSIS & SOCIAL LISTENING:
- Platform-specific sentiment: Reddit, Whirlpool, YouTube, The Conversation, Google Reviews.
- Per-provider sentiment profile: positive themes, negative themes, volume, representative quotes.
- Cross-provider differentiators and USPs from student voice (not marketing claims).
- Sentiment-derived mini-personas: who chooses each provider and why.
- Competitive switching insights: why students choose one over another.`,
        "4": `ANALYSIS FOCUS for Phase 4 — Product Features:
Synthesise source data into a structured, apples-to-apples product comparison covering these 10 data domains per provider:
1. IDENTITY & AUDIENCE: Provider name, exact program title, positioning snippet, target segment.
2. STRUCTURE: Total units, credit points, core/elective split, specialisations, capstone type, assessment mix.
3. DELIVERY & WORKLOAD: Mode (online/blended), block length, weekly hours, synchronous requirements, intensives, class size.
4. CALENDAR & PACING: Intakes list, entry points per year, standard/min duration, max completion, concurrent unit limits.
5. ADMISSIONS & RPL: Academic requirements, work experience, English requirements, RPL allowance and caps, articulation paths.
6. FEES & FUNDING: Per-unit and per-credit-point fees (AUD), total indicative tuition, mandatory costs, FEE-HELP, scholarships.
7. QUALITY SIGNALS: Accreditations, rankings (publisher + year), QILT/GOS satisfaction, outcomes claims, alumni network.
8. CONVERSION & TRANSPARENCY: What's gated vs public, info session CTAs.
9. EVIDENCE: Primary URL, supporting URLs, key verbatim quote.
10. GAPS & CONFIDENCE: Missing data with explanation, confidence rating.
Normalise all fees to AUD. Flag derived calculations. Mark unverifiable claims as "Not stated on site".`,
        "5": `ANALYSIS FOCUS for Phase 5 — Academic Content:
Synthesise across two sub-dimensions:
A) COURSE STRUCTURE & ACADEMIC DIFFERENTIATORS (cross-competitor):
- Course structure overview: core/elective split, credit points, sequencing, stackable credentials, capstone design.
- Academic differentiators: industry tools in curriculum, WIL modules, real-world projects, AI/digital integration, innovation components, professional development add-ons.
- Curriculum philosophy: pedagogical approach, theory vs application balance, assessment philosophy, curriculum currency.
- Teaching signals: faculty vs practitioners, guest lecturers, peer learning design, learning technology, engagement design.
- Accreditation and academic standing: professional bodies, TEQSA, industry endorsements, dual-award arrangements.
- Comparative analysis: side-by-side structure, academic USPs, unique features, curriculum gaps.
B) UNIT-BY-UNIT DEEP DIVE (per provider where data exists):
- Unit identity: code, title, credit points, core/elective classification, prerequisites.
- Unit content: overview, learning outcomes, teaching approach, delivery format, tools/software used.
- Assessment design: task types, weightings, individual vs group, real-world or industry-partnered assessments.
- Differentiation signals: what makes individual units distinctive or innovative.
- Summary: curriculum selling points, gaps, innovation signals.
Note: Unit-level data is resource-intensive. Prioritise providers where handbook data is available.`,
        "6": `ANALYSIS FOCUS for Phase 6 — Industry Engagement:
Synthesise source data into a forensic-level comparative analysis covering:
1. PARTNERSHIP PORTFOLIO: Per provider, identify key industry partners and CLASSIFY each engagement type — governance/advisory, experiential learning (WIL/consulting projects), content contribution (guest lectures, case studies), recruitment pipeline, joint R&D, financial sponsorship, technology partnerships. Do not just list names.
2. CO-CREATION & CURRICULUM INTEGRATION: Evidence of genuine industry co-creation vs marketing claims. Did partners co-design units or modules? Specific examples of curriculum influenced by partnerships.
3. QUANTIFIABLE METRICS: Student participation rates in industry projects/WIL, hiring conversion rates, partner contribution levels, graduate employment data linked to engagement activities.
4. CAREER SERVICES COMPARISON: Operational models (in-house, centralised, outsourced), specific services and technologies, employer engagement programs, alignment with target demographic.
5. WIL DEEP DIVE: Types available, mandatory vs optional, credit-bearing, duration, online delivery adaptations, industry partner involvement, student outcomes.
6. PROFESSIONAL BODY AFFILIATIONS: Accreditations, memberships, student benefits (exemptions, fast-tracked membership).
Produce per-provider analysis plus a cross-provider comparison table. Distinguish verified evidence from marketing claims.`,
        "7": `ANALYSIS FOCUS for Phase 7 — Options for OES:
CRITICAL CONTEXT: OES develops and manages programs for university partners. OES never brands programs — they are university-branded. Do NOT reference OES as an OPM.

This is the capstone synthesis. Draw on ALL prior phase data in the source files to produce:

1. KNOWLEDGE BASE SYNTHESIS: Distil the strategic signals from Phases 1-6. Not a repeat — a tight summary of what matters.

2. WHITE SPACE ANALYSIS: Specific market gaps — unoccupied price points, underserved segments, missing formats (accelerated, stackable, micro-credentials), delivery innovations, industry engagement models. For each: market size signal, defensibility, OES alignment.

3. MARKET TARGETING: Which segment to target, ideal university partner profile (Go8/ATN/regional/specialist), positioning on price-prestige spectrum, demand signals.

4. DIFFERENTIATION STRATEGIES FOR ONLINE HED: Curriculum design, delivery model, student experience, industry engagement, technology, pricing/accessibility. Each grounded in evidence from prior phases.

5. WHAT / SO WHAT / NOW WHAT (CENTREPIECE):
For each key finding across all phases:
- WHAT: The finding, stated with evidence
- SO WHAT: Strategic implication, risk of inaction, interaction with other findings
- NOW WHAT: Specific action, responsible party, priority (critical/high/medium/low), timeline, dependencies
Group by theme: Market Position, Curriculum Design, Student Experience, Industry Engagement, Marketing, Technology.

6. STRATEGIC OPTIONS SUMMARY: Top 5-7 options ranked by impact/feasibility, risk assessment, quick wins vs long-term plays, minimum viable differentiation package.

Write as an experienced HED professional. Be direct and opinionated. Every recommendation traceable to source data.`
    };

    const guidance = phaseGuidance[phaseKey] || '';
    const phaseBoundaries = {
        "1": {
            in_scope: [
                "Macro environment, demand signals, provider landscape categories",
                "High-level programme typology and directional price/duration bands",
                "Regulatory and policy context, top-line strategic implications"
            ],
            out_scope: [
                "Detailed student persona analysis (Phase 2)",
                "Channel-by-channel marketing critique (Phase 3)",
                "Unit-level curriculum, pedagogy, assessment design (Phase 5)",
                "Partnership operations and WIL execution details (Phase 6)"
            ],
            required_headings: [
                "## Executive Summary",
                "## Market Environment Overview",
                "## Competitive Structure",
                "## External Forces",
                "## Strategic Implications for OES"
            ]
        },
        "2": {
            in_scope: [
                "Target student archetypes, motivations, barriers, decision criteria",
                "Demographic and behavioural profile grounded in provided data"
            ],
            out_scope: [
                "Detailed competitor marketing tactics (Phase 3)",
                "Product feature matrix and fee mechanics (Phase 4)",
                "Curriculum design and unit-level critique (Phase 5)"
            ],
            required_headings: [
                "## Executive Summary",
                "## Student Archetype",
                "## Motivations and Barriers",
                "## Decision Drivers",
                "## Strategic Implications for OES"
            ]
        },
        "3": {
            in_scope: [
                "Messaging, channel strategy, positioning, website communication, sentiment themes"
            ],
            out_scope: [
                "Deep programme structure comparison (Phase 4)",
                "Academic content critique (Phase 5)",
                "Industry engagement mechanisms (Phase 6)"
            ],
            required_headings: [
                "## Executive Summary",
                "## Positioning and Messaging",
                "## Channel and Website Review",
                "## Sentiment Signals",
                "## Strategic Implications for OES"
            ]
        },
        "4": {
            in_scope: [
                "Programme features, structure, delivery model, fees, admissions, transparency"
            ],
            out_scope: [
                "Detailed marketing channel analysis (Phase 3)",
                "Unit-level pedagogy and assessment critique (Phase 5)",
                "Partnership portfolio evaluation (Phase 6)"
            ],
            required_headings: [
                "## Executive Summary",
                "## Product Feature Comparison",
                "## Delivery and Pricing Architecture",
                "## Conversion and Transparency Signals",
                "## Strategic Implications for OES"
            ]
        },
        "5": {
            in_scope: [
                "Academic design, curriculum structure, learning outcomes, pedagogy, assessment signals"
            ],
            out_scope: [
                "Marketing performance and positioning tactics (Phase 3)",
                "Operational partnership/commercial engagement analysis (Phase 6)"
            ],
            required_headings: [
                "## Executive Summary",
                "## Curriculum Structure",
                "## Learning and Assessment Design",
                "## Academic Differentiators",
                "## Strategic Implications for OES"
            ]
        },
        "6": {
            in_scope: [
                "Industry engagement models, partnerships, affiliations, career pathway links"
            ],
            out_scope: [
                "Detailed curriculum architecture (Phase 5)",
                "General market landscape recap (Phase 1)",
                "Marketing channel performance (Phase 3)"
            ],
            required_headings: [
                "## Executive Summary",
                "## Partnership Landscape",
                "## Engagement Mechanisms",
                "## Career and Professional Pathways",
                "## Strategic Implications for OES"
            ]
        },
        "7": {
            in_scope: [
                "Cross-phase synthesis, white space, strategic choices, prioritised recommendations"
            ],
            out_scope: [
                "Re-running full detailed analysis from earlier phases"
            ],
            required_headings: [
                "## Executive Summary",
                "## Knowledge Base Synthesis",
                "## White Space Analysis",
                "## What / So What / Now What",
                "## Strategic Options for OES"
            ]
        }
    };
    const boundary = phaseBoundaries[phaseKey] || { in_scope: [], out_scope: [], required_headings: [] };
    const phaseLengthTarget = phaseKey === '1'
        ? '450-800 words'
        : (phaseKey === '7' ? '1000-1500 words' : '700-1100 words');

    let prompt = `
SYSTEM: You are a senior Strategy Consultant specialising in Australian Higher Education, producing a client-ready strategic intelligence report for OES (Online Education Services). Write in a McKinsey-quality consulting tone — authoritative, evidence-based, and action-oriented. Use Australian English throughout (e.g. "analyse", "organised", "programme" where appropriate to context, "recognised").

CONTEXT: You may ONLY use the provided Source Data files.
TASK: Provide a deep, structured analysis for Phase ${phaseKey}: ${def.title}.
PHASE DESCRIPTION: ${def.description}.
${guidance}

WRITING & FORMATTING REQUIREMENTS:
- The "summary" field MUST contain well-structured Markdown with clear visual hierarchy.
- Use ## headings to separate major analytical sections (e.g. "## Executive Summary", "## Key Findings", "## Strategic Implications").
- Use ### sub-headings for thematic groupings within sections.
- Use bullet points (- ) and numbered lists (1. ) to present discrete findings, comparisons, and recommendations.
- Use **bold** to emphasise key terms, metrics, and critical insights.
- Include a brief **Executive Summary** (2-3 sentences) at the top of the summary that captures the headline finding and its strategic significance.
- Close with a **Strategic Implications** or **So What** section that connects findings to actionable meaning for OES.
- Write in flowing professional prose between structured elements — do not produce a wall of bullet points. Blend narrative paragraphs with structured lists.
- Ensure each section has analytical depth: not merely describing what the data says, but interpreting what it means, why it matters, and what tensions or opportunities it reveals.
- Aim for the level of a polished consulting deliverable that could be presented to a university Vice-Chancellor.
- Keep it concise and decision-useful. Target length: ${phaseLengthTarget}.
- REQUIRED section order: ${boundary.required_headings.join(' | ')}.

PHASE BOUNDARY CONTRACT (STRICT):
IN SCOPE ONLY:
${boundary.in_scope.map(x => `- ${x}`).join('\n')}
OUT OF SCOPE (DO NOT ANALYSE IN THIS PHASE):
${boundary.out_scope.map(x => `- ${x}`).join('\n')}
- If source material includes out-of-scope detail, do not analyse it here. Mention briefly under a single bullet: "Deferred to later phase".

CRITICAL INSTRUCTIONS:
1. If information is NOT explicitly stated in the source data, say so clearly — do not fabricate.
2. DO NOT hallucinate. DO NOT use general knowledge. Every claim must be traceable to the source files.
3. Return Markdown only (no JSON).
4. If no reliable evidence exists in the provided sources, return exactly: MISSING`;

    // Asked before the briefing is fetched, so a second click does not fetch it for nothing, and again once it has
    // arrived, because another generation can start while it loads.
    const refuseIfRunning = () => {
        if (!getActiveGenerationForProject(currentProject)) return false;
        alert('An insight generation is already running for this project. Please wait for it to complete.');
        if(btn) { btn.innerText = "Generate"; btn.disabled = false; }
        hideInsightsIndicator();
        return true;
    };

    // Facts first (spec section 4): when this phase has checked facts, the server's facts briefing follows the
    // guidance. A phase without facts, or a failed request, leaves the prompt exactly as before.
    const briefProject = currentProject;
    if (typeof evidencePhaseBriefAddition === 'function') {
        if (refuseIfRunning()) return;
        prompt += await evidencePhaseBriefAddition(briefProject, phaseKey);
        if (currentProject !== briefProject) {  // the person switched project while the briefing loaded
            if (btn) { btn.innerText = "Generate"; btn.disabled = false; }
            hideInsightsIndicator();
            return;
        }
    }

    if (userInstructions && userInstructions.trim()) {
        prompt += `\n\nUSER INSTRUCTIONS:\n${userInstructions.trim()}`;
    }

    if (refuseIfRunning()) return;

    // Enforce explicit phase scoping:
    // - Phases 1-6: must have linked source files.
    // - Phase 7: consolidates prior phases only (no raw file context).
    ensureInsightsData();
    // Re-normalize against current project files/index so stale cross-project links
    // cannot slip into generation.
    normalizeInsightsData(currentInsightsData, getAvailableSourceFiles(), getFileIndexIdToName());
    const phaseData = currentInsightsData.phases[phaseKey] || {};
    const linkedFiles = Array.isArray(phaseData.linked_files) ? phaseData.linked_files : [];
    let linkedFileIds = Array.isArray(phaseData.linked_file_ids) ? phaseData.linked_file_ids : [];
    if (linkedFileIds.length === 0 && linkedFiles.length > 0) {
        linkedFileIds = linkedFiles.map(getFileIdByName).filter(Boolean);
        currentInsightsData.phases[phaseKey].linked_file_ids = linkedFileIds;
    }
    let overrideFileIds = null;
    let overrideFiles = null;
    let insightsContext = null;

    if (phaseKey === '7') {
        insightsContext = buildPriorPhaseContextForPhase7();
        if (!insightsContext) {
            if (btn) { btn.innerText = "Generate"; btn.disabled = false; }
            hideInsightsIndicator();
            if (statusEl) statusEl.innerHTML = `<span style="color:#dc3545;">Phase 7 requires completed content in Phases 1-6.</span>`;
            alert("Phase 7 requires completed summaries in Phases 1-6. Generate those first.");
            return;
        }
        // Explicitly pass [] so backend does not fall back to selected_files.
        overrideFileIds = [];
        overrideFiles = [];
    } else {
        const idToName = getFileIndexIdToName();
        const resolvedLinkedNames = linkedFileIds.map(id => idToName[id]).filter(Boolean);
        const effectiveLinkedNames = resolvedLinkedNames.length > 0 ? resolvedLinkedNames : linkedFiles;

        if (effectiveLinkedNames.length === 0) {
            if (btn) { btn.innerText = "Generate"; btn.disabled = false; }
            hideInsightsIndicator();
            if (statusEl) statusEl.innerHTML = `<span style="color:#dc3545;">No valid linked source files for this phase. Re-link files and try again.</span>`;
            alert(`Phase ${phaseKey} requires at least one linked source file.`);
            return;
        }
        if (linkedFileIds.length > 0) {
            overrideFileIds = linkedFileIds;
        } else {
            overrideFiles = linkedFiles;
        }
    }

    const genId = newGenerationId();
    activeInsightGenerations[genId] = {
        type: `phase-${phaseKey}`,
        project: currentProject,
        overrideFileIds: overrideFileIds,
        overrideFiles: overrideFiles,
        insightsContext: insightsContext,
        insightsDataSnapshot: JSON.parse(JSON.stringify(currentInsightsData || {})),
        abortController: null
    };
    syncInsightsControlButtons();

    const input = document.getElementById('chat-input');
    input.value = prompt;
    try {
        await sendMessage(genId);
    } catch(e) {
        console.error(`[insights] phase ${phaseKey} generation error:`, e);
        delete activeInsightGenerations[genId];
        resetInsightGenerationUI();
        if(statusEl) statusEl.innerHTML = `<span style="color:#dc3545;">Error: ${e.message}</span>`;
    }
}

async function handleInsightResponse(text, genId) {
    const genCtx = getGenerationContext(genId);
    const genType = genCtx ? genCtx.type : 'all';
    const originProject = genCtx ? genCtx.project : currentProject;
    const snapshot = genCtx ? genCtx.insightsDataSnapshot : {};

    // Clean up the generation entry immediately
    if (genId) delete activeInsightGenerations[genId];

    hideInsightsIndicator();
    const statusEl = document.getElementById('insights-status');

    // If the user switched projects while generation was running,
    // save to the originating project but don't update the current UI
    const projectMismatch = originProject !== currentProject;
    if (projectMismatch) {
        console.warn(`[insights] Generation completed for "${originProject}" but current project is "${currentProject}". Saving to originating project.`);
    }

    // Determine target data: use live currentInsightsData if same project, snapshot if different
    let targetInsightsData;
    if (!projectMismatch) {
        ensureInsightsData();
        targetInsightsData = currentInsightsData;
    } else {
        targetInsightsData = snapshot && Object.keys(snapshot).length > 0 ? snapshot : {};
    }

    try {
        let jsonStr = "";
        const codeBlockMatch = text.match(/```json\s*([\s\S]*?)\s*```/);
        if (codeBlockMatch) jsonStr = codeBlockMatch[1].trim();
        if (!jsonStr) {
            const firstBrace = text.indexOf('{');
            const lastBrace = text.lastIndexOf('}');
            if (firstBrace !== -1 && lastBrace !== -1) jsonStr = text.substring(firstBrace, lastBrace + 1);
        }

        let data = null;
        let parseError = null;
        if (jsonStr) {
            try {
                data = JSON.parse(jsonStr);
            } catch (e) {
                parseError = e;
            }
        } else {
            parseError = new Error("No JSON structure found");
        }

        const normalizationValidFiles = projectMismatch ? null : getAvailableSourceFiles();
        const normalizationFileIndex = projectMismatch ? null : getFileIndexIdToName();

        if (genType === 'competitors') {
            let parsedCompetitors = [];
            if (data && Array.isArray(data.competitors)) {
                parsedCompetitors = data.competitors;
            } else {
                parsedCompetitors = parseCompetitorsFromMarkdown(text);
            }
            targetInsightsData.competitors = parsedCompetitors;
            targetInsightsData.competitor_landscape_markdown = (text || '').trim();
            targetInsightsData.generated_at = new Date().toISOString();
            normalizeInsightsData(targetInsightsData, normalizationValidFiles, normalizationFileIndex);
        } else if (genType.startsWith('phase-')) {
            const phaseKey = genType.replace('phase-', '');
            if (!targetInsightsData.phases) targetInsightsData.phases = {};
            const existingLinked = targetInsightsData.phases[phaseKey]?.linked_files || [];
            const existingLinkedIds = targetInsightsData.phases[phaseKey]?.linked_file_ids || [];

            if (!data || typeof data !== 'object' || Array.isArray(data)) {
                // Phase generations are markdown-first now; tolerate non-JSON output.
                const fallbackSummary = (text || '').trim() || 'MISSING';
                data = {
                    title: PHASE_DEFINITIONS[phaseKey]?.title || `Phase ${phaseKey}`,
                    summary: fallbackSummary,
                    confidence: (fallbackSummary === 'MISSING' ? 'none' : 'medium'),
                    evidence_sources: existingLinked,
                    gaps: [],
                    suggested_topics: []
                };
            }

            targetInsightsData.phases[phaseKey] = data;
            targetInsightsData.phases[phaseKey].linked_files = existingLinked;
            targetInsightsData.phases[phaseKey].linked_file_ids = existingLinkedIds;
            targetInsightsData.generated_at = new Date().toISOString();
            normalizeInsightsData(targetInsightsData, normalizationValidFiles, normalizationFileIndex);
        } else {
            if (!data) throw parseError || new Error("Could not parse insights JSON");
            if (!data.generated_at) data.generated_at = new Date().toISOString();
            if (targetInsightsData && targetInsightsData.phases) {
                Object.entries(targetInsightsData.phases).forEach(([k, oldPhase]) => {
                    if (data.phases && data.phases[k] && oldPhase.linked_files && oldPhase.linked_files.length) {
                        data.phases[k].linked_files = oldPhase.linked_files;
                    }
                    if (data.phases && data.phases[k] && oldPhase.linked_file_ids && oldPhase.linked_file_ids.length) {
                        data.phases[k].linked_file_ids = oldPhase.linked_file_ids;
                    }
                });
            }
            normalizeInsightsData(data, normalizationValidFiles, normalizationFileIndex);
            targetInsightsData = data;
        }

        // Await saves to ensure insights are persisted to disk before any
        // subsequent loadProject() call (e.g. from file toggle) can reload.
        // skipReload=true because we already updated in-memory state above.
        await saveInsightsToFile(targetInsightsData, originProject, true);
        await saveInsightsHistory(targetInsightsData, originProject);

        // Update live state + UI if still on the same project
        if (!projectMismatch) {
            currentInsightsData = targetInsightsData;
            viewingHistoricalVersion = false;
            renderInsights(currentInsightsData);
            if(statusEl) statusEl.innerHTML = `Latest — ${new Date(currentInsightsData.generated_at).toLocaleString()} <button onclick="downloadInsights()" class="small-btn">JSON</button>`;
            // Keep cache in sync
            saveInsightsToCache(currentProject);
        } else {
            // Different project: update its cache so switching back is instant
            updateCacheForProject(originProject, targetInsightsData);
            showToast(`${originProject}: Insights generation complete`, originProject);
        }

    } catch (e) {
        resetInsightGenerationUI();
        alert("Could not parse AI response. Error: " + e.message);
    }
}

async function saveInsightsToFile(data, project, skipReload) {
    const targetProject = project || currentProject;
    try {
        await fetch('/api/files', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({
                project: targetProject,
                filename: 'insights.json',
                content: JSON.stringify(data, null, 2)
            })
        });
        // Reload project data (file list, chats, etc.) but never clobber
        // in-memory insights — the caller already has the authoritative copy.
        if (!skipReload && targetProject === currentProject) await loadProject({reloadInsights: false});
    } catch(err) { console.error("Failed to save insights:", err); }
}

function normalizeInsightsData(data, validFiles, fileIndexMap) {
    const validFileSet = Array.isArray(validFiles) ? new Set(validFiles) : null;
    const idToName = (fileIndexMap && typeof fileIndexMap === 'object')
        ? fileIndexMap
        : getFileIndexIdToName();
    const validIdSet = new Set(Object.keys(idToName));
    const nameToId = {};
    Object.entries(idToName).forEach(([fileId, filename]) => {
        if (typeof filename === 'string' && filename) nameToId[filename] = fileId;
    });
    // Normalize phases
    if (data.phases) {
        Object.values(data.phases).forEach(phase => {
            if (!phase.confidence) {
                phase.confidence = (!phase.summary || phase.summary === 'MISSING') ? 'none' : 'medium';
            }
            if (!Array.isArray(phase.evidence_sources)) phase.evidence_sources = [];
            if (!Array.isArray(phase.gaps)) phase.gaps = [];
            if (!Array.isArray(phase.suggested_topics)) phase.suggested_topics = [];
            if (!Array.isArray(phase.linked_files)) phase.linked_files = [];
            if (!Array.isArray(phase.linked_file_ids)) phase.linked_file_ids = [];

            // Backfill IDs from legacy linked_files names when possible.
            if (phase.linked_file_ids.length === 0 && phase.linked_files.length > 0) {
                phase.linked_file_ids = phase.linked_files
                    .map(name => nameToId[name])
                    .filter(Boolean);
            }

            const cleanedIds = [];
            const seenIds = new Set();
            phase.linked_file_ids.forEach(fileId => {
                if (typeof fileId !== 'string' || seenIds.has(fileId)) return;
                if (validIdSet.size > 0 && !validIdSet.has(fileId)) return;
                seenIds.add(fileId);
                cleanedIds.push(fileId);
            });
            phase.linked_file_ids = cleanedIds;

            const namesFromIds = phase.linked_file_ids
                .map(fileId => idToName[fileId])
                .filter(name => typeof name === 'string' && name.length > 0);
            const fallbackNames = phase.linked_files.filter(f => typeof f === 'string');
            const sourceNames = namesFromIds.length > 0 ? namesFromIds : fallbackNames;
            const cleanedNames = [];
            const seenNames = new Set();
            sourceNames.forEach(f => {
                if (seenNames.has(f)) return;
                if (validFileSet && !validFileSet.has(f)) return;
                seenNames.add(f);
                cleanedNames.push(f);
            });
            phase.linked_files = cleanedNames;
        });
    }
    // Normalize competitors
    if (Array.isArray(data.competitors)) {
        data.competitors.forEach(comp => {
            if (!comp.name) comp.name = "Unknown";
            if (!comp.price) comp.price = "MISSING";
            if (!comp.duration) comp.duration = "MISSING";
            if (!comp.usp) comp.usp = "MISSING";
            if (typeof comp.details !== 'string') comp.details = '';
            ['price', 'duration', 'usp'].forEach(field => {
                const confKey = field + '_confidence';
                if (!comp[confKey]) {
                    comp[confKey] = (!comp[field] || comp[field] === 'MISSING') ? 'none' : 'medium';
                }
            });
            if (!Array.isArray(comp.suggested_topics)) comp.suggested_topics = [];
        });
    }
    if (typeof data.competitor_landscape_markdown !== 'string') {
        data.competitor_landscape_markdown = '';
    }
}

function renderInsights(data) {
    const grid = document.getElementById('competitor-grid');
    const downloadJsonBtn = document.getElementById('download-json-btn');
    const downloadReportBtn = document.getElementById('download-report-btn');
    const hasContent = hasMeaningfulInsightsContent(data);
    if (downloadJsonBtn) downloadJsonBtn.style.display = hasContent ? 'inline-block' : 'none';
    if (downloadReportBtn) downloadReportBtn.style.display = hasContent ? 'inline-block' : 'none';

    // --- Competitor Generate Button ---
    const compBtnContainer = document.getElementById('competitor-section-actions');
    if (compBtnContainer) {
        compBtnContainer.innerHTML = `<button id="competitor-generate-btn" class="section-generate-btn" onclick="generateCompetitorLandscape()">Generate Competitors</button>`;
    }

    grid.innerHTML = '';

    if (data.competitors && data.competitors.length > 0) {
        data.competitors.forEach((comp, index) => {
            const hasDetails = comp.details && comp.details.trim();
            const enrichedClass = hasDetails ? ' enriched' : '';
            const badge = hasDetails ? '<span class="comp-enriched-badge">Detailed</span>' : '';
            grid.innerHTML += `
                <div class="competitor-card clickable-card${enrichedClass}" onclick="openCompetitorModal(${index})">
                    <div class="comp-header">
                        <div class="comp-header-left">
                            <h4 class="markdown-body">${renderMarkdownInline(stripOrphanedBoldMarkers(comp.name) || 'Unknown')} ${badge}</h4>
                            <div class="comp-duration markdown-body">${renderDataOrMarkdown('Duration', comp.duration, comp.name)} ${renderConfidenceBadge(comp.duration_confidence)}</div>
                        </div>
                    </div>
                    <div class="comp-body">
                        <div class="metric"><strong>Fee:</strong> <span class="markdown-body">${renderDataOrMarkdown('Pricing', comp.price, comp.name)}</span> ${renderConfidenceBadge(comp.price_confidence)}</div>
                        <div class="metric"><strong>USP:</strong> <span class="markdown-body">${renderDataOrMarkdown('USP', comp.usp, comp.name)}</span> ${renderConfidenceBadge(comp.usp_confidence)}</div>
                        ${renderSuggestedTopics(comp.suggested_topics)}
                    </div>
                </div>`;
        });
    } else {
        const landscape = (data.competitor_landscape_markdown || '').trim();
        if (landscape && landscape !== 'MISSING') {
            grid.innerHTML = `<div class="competitor-markdown-fallback markdown-body">${renderMarkdown(landscape)}</div>`;
        } else {
            grid.innerHTML = `<div class="empty-state"><p>No specific competitor data found. Click "Generate Competitors" above to analyse your source data.</p></div>`;
        }
    }

    // --- Phase Cards ---
    const phasesContainer = document.getElementById('insight-phases');
    phasesContainer.innerHTML = '';
    if (data.phases) {
        Object.entries(data.phases).forEach(([key, phase]) => {
            let summaryHtml = phase.summary;
            const confidenceBadge = renderConfidenceBadge(phase.confidence);
            const hasContent = summaryHtml && summaryHtml !== "MISSING";
            const btnLabel = hasContent ? 'Refresh' : 'Generate';
            const generateBtn = `<button class="phase-generate-btn" id="phase-generate-btn-${key}" onclick="event.stopPropagation(); showPhaseInstructionPrompt('${key}')">${btnLabel}</button>`;

            const evidenceSources = (phase.evidence_sources && phase.evidence_sources.length)
                ? `<div class="phase-evidence-sources">Sources: ${phase.evidence_sources.map(s => renderMarkdownInline(s)).join(', ')}</div>`
                : '';
            const gapsHtml = renderGaps(phase.gaps);
            const topicsHtml = renderSuggestedTopics(phase.suggested_topics);
            // A write-up that cites no facts says so above its text (spec section 5). evidence.js links the markers.
            const citeNote = hasContent && typeof evidenceNotCitedNote === 'function' ? evidenceNotCitedNote(phase.summary) : '';

            if (!hasContent) {
                summaryHtml = `<div class="missing-data-notice"><p>No data available for this phase.</p><button class="btn-research-small" onclick="event.stopPropagation(); triggerDeepResearch('${escapeAttr(phase.title)}')">Research Prompt</button></div>`;
            } else {
                summaryHtml = renderMarkdown(summaryHtml);
            }

            const clickToEdit = hasContent ? `onclick="openPhaseEditor('${key}')"` : '';
            const cursorStyle = hasContent ? 'style="cursor:pointer"' : '';

            phasesContainer.innerHTML += `
                <div class="insight-phase-card" id="phase-card-${key}" ${clickToEdit} ${cursorStyle}>
                    <div class="phase-header">
                        <span class="phase-num">Phase ${key}</span>
                        <h3>${renderMarkdownInline(phase.title || '')}</h3>
                        ${confidenceBadge}
                        ${generateBtn}
                    </div>
                    <div class="phase-content markdown-body"${hasContent ? ` data-cite-phase="${escapeAttr(key)}"` : ''}>
                        ${citeNote ? `<p class="ev-not-cited">${escapeHtml(citeNote)}</p>` : ''}
                        ${summaryHtml}
                        ${evidenceSources}
                        ${gapsHtml}
                        ${topicsHtml}
                    </div>
                    ${renderPhaseLinkedFiles(key, phase)}
                    ${hasContent ? '<div class="phase-edit-hint">Click to edit</div>' : ''}
                </div>
                ${viewingHistoricalVersion ? '' : `<div class="ev-slot" data-evidence-phase="${escapeAttr(key)}"></div>`}`;
        });
    }

    // Facts and conclusions sit below each phase card, outside it, so clicking them never opens the phase editor.
    // Facts are not versioned, so no slot is written while a past Insights version is showing; evidence.js then
    // finds nothing to fill.
    if (typeof evidenceRenderAll === 'function') evidenceRenderAll(currentProject);

    renderExcludedCompetitors();
    renderPhaseNavBar(data);
    setupPhaseNavObserver();
}

function escapeAttr(str) {
    return String(str).replace(/&/g,'&amp;').replace(/'/g,'&#39;').replace(/"/g,'&quot;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
}

function renderConfidenceBadge(confidence) {
    const colors = { high: '#28a745', medium: '#FF8A00', low: '#dc3545', none: '#999' };
    const labels = { high: 'High', medium: 'Medium', low: 'Low', none: 'No Data' };
    const c = confidence || 'none';
    return `<span class="confidence-badge confidence-${c}"><span class="confidence-dot" style="background:${colors[c] || '#999'}"></span>${labels[c] || 'No Data'}</span>`;
}

function renderSuggestedTopics(topics) {
    if (!topics || !topics.length) return '';
    const chips = topics.map(t => {
        const clean = t.replace(/\*\*(.+?)\*\*/g, '$1').replace(/__(.+?)__/g, '$1');
        return `<button class="topic-chip" onclick="triggerDeepResearch('${escapeAttr(clean)}')" title="Research this topic">${escapeHtml(clean)}</button>`;
    }).join('');
    return `<div class="suggested-topics"><span class="suggested-topics-label">Suggested Research:</span>${chips}</div>`;
}

function renderGaps(gaps) {
    if (!gaps || !gaps.length) return '';
    const items = gaps.map(g => `<li>${renderMarkdownInline(g)}</li>`).join('');
    return `<div class="data-gaps"><strong>Data Gaps:</strong><ul>${items}</ul></div>`;
}

function stripOrphanedBoldMarkers(text) {
    if (typeof text !== 'string') return text;
    // Strip leading "** " (orphaned bold opener the LLM sometimes produces)
    let cleaned = text.replace(/^\*\*\s+/, '');
    // Strip trailing orphaned " **"
    cleaned = cleaned.replace(/\s+\*\*$/, '');
    return cleaned;
}

function renderDataOrButton(label, value, context) {
    const cleaned = stripOrphanedBoldMarkers(value);
    if (!cleaned || cleaned === "MISSING" || cleaned.toLowerCase().includes("not mentioned")) {
        return `<button class="btn-research-small" onclick="triggerDeepResearch('${escapeAttr(label)} for ${escapeAttr(context)}')">⚡ Research</button>`;
    }
    return cleaned;
}

function renderDataOrMarkdown(label, value, context) {
    const html = renderDataOrButton(label, value, context);
    if (html.trim().startsWith("<button")) return html;
    return renderMarkdownInline(html);
}

function triggerDeepResearch(topic) {
    showTab('research');
    if (typeof boardOpenAddResearch === 'function') boardOpenAddResearch(topic);
}

// ---------------------------------------------------------
// PHASE NAVIGATION BAR
// ---------------------------------------------------------
let phaseNavObserver = null;

function renderPhaseNavBar(data) {
    const bar = document.getElementById('phase-nav-bar');
    if (!bar || !data || !data.phases) { if (bar) bar.innerHTML = ''; return; }
    bar.innerHTML = Object.entries(data.phases).map(([key, phase]) => {
        const hasContent = phase.summary && phase.summary !== 'MISSING';
        const dotClass = hasContent ? 'has-content' : 'empty';
        return `<button class="phase-nav-pill" data-phase="${key}" onclick="scrollToPhase('${key}')">
            <span class="phase-nav-dot ${dotClass}"></span>P${key}: ${renderMarkdownInline(phase.title || '')}
        </button>`;
    }).join('');
}

function scrollToPhase(phaseKey) {
    const card = document.getElementById(`phase-card-${phaseKey}`);
    if (!card) return;
    card.scrollIntoView({ behavior: 'smooth', block: 'start' });
    updateActiveNavPill(phaseKey);
}

function updateActiveNavPill(phaseKey) {
    document.querySelectorAll('.phase-nav-pill').forEach(pill => {
        pill.classList.toggle('active', pill.dataset.phase === phaseKey);
    });
}

function setupPhaseNavObserver() {
    if (phaseNavObserver) { phaseNavObserver.disconnect(); phaseNavObserver = null; }
    const container = document.querySelector('.story-map-container');
    const cards = document.querySelectorAll('.insight-phase-card[id^="phase-card-"]');
    if (!container || cards.length === 0) return;
    phaseNavObserver = new IntersectionObserver((entries) => {
        let bestKey = null;
        let bestRatio = 0;
        entries.forEach(entry => {
            if (entry.isIntersecting && entry.intersectionRatio > bestRatio) {
                bestRatio = entry.intersectionRatio;
                bestKey = entry.target.id.replace('phase-card-', '');
            }
        });
        if (bestKey) updateActiveNavPill(bestKey);
    }, { root: container, threshold: [0, 0.25, 0.5, 0.75, 1] });
    cards.forEach(card => phaseNavObserver.observe(card));
}

// ---------------------------------------------------------
// PHASE INSTRUCTION PROMPT (INLINE)
// ---------------------------------------------------------
function showPhaseInstructionPrompt(phaseKey) {
    closePhaseInstructionPrompt();
    const card = document.getElementById(`phase-card-${phaseKey}`);
    if (!card) { generatePhaseInsight(phaseKey); return; }
    const existing = card.querySelector('.phase-instruction-prompt');
    if (existing) { existing.remove(); return; }
    const phase = currentInsightsData?.phases?.[phaseKey];
    const hasContent = phase && phase.summary && phase.summary !== 'MISSING';
    const btnLabel = hasContent ? 'Refresh' : 'Generate';
    const div = document.createElement('div');
    div.className = 'phase-instruction-prompt';
    div.innerHTML = `
        <label>Optional instructions for Phase ${phaseKey}: ${PHASE_DEFINITIONS[phaseKey]?.title || ''}</label>
        <textarea class="phase-instruction-textarea" id="phase-instruction-input-${phaseKey}" rows="2" placeholder="e.g. Focus on pricing strategy, integrate the new UNSW document..."></textarea>
        <div class="phase-instruction-actions">
            <button class="phase-instruction-cancel" onclick="event.stopPropagation(); closePhaseInstructionPrompt()">Cancel</button>
            <button class="phase-instruction-generate" onclick="event.stopPropagation(); confirmPhaseGeneration('${phaseKey}')">${btnLabel}</button>
        </div>`;
    const linkedFilesSection = card.querySelector('.phase-linked-files');
    if (linkedFilesSection) {
        card.insertBefore(div, linkedFilesSection);
    } else {
        card.appendChild(div);
    }
    const textarea = div.querySelector('textarea');
    if (textarea) {
        textarea.focus();
        textarea.addEventListener('keydown', (e) => {
            if (e.ctrlKey && e.key === 'Enter') { e.preventDefault(); confirmPhaseGeneration(phaseKey); }
            if (e.key === 'Escape') { e.preventDefault(); closePhaseInstructionPrompt(); }
        });
    }
    div.addEventListener('click', (e) => e.stopPropagation());
}

function closePhaseInstructionPrompt() {
    document.querySelectorAll('.phase-instruction-prompt').forEach(el => el.remove());
}

function confirmPhaseGeneration(phaseKey) {
    const textarea = document.getElementById(`phase-instruction-input-${phaseKey}`);
    const instructions = textarea ? textarea.value.trim() : '';
    closePhaseInstructionPrompt();
    generatePhaseInsight(phaseKey, instructions || undefined);
}

// ---------------------------------------------------------
// REFRESH ALL INSTRUCTIONS MODAL
// ---------------------------------------------------------
function showRefreshAllInstructionModal() {
    if (getActiveGenerationForProject(currentProject)) {
        alert('An insight generation is already running for this project. Please wait for it to complete.');
        return;
    }
    const modal = document.getElementById('refresh-all-instructions-modal');
    const textarea = document.getElementById('refresh-all-instructions');
    if (textarea) textarea.value = '';
    if (modal) modal.classList.add('active');
    if (textarea) {
        textarea.focus();
        textarea.addEventListener('keydown', function handler(e) {
            if (e.ctrlKey && e.key === 'Enter') { e.preventDefault(); confirmRefreshAll(); textarea.removeEventListener('keydown', handler); }
            if (e.key === 'Escape') { e.preventDefault(); closeRefreshAllModal(); textarea.removeEventListener('keydown', handler); }
        });
    }
}

function closeRefreshAllModal() {
    const modal = document.getElementById('refresh-all-instructions-modal');
    if (modal) modal.classList.remove('active');
}

function confirmRefreshAll() {
    const textarea = document.getElementById('refresh-all-instructions');
    const instructions = textarea ? textarea.value.trim() : '';
    closeRefreshAllModal();
    generateInsights(instructions || undefined);
}

// ---------------------------------------------------------
// COMPETITOR DETAIL MODAL
// ---------------------------------------------------------
let compModalIndex = null;
let compDetailsPreview = false;
let compDetailsRaw = "";

function openCompetitorModal(index) {
    if (!currentInsightsData || !currentInsightsData.competitors) return;
    const comp = currentInsightsData.competitors[index];
    if (!comp) return;
    compModalIndex = index;
    compDetailsPreview = false;

    document.getElementById('comp-modal-name').value = (comp.name && comp.name !== 'MISSING') ? comp.name : '';
    document.getElementById('comp-modal-price').value = (comp.price && comp.price !== 'MISSING') ? comp.price : '';
    document.getElementById('comp-modal-duration').value = (comp.duration && comp.duration !== 'MISSING') ? comp.duration : '';
    document.getElementById('comp-modal-usp').value = (comp.usp && comp.usp !== 'MISSING') ? comp.usp : '';

    compDetailsRaw = comp.details || '';
    const detailsEl = document.getElementById('comp-modal-details');
    detailsEl.contentEditable = "true";
    detailsEl.classList.remove('markdown-body');
    detailsEl.innerText = compDetailsRaw;

    const toggleBtn = document.getElementById('comp-details-preview-toggle');
    if (toggleBtn) toggleBtn.innerText = 'Preview';

    document.getElementById('competitor-detail-modal').classList.add('active');
}

function closeCompetitorModal() {
    document.getElementById('competitor-detail-modal').classList.remove('active');
    compModalIndex = null;
    compDetailsPreview = false;
    compDetailsRaw = "";
}

function getCompDetailsContent() {
    const el = document.getElementById('comp-modal-details');
    if (!el) return compDetailsRaw;
    if (compDetailsPreview) {
        return compDetailsRaw;
    } else {
        compDetailsRaw = el.innerText;
        return compDetailsRaw;
    }
}

function toggleCompDetailsPreview() {
    const el = document.getElementById('comp-modal-details');
    const btn = document.getElementById('comp-details-preview-toggle');
    if (!el) return;

    if (!compDetailsPreview) {
        compDetailsRaw = el.innerText;
        el.contentEditable = "false";
        el.classList.add('markdown-body');
        el.innerHTML = renderMarkdown(compDetailsRaw);
        if (btn) btn.innerText = 'Raw';
        compDetailsPreview = true;
    } else {
        el.contentEditable = "true";
        el.classList.remove('markdown-body');
        el.innerText = compDetailsRaw;
        if (btn) btn.innerText = 'Preview';
        compDetailsPreview = false;
    }
}

async function saveCompetitorFromModal() {
    if (compModalIndex === null || !currentInsightsData) return;

    const comp = currentInsightsData.competitors[compModalIndex];
    comp.name = document.getElementById('comp-modal-name').value.trim() || 'Unknown';
    comp.price = document.getElementById('comp-modal-price').value.trim() || 'MISSING';
    comp.duration = document.getElementById('comp-modal-duration').value.trim() || 'MISSING';
    comp.usp = document.getElementById('comp-modal-usp').value.trim() || 'MISSING';
    comp.details = getCompDetailsContent().trim();

    renderInsights(currentInsightsData);
    await saveInsightsToFile(currentInsightsData);
    closeCompetitorModal();
}

async function deleteCompetitorFromModal() {
    if (compModalIndex === null || !currentInsightsData) return;
    const name = currentInsightsData.competitors[compModalIndex]?.name || 'this competitor';
    if (!confirm(`Delete "${name}"? This cannot be undone.\n\nThis competitor will also be excluded from future insight generations.`)) return;

    // Add to exclusion list so future generations skip this competitor
    await addExcludedCompetitor(name);

    currentInsightsData.competitors.splice(compModalIndex, 1);
    renderInsights(currentInsightsData);
    await saveInsightsToFile(currentInsightsData);
    closeCompetitorModal();
}

// --- Competitor Exclusion List ---

function getExcludedCompetitors() {
    try {
        if (projectData.files && projectData.files['excluded_competitors.json']) {
            const parsed = JSON.parse(projectData.files['excluded_competitors.json']);
            if (Array.isArray(parsed)) return parsed;
        }
    } catch(e) { console.error("Failed to parse excluded competitors:", e); }
    return [];
}

async function addExcludedCompetitor(name) {
    if (!name || name === 'this competitor' || name === 'MISSING') return;
    const list = getExcludedCompetitors();
    const normalised = name.trim();
    if (list.some(n => n.toLowerCase() === normalised.toLowerCase())) return;
    list.push(normalised);
    await saveExcludedCompetitors(list);
}

async function removeExcludedCompetitor(name) {
    let list = getExcludedCompetitors();
    list = list.filter(n => n.toLowerCase() !== name.toLowerCase());
    await saveExcludedCompetitors(list);
    renderExcludedCompetitors();
}

async function saveExcludedCompetitors(list) {
    try {
        await fetch('/api/files', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({
                project: currentProject,
                filename: 'excluded_competitors.json',
                content: JSON.stringify(list, null, 2)
            })
        });
        // Update local cache so getExcludedCompetitors() sees the change immediately
        if (!projectData.files) projectData.files = {};
        projectData.files['excluded_competitors.json'] = JSON.stringify(list, null, 2);
    } catch(e) { console.error("Failed to save excluded competitors:", e); }
}

function renderExcludedCompetitors() {
    const container = document.getElementById('excluded-competitors-list');
    if (!container) return;
    const list = getExcludedCompetitors();
    if (list.length === 0) {
        container.innerHTML = '';
        container.style.display = 'none';
        return;
    }
    container.style.display = 'block';
    const chips = list.map(name =>
        `<span class="excluded-chip">${escapeHtml(name)} <button onclick="removeExcludedCompetitor('${escapeAttr(name)}')" title="Re-include this competitor">&times;</button></span>`
    ).join('');
    container.innerHTML = `<div class="excluded-competitors-header">Excluded from future generations:</div>${chips}`;
}

// --- Phase Linked Files ---

function getAvailableSourceFiles() {
    const HIDDEN = ['insights.json', 'insights_history.json', 'excluded_competitors.json'];
    if (!projectData.files) return [];
    return Object.keys(projectData.files).filter(f => !HIDDEN.includes(f));
}

async function linkFileToPhase(phaseKey, filename) {
    if (!currentInsightsData || !currentInsightsData.phases[phaseKey]) return;
    const available = getAvailableSourceFiles();
    if (!available.includes(filename)) return;
    const fileId = getFileIdByName(filename);
    const phase = currentInsightsData.phases[phaseKey];
    if (!Array.isArray(phase.linked_files)) phase.linked_files = [];
    if (!Array.isArray(phase.linked_file_ids)) phase.linked_file_ids = [];
    if (phase.linked_files.includes(filename)) return;
    if (fileId && !phase.linked_file_ids.includes(fileId)) phase.linked_file_ids.push(fileId);
    phase.linked_files.push(filename);
    await saveInsightsToFile(currentInsightsData);
    renderInsights(currentInsightsData);
}

async function unlinkFileFromPhase(phaseKey, filename) {
    if (!currentInsightsData || !currentInsightsData.phases[phaseKey]) return;
    const phase = currentInsightsData.phases[phaseKey];
    const fileId = getFileIdByName(filename);
    if (!Array.isArray(phase.linked_files)) phase.linked_files = [];
    if (!Array.isArray(phase.linked_file_ids)) phase.linked_file_ids = [];
    phase.linked_files = phase.linked_files.filter(f => f !== filename);
    if (fileId) phase.linked_file_ids = phase.linked_file_ids.filter(id => id !== fileId);
    await saveInsightsToFile(currentInsightsData);
    renderInsights(currentInsightsData);
}

function renderPhaseLinkedFiles(phaseKey, phase) {
    const linked = phase.linked_files || [];
    const available = getAvailableSourceFiles().filter(f => !linked.includes(f));

    const chips = linked.map(f =>
        `<span class="linked-file-chip"><span class="linked-file-name" title="${escapeAttr(f)}">${escapeHtml(f)}</span><button onclick="event.stopPropagation(); unlinkFileFromPhase('${phaseKey}', '${escapeAttr(f)}')" title="Remove">&times;</button></span>`
    ).join('');

    const options = available.map(f =>
        `<option value="${escapeAttr(f)}">${escapeHtml(f)}</option>`
    ).join('');
    const dropdown = available.length
        ? `<select class="link-file-select" onchange="event.stopPropagation(); if(this.value){linkFileToPhase('${phaseKey}',this.value); this.value='';}"><option value="">+ Link source file...</option>${options}</select>`
        : '';

    const countLabel = linked.length
        ? `<span class="linked-files-count">${linked.length} source${linked.length > 1 ? 's' : ''} linked</span>`
        : `<span class="linked-files-count no-links">No linked sources</span>`;

    return `<div class="phase-linked-files" onclick="event.stopPropagation()">
        <div class="linked-files-header">${countLabel}${dropdown}</div>
        ${chips ? `<div class="linked-files-chips">${chips}</div>` : ''}
    </div>`;
}

async function populateCompetitorFromSource() {
    const btn = document.getElementById('comp-populate-btn');
    const name = document.getElementById('comp-modal-name').value.trim();
    if (!name) { alert('Enter a competitor name first.'); return; }

    const original = btn.innerText;
    btn.innerText = 'Analysing...';
    btn.disabled = true;

    try {
        const res = await fetch('/api/insights/populate-competitor', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ competitor_name: name, project: currentProject })
        });
        const data = await res.json();
        if (!data.success) throw new Error(data.error || 'Population failed');

        if (data.price && data.price !== 'MISSING') document.getElementById('comp-modal-price').value = data.price;
        if (data.duration && data.duration !== 'MISSING') document.getElementById('comp-modal-duration').value = data.duration;
        if (data.usp && data.usp !== 'MISSING') document.getElementById('comp-modal-usp').value = data.usp;
        if (data.details) {
            compDetailsRaw = data.details;
            const el = document.getElementById('comp-modal-details');
            if (compDetailsPreview) {
                el.innerHTML = renderMarkdown(compDetailsRaw);
            } else {
                el.innerText = compDetailsRaw;
            }
        }
    } catch (e) {
        alert('Populate failed: ' + e.message);
    } finally {
        btn.innerText = original;
        btn.disabled = false;
    }
}

// ---------------------------------------------------------
// RESEARCH RUN BACKGROUND POLLING
// ---------------------------------------------------------

function startRunPolling(runId, responseId, project, chatId) {
    let consecutiveFailures = 0;
    let busy = false;  // Guard against overlapping async callbacks
    const MAX_FAILURES = 10;
    const POLL_MS = 5000;
    const TIMEOUT_MS = 60 * 60 * 1000;
    const startTime = Date.now();

    const intervalId = setInterval(async () => {
        if (busy) return;  // Previous tick still running
        busy = true;

        try {
            if (Date.now() - startTime > TIMEOUT_MS) {
                clearInterval(intervalId);
                delete activeResearchRuns[runId];
                await finalizeRun(runId, project, null, 'Timed out (1 hour)');
                renderResearchRuns();
                return;
            }

            const res = await fetch(
                `/api/deep-research/status/${responseId}?run_id=${encodeURIComponent(runId)}&project=${encodeURIComponent(project)}`
            );
            if (!res.ok) throw new Error(`HTTP ${res.status}`);

            const data = await res.json();
            consecutiveFailures = 0;

            if (!data.success) throw new Error(data.error || 'Status check failed');

            const s = data.status || 'running';

            // Update local state for UI
            if (projectData.research_runs && projectData.research_runs[runId]) {
                projectData.research_runs[runId].status = s;
            }
            renderResearchRuns();

            if (s === 'completed') {
                const output = data.output_markdown || data.output || '';
                if (!output) {
                    clearInterval(intervalId);
                    delete activeResearchRuns[runId];
                    await finalizeRun(runId, project, null, 'Completed but returned no output');
                    renderResearchRuns();
                    return;
                }

                // Save artifact and finalize — if this throws, the interval
                // stays alive and the next tick will retry
                const ts = new Date().toISOString().slice(0, 16).replace('T', ' ');
                const artifactName = `Deep Research - ${ts}`;
                const artifactId = await saveArtifactToServer(artifactName, output, null, project);

                try {
                    await fetch(`/api/chats/${chatId}/append`, {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ project, role: 'assistant', content: output })
                    });
                } catch (appendErr) {
                    console.warn('[research-run] chat append failed:', appendErr);
                }

                await finalizeRun(runId, project, artifactId, null);
                await loadProject({reloadInsights: false});

                // Link the artifact to the chat message by ID so future
                // edits/renames don't break the link (no fragile content matching)
                if (artifactId) {
                    const msgs = projectData?.chats?.[chatId]?.messages || [];
                    const lastIdx = msgs.length - 1;
                    if (lastIdx >= 0 && msgs[lastIdx]?.role === 'assistant') {
                        try {
                            await fetch(`/api/chats/${chatId}/link-artifact`, {
                                method: 'POST',
                                headers: { 'Content-Type': 'application/json' },
                                body: JSON.stringify({ project, index: lastIdx, artifact_id: artifactId })
                            });
                            if (projectData?.chats?.[chatId]?.messages?.[lastIdx]) {
                                projectData.chats[chatId].messages[lastIdx].artifact_id = artifactId;
                            }
                        } catch (linkErr) {
                            console.warn('[research-run] artifact link failed:', linkErr);
                        }
                    }
                }

                // Only NOW clear the interval — all saves succeeded
                clearInterval(intervalId);
                delete activeResearchRuns[runId];
                renderResearchRuns();
                if (currentChat === chatId) renderChat();

            } else if (s === 'failed' || s === 'cancelled') {
                clearInterval(intervalId);
                delete activeResearchRuns[runId];
                const reason = data.error ? `Deep research ${s}: ${data.error}` : `Deep research ${s}`;
                await finalizeRun(runId, project, null, reason);
                await loadProject({reloadInsights: false});
                renderResearchRuns();
            }

        } catch (err) {
            consecutiveFailures++;
            console.warn(`[research-run] poll error for ${runId} (${consecutiveFailures}/${MAX_FAILURES}):`, err.message);


            if (consecutiveFailures >= MAX_FAILURES) {
                clearInterval(intervalId);
                delete activeResearchRuns[runId];
                await finalizeRun(runId, project, null, `Lost connection after ${MAX_FAILURES} retries`);
                renderResearchRuns();
            }
        } finally {
            busy = false;
        }
    }, POLL_MS);

    activeResearchRuns[runId] = { intervalId, responseId, project, chatId };
}

async function finalizeRun(runId, project, artifactId, errorMessage) {
    try {
        await fetch(`/api/research-runs/${runId}/complete`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                project,
                artifact_id: artifactId || null,
                error: errorMessage || null,
            })
        });
    } catch (e) {
        console.error('[research-run] finalizeRun failed:', e);
    }
    updateRunsIndicator();
}

async function cancelResearchRun(runId) {
    const tracked = activeResearchRuns[runId];
    // Use the project captured when polling started, not the current global
    const runProject = (tracked && tracked.project) ? tracked.project : currentProject;
    if (tracked) {
        clearInterval(tracked.intervalId);
        delete activeResearchRuns[runId];
    }
    try {
        await fetch(`/api/research-runs/${runId}/cancel`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ project: runProject })
        });
    } catch (e) {
        console.error('[research-run] cancel failed:', e);
    }
    if (projectData.research_runs && projectData.research_runs[runId]) {
        projectData.research_runs[runId].status = 'cancelled';
    }
    renderResearchRuns();
}

function resumeActiveRuns() {
    const TERMINAL = ['completed', 'failed', 'cancelled'];
    const runs = projectData.research_runs || {};
    Object.values(runs).forEach(run => {
        if (!TERMINAL.includes(run.status) && !activeResearchRuns[run.id] && !run.research_work_item_id) {
            // Use the project stored in the run object (set by create_run on the backend).
            // Falls back to currentProject for legacy runs that pre-date the field.
            const runProject = run.project || currentProject;
            console.log(`[research-run] resuming polling for ${run.id} (status: ${run.status}, project: ${runProject})`);
            startRunPolling(run.id, run.response_id, runProject, run.chat_id);
        }
    });
}

// ---------------------------------------------------------
// RESEARCH RUNS UI
// ---------------------------------------------------------

function renderResearchRuns() {
    const container = document.getElementById('research-runs-list');
    if (!container) return;

    const runs = projectData.research_runs || {};
    const runsList = Object.values(runs).sort(
        (a, b) => new Date(b.created_at) - new Date(a.created_at)
    );

    if (runsList.length === 0) {
        container.innerHTML = '<div class="empty-state" style="padding:2rem;">No research runs yet.</div>';
        updateRunsIndicator();
        return;
    }

    let html = '';
    runsList.forEach(run => {
        const statusColor = {
            running: '#FF8A00', completed: '#28a745', failed: '#dc3545', cancelled: '#6c757d'
        }[run.status] || '#999';
        const statusLabel = {
            running: 'In Progress', completed: 'Completed', failed: 'Failed', cancelled: 'Cancelled'
        }[run.status] || run.status;

        const timeStr = formatTimeAgo(run.created_at);
        const preview = escapeHtml(run.prompt_preview || '');
        const canClick = run.status === 'completed' && run.artifact_id;
        // Show artifact name if available (propagates renames), fall back to "Deep Research"
        const linkedArt = run.artifact_id && projectData.artifacts ? projectData.artifacts[run.artifact_id] : null;
        const runTitle = linkedArt ? escapeHtml(linkedArt.name) : 'Deep Research';

        html += `
            <div class="competitor-card" ${canClick ? `onclick="openArtifactEditor('${run.artifact_id}')"` : ''} style="${canClick ? 'cursor:pointer' : ''}">
                <div class="comp-header" style="display:flex; justify-content:space-between; align-items:center;">
                    <div>
                        <h4 style="margin:0; font-size:0.9rem;">${runTitle}</h4>
                        <div class="comp-duration">${timeStr}</div>
                    </div>
                    <span style="background:${statusColor}; color:white; padding:0.2rem 0.6rem; border-radius:12px; font-size:0.7rem; font-weight:600;">${statusLabel}</span>
                </div>
                <div class="comp-body">
                    <div style="font-size:0.8rem; color:#666; max-height:60px; overflow:hidden;">${preview}</div>
                    <div style="margin-top:auto; padding-top:0.75rem; display:flex; justify-content:space-between; align-items:center; border-top:1px solid #eee;">
                        ${run.status === 'completed' && run.artifact_id
                            ? '<span style="font-size:0.75rem; color:#28a745;">Click to view artifact</span>'
                            : run.status === 'failed'
                            ? `<span style="font-size:0.75rem; color:#dc3545;">${escapeHtml(run.error || 'Unknown error')}</span>`
                            : run.status === 'cancelled'
                            ? '<span style="font-size:0.75rem; color:#999;">Cancelled</span>'
                            : `<button class="small-btn" onclick="event.stopPropagation(); cancelResearchRun('${run.id}')" style="color:#dc3545; border-color:#dc3545;">Cancel</button>`
                        }
                        <span style="font-size:0.7rem; color:#999;">${new Date(run.created_at).toLocaleString()}</span>
                    </div>
                </div>
            </div>`;
    });

    container.innerHTML = html;
    updateRunsIndicator();
}

function formatTimeAgo(isoString) {
    const diff = Date.now() - new Date(isoString).getTime();
    const mins = Math.floor(diff / 60000);
    if (mins < 1) return 'Just now';
    if (mins < 60) return `${mins}m ago`;
    const hours = Math.floor(mins / 60);
    if (hours < 24) return `${hours}h ago`;
    return `${Math.floor(hours / 24)}d ago`;
}

function updateRunsIndicator() {
    const indicator = document.getElementById('research-runs-indicator');
    if (!indicator) return;
    const runs = projectData.research_runs || {};
    const activeCount = Object.values(runs).filter(r => r.status === 'running').length;
    if (activeCount > 0) {
        indicator.style.display = 'block';
        document.getElementById('runs-indicator-text').textContent =
            `${activeCount} research run${activeCount > 1 ? 's' : ''} in progress...`;
    } else {
        indicator.style.display = 'none';
    }
}


async function sendMessage(insightGenId) {
    const input = document.getElementById('chat-input');
    const message = input.value.trim();
    if (!message) return;
    input.value = ''; input.disabled = true;
    document.getElementById('send-btn').style.display = 'none';
    document.getElementById('stop-btn').style.display = 'inline-block';

    // Capture project and chat at call time to prevent cross-project contamination
    const capturedProject = currentProject;
    const capturedChat = currentChat;

    if (!insightGenId) { appendMessage('user', message); }
    const assistantContentDiv = (!insightGenId) ? appendMessage('assistant', 'Thinking...') : null;

    // For insight generations, use a local AbortController stored in the dict
    // For normal chat, use the global currentAbortController so the Stop button works
    let abortController;
    if (insightGenId) {
        abortController = new AbortController();
        const genCtx = getGenerationContext(insightGenId);
        if (genCtx) genCtx.abortController = abortController;
    } else {
        currentAbortController = new AbortController();
        abortController = currentAbortController;
    }

    try {
        const chatBody = { message, chat_id: capturedChat, project: capturedProject };
        if (insightGenId) {
            const genCtx = getGenerationContext(insightGenId);
            chatBody.stateless = true;
            const genType = genCtx ? genCtx.type : '';
            chatBody.insight_type = genType;
            chatBody.require_sources = genType !== 'phase-7';
            if (genCtx && genCtx.overrideFileIds) {
                chatBody.source_file_ids = genCtx.overrideFileIds;
            }
            if (genCtx && Array.isArray(genCtx.overrideFileIds) && genCtx.overrideFileIds.length === 0) {
                chatBody.source_file_ids = [];
            }
            if (genCtx && genCtx.overrideFiles) {
                chatBody.source_files = genCtx.overrideFiles;
            }
            if (genCtx && Array.isArray(genCtx.overrideFiles) && genCtx.overrideFiles.length === 0) {
                chatBody.source_files = [];
            }
            if (genCtx && genCtx.insightsContext) {
                chatBody.insights_context = genCtx.insightsContext;
            }
        }
        let response = null;
        const maxAttempts = insightGenId ? 2 : 1;
        for (let attempt = 1; attempt <= maxAttempts; attempt++) {
            try {
                response = await fetch('/api/chat', {
                    method: 'POST', headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(chatBody),
                    signal: abortController.signal
                });
                break;
            } catch (e) {
                const aborted = !!(e && (e.name === 'AbortError' || /aborted/i.test(e.message || '')));
                if (aborted || attempt === maxAttempts) throw e;
                await new Promise(resolve => setTimeout(resolve, 700 * attempt));
            }
        }
        if (!response.ok) {
            let errMsg = `Chat request failed (${response.status})`;
            try {
                const err = await response.json();
                if (err && err.error) errMsg = err.error;
            } catch (_) {}
            throw new Error(errMsg);
        }
        if (!response.body) {
            throw new Error('Chat stream not available');
        }

        const reader = response.body.getReader();
        const decoder = new TextDecoder();
        let fullResponse = '';
        let isFirstChunk = true;
        let sseBuffer = '';

        const handleStreamEvent = async (data) => {
            if (data.type === 'content') {
                if (isFirstChunk && assistantContentDiv) {
                    assistantContentDiv.innerHTML = '';
                    isFirstChunk = false;
                }
                fullResponse += data.text;
                if (assistantContentDiv) assistantContentDiv.innerHTML = formatMessageContent(fullResponse);
                return;
            }

            if (data.type === 'error') {
                console.error('[chat] Server error:', data.message);
                if (insightGenId) {
                    delete activeInsightGenerations[insightGenId];
                    resetInsightGenerationUI();
                    const statusEl = document.getElementById('insights-status');
                    if (statusEl) statusEl.innerHTML = `<span style="color:#dc3545;">Error: ${data.message || 'Generation failed'}</span>`;
                } else if (assistantContentDiv) {
                    assistantContentDiv.innerHTML = `<span style="color:#dc3545;">Error: ${data.message || 'Something went wrong'}</span>`;
                }
                return;
            }

            if (data.type === 'done') {
                // Only update currentChat if still on the same project
                if (capturedProject === currentProject && data.chat_id) {
                    currentChat = data.chat_id;
                    _saveTabState();
                }
                if (insightGenId) {
                    await handleInsightResponse(fullResponse, insightGenId);
                    // Don't call loadProject() here — handleInsightResponse
                    // awaits its saves and renders directly.
                } else if (chapterMode) {
                    // AUTO-SAVE ARTIFACT if mode is on
                    const artifactId = await saveArtifactToServer("Generated Artifact", fullResponse);
                    await loadProject({reloadInsights: false});
                    if (artifactId) {
                        const msgs = projectData?.chats?.[currentChat]?.messages || [];
                        const lastIndex = msgs.length - 1;
                        if (lastIndex >= 0 && msgs[lastIndex]?.role === "assistant") {
                            await linkArtifactToMessage(lastIndex, artifactId);
                        }
                    }
                    renderChat(); // Reload chat to convert text to card
                } else {
                    await loadProject({reloadInsights: false});
                }
            }
        };

        while (true) {
            const { done, value } = await reader.read();
            if (done) break;
            sseBuffer += decoder.decode(value, { stream: true });
            sseBuffer = sseBuffer.replace(/\r\n/g, '\n');

            let eventEnd = sseBuffer.indexOf('\n\n');
            while (eventEnd !== -1) {
                const rawEvent = sseBuffer.slice(0, eventEnd);
                sseBuffer = sseBuffer.slice(eventEnd + 2);

                if (rawEvent.trim()) {
                    const dataLines = rawEvent
                        .split('\n')
                        .filter(line => line.startsWith('data:'))
                        .map(line => line.slice(5).trimStart());

                    if (dataLines.length > 0) {
                        const payload = dataLines.join('\n');
                        if (payload !== '[DONE]') {
                            try {
                                const data = JSON.parse(payload);
                                await handleStreamEvent(data);
                            } catch (e) {
                                console.warn('[chat] Dropped malformed SSE payload chunk:', payload.slice(0, 200));
                            }
                        }
                    }
                }

                eventEnd = sseBuffer.indexOf('\n\n');
            }
        }

        // Flush decoder and process any final buffered event
        sseBuffer += decoder.decode();
        sseBuffer = sseBuffer.replace(/\r\n/g, '\n');
        if (sseBuffer.trim()) {
            const dataLines = sseBuffer
                .split('\n')
                .filter(line => line.startsWith('data:'))
                .map(line => line.slice(5).trimStart());
            if (dataLines.length > 0) {
                const payload = dataLines.join('\n');
                if (payload !== '[DONE]') {
                    try {
                        const data = JSON.parse(payload);
                        await handleStreamEvent(data);
                    } catch (e) {
                        console.warn('[chat] Dropped malformed trailing SSE payload:', payload.slice(0, 200));
                    }
                }
            }
        }
    } catch (error) {
        const wasCancelled = !!(error && (error.name === 'AbortError' || /aborted/i.test(error.message || '')));
        if (insightGenId) {
            delete activeInsightGenerations[insightGenId];
            resetInsightGenerationUI();
            const statusEl = document.getElementById('insights-status');
            if (statusEl) {
                statusEl.innerHTML = wasCancelled
                    ? `<span style="color:#6c757d;">Generation cancelled.</span>`
                    : `<span style="color:#dc3545;">Error: ${escapeHtml(error.message)}</span>`;
            }
        }
        if (assistantContentDiv) {
            assistantContentDiv.innerHTML += wasCancelled
                ? `<br><span style="color:#6c757d;">Cancelled.</span>`
                : `<br><span style="color:red">Error: ${error.message}</span>`;
        }
    }
    finally {
        if (insightGenId) { delete activeInsightGenerations[insightGenId]; resetInsightGenerationUI(); }
        input.disabled = false; input.focus();
        document.getElementById('send-btn').style.display = 'inline-block';
        document.getElementById('stop-btn').style.display = 'none';
        if (!insightGenId) currentAbortController = null;
    }
}

function stopInsightsGeneration() {
    const genId = getActiveGenerationForProject(currentProject);
    if (!genId) return;
    const genCtx = getGenerationContext(genId);
    if (genCtx && genCtx.abortController) {
        const stopBtn = document.getElementById('insight-stop-btn');
        if (stopBtn) stopBtn.disabled = true;
        const statusEl = document.getElementById('insights-status');
        if (statusEl) statusEl.innerHTML = `<span style="color:#6c757d;">Cancelling...</span>`;
        genCtx.abortController.abort();
    }
}

function stopGeneration() {
    const activeForCurrent = getActiveGenerationForProject(currentProject);
    const anyActive = Object.keys(activeInsightGenerations)[0];
    const genId = activeForCurrent || anyActive;
    if (genId) {
        const genCtx = getGenerationContext(genId);
        if (genCtx && genCtx.abortController) {
            genCtx.abortController.abort();
            return;
        }
    }
    if (currentAbortController) currentAbortController.abort();
}
function formatMessageContent(text) { return renderMarkdown(text); }
function escapeHtml(text) { if (!text) return ''; const div = document.createElement('div'); div.textContent = text; return div.innerHTML; }
function copyToClipboard(text) { navigator.clipboard.writeText(text); }
function sanitizeRenderedHtml(html) {
    if (!window.DOMPurify) return html;
    return DOMPurify.sanitize(html, {
        ADD_ATTR: ['target', 'rel', 'class']
    });
}
function getOutputContent() {
    const editor = document.getElementById('output-editor');
    if (editor) {
        currentOutputRaw = outputPreview ? htmlToMarkdown(editor.innerHTML) : editor.innerText;
    }
    return currentOutputRaw || "";
}
function copyOutputToClipboard() { copyToClipboard(getOutputContent()); }

function renderMarkdown(text) {
    const safeText = text || "";
    if (window.marked) {
        const html = marked.parse(safeText, { gfm: true, breaks: true });
        return sanitizeRenderedHtml(html);
    }
    return simpleMarkdownToHtml(safeText);
}

function renderMarkdownInline(text) {
    const safeText = text || "";
    if (window.marked && typeof marked.parseInline === "function") {
        const html = marked.parseInline(safeText, { gfm: true, breaks: true });
        return sanitizeRenderedHtml(html);
    }
    return simpleMarkdownInline(safeText);
}

function toMarkdownFromHtmlCore(html) {
    const svc = getTurndownService();
    if (svc) return svc.turndown(html || "");
    const div = document.createElement('div');
    div.innerHTML = html || "";
    return div.innerText || "";
}

function extractCellText(cell) {
    if (!cell) return "";
    const clone = cell.cloneNode(true);
    clone.querySelectorAll('br').forEach(br => br.replaceWith('\n'));
    return (clone.textContent || "")
        .replace(/\u00A0/g, ' ')
        .replace(/\r\n/g, '\n')
        .trim();
}

function normalizeTableMatrix(matrix) {
    const rows = Array.isArray(matrix) ? matrix : [];
    let colCount = rows.reduce((max, row) => Math.max(max, Array.isArray(row) ? row.length : 0), 0);
    if (colCount <= 0) colCount = 1;
    return rows.map(row => {
        const r = Array.isArray(row) ? row.slice(0, colCount) : [];
        while (r.length < colCount) r.push('');
        return r;
    });
}

function escapeMarkdownTableCell(value) {
    return String(value || '')
        .replace(/\|/g, '\\|')
        .replace(/\r?\n/g, '<br>')
        .trim();
}

function tableElementToMarkdown(tableEl) {
    if (!tableEl) return '';
    const rowEls = Array.from(tableEl.querySelectorAll('tr'));
    if (!rowEls.length) return '';

    const matrix = normalizeTableMatrix(
        rowEls.map(row => Array.from(row.querySelectorAll('th, td')).map(extractCellText))
    );
    if (!matrix.length) return '';

    const colCount = matrix[0].length || 1;
    const header = matrix[0];
    const bodyRows = matrix.slice(1);
    const lines = [];
    lines.push(`| ${header.map(escapeMarkdownTableCell).join(' | ')} |`);
    lines.push(`| ${new Array(colCount).fill('---').join(' | ')} |`);

    if (bodyRows.length === 0) {
        lines.push(`| ${new Array(colCount).fill('').join(' | ')} |`);
    } else {
        bodyRows.forEach(row => {
            lines.push(`| ${row.map(escapeMarkdownTableCell).join(' | ')} |`);
        });
    }
    return lines.join('\n');
}

function htmlToMarkdown(html) {
    const container = document.createElement('div');
    container.innerHTML = html || "";

    // Remove editor-only controls.
    container.querySelectorAll('.table-edit-btn').forEach(el => el.remove());
    container.querySelectorAll('.table-edit-wrapper').forEach(wrapper => {
        const table = wrapper.querySelector('table');
        if (table) wrapper.replaceWith(table);
        else wrapper.remove();
    });

    const tables = Array.from(container.querySelectorAll('table'));
    const placeholders = [];
    tables.forEach((table, idx) => {
        const token = `TABLE_TOKEN_${Date.now()}_${idx}`;
        placeholders.push({ token, markdown: tableElementToMarkdown(table) });
        const marker = document.createElement('p');
        marker.textContent = token;
        table.replaceWith(marker);
    });

    let markdown = toMarkdownFromHtmlCore(container.innerHTML || "");
    placeholders.forEach(({ token, markdown: tableMd }) => {
        const tokenRegex = new RegExp(`\\b${token}\\b`, 'g');
        markdown = markdown.replace(tokenRegex, `\n\n${tableMd}\n\n`);
    });

    return markdown.replace(/\n{3,}/g, '\n\n').trim();
}

function getTurndownService() {
    if (!window.TurndownService) return null;
    if (!turndownService) {
        turndownService = new TurndownService({ codeBlockStyle: "fenced" });
    }
    return turndownService;
}

function simpleMarkdownInline(text) {
    let s = escapeHtml(text || "");
    s = s.replace(/\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)/g, '<a href="$2" target="_blank" rel="noopener noreferrer">$1</a>');
    s = s.replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>");
    s = s.replace(/__(.+?)__/g, "<strong>$1</strong>");
    s = s.replace(/\*(.+?)\*/g, "<em>$1</em>");
    s = s.replace(/_(.+?)_/g, "<em>$1</em>");
    s = s.replace(/`([^`]+?)`/g, "<code>$1</code>");
    return s;
}

function simpleMarkdownToHtml(text) {
    const raw = text || "";
    let escaped = escapeHtml(raw);
    const blocks = [];
    escaped = escaped.replace(/```([\s\S]*?)```/g, (m, code) => {
        const idx = blocks.length;
        blocks.push(`<pre><code>${code}</code></pre>`);
        return `@@CODEBLOCK${idx}@@`;
    });

    const lines = escaped.split("\n");
    let out = [];
    let inUl = false;
    let inOl = false;

    function closeLists() {
        if (inUl) { out.push("</ul>"); inUl = false; }
        if (inOl) { out.push("</ol>"); inOl = false; }
    }

    for (const line of lines) {
        if (line.trim() === "") {
            closeLists();
            continue;
        }
        if (line.startsWith("@@CODEBLOCK")) {
            closeLists();
            out.push(line);
            continue;
        }

        const h = line.match(/^(#{1,6})\s+(.+)$/);
        if (h) {
            closeLists();
            const level = h[1].length;
            out.push(`<h${level}>${simpleMarkdownInline(h[2])}</h${level}>`);
            continue;
        }

        const bq = line.match(/^>\s+(.+)$/);
        if (bq) {
            closeLists();
            out.push(`<blockquote>${simpleMarkdownInline(bq[1])}</blockquote>`);
            continue;
        }

        const ul = line.match(/^\s*[-*+]\s+(.+)$/);
        if (ul) {
            if (!inUl) { closeLists(); out.push("<ul>"); inUl = true; }
            out.push(`<li>${simpleMarkdownInline(ul[1])}</li>`);
            continue;
        }

        const ol = line.match(/^\s*\d+\.\s+(.+)$/);
        if (ol) {
            if (!inOl) { closeLists(); out.push("<ol>"); inOl = true; }
            out.push(`<li>${simpleMarkdownInline(ol[1])}</li>`);
            continue;
        }

        closeLists();
        out.push(`<p>${simpleMarkdownInline(line)}</p>`);
    }

    closeLists();
    let html = out.join("\n");
    html = html.replace(/@@CODEBLOCK(\d+)@@/g, (m, idx) => blocks[Number(idx)] || "");
    return html;
}

function setupSelectionCapture() {
    const editor = document.getElementById('output-editor');
    if (editor) editor.addEventListener('mouseup', () => {
        const sel = window.getSelection();
        if (sel.rangeCount > 0 && editor.contains(sel.anchorNode)) {
            const text = sel.toString().trim();
            if (!text) return;
            lastSelection = text;
            lastRange = sel.getRangeAt(0).cloneRange();
            // In preview mode, capture the markdown equivalent of the selection
            // so we can do replacement at the source level
            if (outputPreview) {
                try {
                    const frag = lastRange.cloneContents();
                    const tmp = document.createElement('div');
                    tmp.appendChild(frag);
                    lastSelectionMarkdown = htmlToMarkdown(tmp.innerHTML).trim();
                } catch (e) {
                    lastSelectionMarkdown = null;
                }
                // Highlight the selection so user can see what's captured
                clearSelectionHighlight(editor);
                try {
                    const range = sel.getRangeAt(0);
                    const mark = document.createElement('mark');
                    mark.className = 'selection-highlight';
                    range.surroundContents(mark);
                } catch (e) {
                    // surroundContents fails on cross-element selections;
                    // fall back to no highlight (selection still captured)
                }
            } else {
                lastSelectionMarkdown = text;
            }
        }
    });
}
function clearSelectionHighlight(container) {
    (container || document).querySelectorAll('mark.selection-highlight').forEach(m => {
        const parent = m.parentNode;
        while (m.firstChild) parent.insertBefore(m.firstChild, m);
        parent.removeChild(m);
    });
}
function markSelection() {
    if (!lastSelection) { alert('Select text in the editor first.'); return; }
    document.getElementById('marked-text-content').innerText = lastSelection;
    document.getElementById('marked-text-display').style.display = 'block';
    // Ensure panel is open when marking text
    const panel = document.getElementById('edit-panel');
    if (panel.style.display !== 'flex') toggleEditPanel();
}
// Original sendEditMessage (overridden below with markdown rendering)
async function sendEditMessage() {
    const input = document.getElementById('edit-chat-input');
    const instruction = input.value;
    if (!instruction) return;
    const chatMessages = document.getElementById('edit-chat-messages');
    chatMessages.innerHTML += `<div class="message user">${escapeHtml(instruction)}</div>`;
    input.value = '';

    const hasSelection = lastSelection && lastSelection.trim().length > 0;
    const editBody = {
        selected_text: hasSelection ? lastSelection : "",
        instruction,
        full_chapter: getOutputContent(),
        project: currentProject,
    };
    if (currentPhaseKey && currentInsightsData) {
        const linkedIds = currentInsightsData.phases[currentPhaseKey]?.linked_file_ids || [];
        if (linkedIds.length > 0) {
            editBody.source_file_ids = linkedIds;
        }
    }

    try {
        const res = await fetch('/api/edit', { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(editBody) });
        const data = await res.json();
        if (data.error) {
            chatMessages.innerHTML += `<div class="message assistant"><em>Error: ${escapeHtml(data.error)}</em></div>`;
            return;
        }
        const actionLabel = hasSelection ? 'Replace Selection' : 'Append to Document';
        chatMessages.innerHTML += `<div class="message assistant">${data.response}<br><button class="btn-primary" onclick="applyTextReplacement(decodeURIComponent('${encodeURIComponent(data.response)}'))">${actionLabel}</button></div>`;
    } catch (err) {
        chatMessages.innerHTML += `<div class="message assistant"><em>Request failed: ${escapeHtml(err.message)}</em></div>`;
    }
    chatMessages.scrollTop = chatMessages.scrollHeight;
}
function applyTextReplacement(newText) {
    const editor = document.getElementById('output-editor');
    if (!editor) return;

    // Try markdown-level replacement first (works reliably in preview mode)
    const mdNeedle = lastSelectionMarkdown || lastSelection;
    if (mdNeedle && mdNeedle.trim() && currentOutputRaw.includes(mdNeedle)) {
        currentOutputRaw = currentOutputRaw.replace(mdNeedle, newText);
    } else if (lastRange && !outputPreview) {
        // Raw mode fallback: DOM manipulation is safe here
        lastRange.deleteContents();
        lastRange.insertNode(document.createTextNode(newText));
        currentOutputRaw = editor.innerText;
    } else {
        // No match found — append to the end
        currentOutputRaw += '\n\n' + newText;
    }

    // Re-render the editor with updated content
    updateOutputEditorView();

    // Clear selection state after applying
    lastSelection = null;
    lastRange = null;
    lastSelectionMarkdown = null;
}
function toggleEditPanel() {
    const panel = document.getElementById('edit-panel');
    const toggle = document.getElementById('chat-panel-toggle');
    const isOpening = panel.style.display !== 'flex';
    panel.style.display = isOpening ? 'flex' : 'none';
    if (toggle) toggle.classList.toggle('active', isOpening);
    if (isOpening) updateEditPanelContext();
}

function updateEditPanelContext() {
    const badge = document.getElementById('edit-source-badge');
    const title = document.getElementById('edit-panel-title');
    if (!badge) return;

    if (currentPhaseKey && currentInsightsData) {
        const phase = currentInsightsData.phases[currentPhaseKey];
        const linked = phase?.linked_files || [];
        if (title) title.textContent = `Phase ${currentPhaseKey} Assistant`;
        if (linked.length > 0) {
            badge.style.display = 'block';
            badge.innerHTML = `<span class="edit-source-label">Context:</span> ${linked.map(f => `<span class="edit-source-file">${escapeHtml(f)}</span>`).join('')}`;
        } else {
            badge.style.display = 'block';
            badge.innerHTML = `<span class="edit-source-label">Context:</span> <span class="edit-source-file">No linked sources</span>`;
        }
    } else {
        if (title) title.textContent = 'AI Assistant';
        badge.style.display = 'none';
    }
}
function clearEditChat() { document.getElementById('edit-chat-messages').innerHTML = ''; }

async function loadProject(options) {
    const reloadInsights = !options || options.reloadInsights !== false;
    const requestedProject = currentProject;
    if (!requestedProject) return;
    const loadId = ++latestProjectLoadId;

    let loadedData;
    try {
        const res = await fetch(`/api/projects/${encodeURIComponent(requestedProject)}?set_current=0&t=${Date.now()}`);
        loadedData = await res.json();
    } catch (e) {
        if (loadId !== latestProjectLoadId || requestedProject !== currentProject) return;
        console.error('[project] load failed:', e);
        return;
    }

    // Ignore stale responses that return after the user has switched projects again
    if (loadId !== latestProjectLoadId || requestedProject !== currentProject) return;
    projectData = loadedData;
    if (!projectData.file_index || typeof projectData.file_index !== 'object') projectData.file_index = {};
    if (!Array.isArray(projectData.selected_file_ids)) projectData.selected_file_ids = [];
    if (!Array.isArray(projectData.selected_files)) {
        projectData.selected_files = projectData.selected_file_ids
            .map(id => projectData.file_index[id])
            .filter(Boolean);
    }

    // Reload insights from disk only on project switch / initial load.
    // Routine reloads (file toggle, upload, rename, chat ops) preserve
    // the in-memory currentInsightsData so it can't be clobbered by a
    // stale or not-yet-written insights.json on disk.
    if (reloadInsights) {
        // Reloading insights from disk always lands on the live/current
        // view, never a lingering historical flag from whatever was being
        // viewed before (possibly in a different project).
        viewingHistoricalVersion = false;
        if (projectData.files && projectData.files['insights.json']) {
            try {
                const loaded = JSON.parse(projectData.files['insights.json']);
                normalizeInsightsData(loaded, getAvailableSourceFiles(), projectData.file_index || {});
                currentInsightsData = loaded;
                renderInsights(currentInsightsData);
                const statusEl = document.getElementById('insights-status');
                if (statusEl) statusEl.innerHTML = `Latest — ${new Date(currentInsightsData.generated_at).toLocaleString()} <button onclick="downloadInsights()" class="small-btn">JSON</button>`;
            } catch(e) {
                currentInsightsData = createEmptyInsightsData();
                renderInsights(currentInsightsData);
                const statusEl = document.getElementById('insights-status');
                if (statusEl) statusEl.textContent = "No insights generated yet";
            }
        } else {
            currentInsightsData = createEmptyInsightsData();
            renderInsights(currentInsightsData);
            const statusEl = document.getElementById('insights-status');
            if (statusEl) statusEl.textContent = "No insights generated yet";
        }
        loadInsightsHistory();
        // Keep cache in sync after loading from disk
        saveInsightsToCache(currentProject);
    }

    // Validate the restored currentChat still exists in this project's chats
    if (currentChat && !projectData.chats[currentChat]) {
        currentChat = null;
    }
    _saveTabState();

    const sys = document.getElementById('system-prompt'); if(sys) sys.value = projectData.system_prompt||'';
    const proj = document.getElementById('project-prompt'); if(proj) proj.value = projectData.project_prompt||'';
    renderFileList(); renderChatList();
    if (currentChat) renderChat();
    
    // If we are on artifacts tab, render it
    if (document.getElementById('compiled-tab').classList.contains('active')) {
        renderArtifactsTab();
    }

    // Render research runs and resume any active polling
    renderResearchRuns();
    resumeActiveRuns();
    syncInsightsControlButtons();
    if (typeof boardOnProjectLoaded === 'function') boardOnProjectLoaded();
}
function switchProject() {
    // Save current project's insights state before switching
    const previousProject = currentProject;
    if (previousProject) saveInsightsToCache(previousProject);

    hideInsightsIndicator();
    closeOutputView();
    closeFileEditor();
    closeCompetitorModal();
    clearEditChat();
    currentProject = document.getElementById('project-select').value;
    currentChat = null;
    _saveTabState();

    // Only a project with an insights generation still running is restored from the
    // cache (it holds results not yet saved); loadProject then skips the reload. Any
    // other project loads from the server, which may have changed since (report links,
    // the options report). Clear the old project's insights meanwhile so they can't be
    // edited or saved under the new project.
    const activeGen = getActiveGenerationForProject(currentProject);
    const cacheHit = activeGen ? restoreInsightsFromCache(currentProject) : false;
    if (!cacheHit) resetInsightsUI();
    syncInsightsControlButtons();

    // If the new project has an active generation, restore its indicator
    if (activeGen) {
        const genCtx = getGenerationContext(activeGen);
        const genType = genCtx ? genCtx.type : '';
        const label = genType === 'competitors' ? 'Analysing competitor landscape...'
            : genType.startsWith('phase-') ? `Analysing Phase ${genType.replace('phase-', '')}...`
            : 'Generating insights...';
        showInsightsIndicator(label);
    }

    loadProject(cacheHit ? {reloadInsights: false} : undefined);
}
async function getErrorMessage(res, fallback = 'Request failed') {
    try {
        const data = await res.json();
        return data.error || fallback;
    } catch (_) {
        return fallback;
    }
}

async function newProject() {
    const name = prompt('Name:');
    if (!name) return;

    const res = await fetch('/api/projects', {
        method:'POST',
        headers:{'Content-Type':'application/json'},
        body:JSON.stringify({name})
    });
    if (!res.ok) {
        alert(await getErrorMessage(res, 'Unable to create project.'));
        return;
    }
    location.reload();
}
async function uploadFile(e) {
    const file = e.target.files[0];
    if (!file) return;
    const formData = new FormData();
    formData.append('file', file);
    formData.append('project', currentProject);
    try {
        const res = await fetch('/api/files', { method: 'POST', body: formData });
        if (!res.ok) throw new Error(await getErrorMessage(res, 'Upload failed.'));
        await loadProject({reloadInsights: false});
    } catch (err) {
        alert(err.message || "Upload failed");
    }
    finally { e.target.value = ''; }
}
async function deleteFile(f) { if(confirm('Delete?')) await fetch(`/api/files/${encodeURIComponent(f)}?project=${encodeURIComponent(currentProject)}`, {method:'DELETE'}); loadProject({reloadInsights: false}); }
async function toggleFileSelection(f) {
    const fileId = getFileIdByName(f);
    await fetch('/api/files/toggle', {
        method:'POST',
        headers:{'Content-Type':'application/json'},
        body:JSON.stringify({filename:f, file_id:fileId, project:currentProject})
    });
    loadProject({reloadInsights: false});
}
async function renameFile(oldName) {
    const newName = prompt("Rename:", oldName);
    if (!newName) return;

    const res = await fetch('/api/files/rename', {
        method:'POST',
        headers:{'Content-Type':'application/json'},
        body:JSON.stringify({old_name:oldName, new_name:newName, project:currentProject})
    });
    if (!res.ok) {
        alert(await getErrorMessage(res, 'Rename failed.'));
        return;
    }
    loadProject({reloadInsights: false});
}
function openFileEditor(f) { 
    setFileEditorContent(f, projectData.files[f]);
    document.getElementById('file-editor-modal').classList.add('active'); 
    editingFile = f; 
}
function closeFileEditor() { document.getElementById('file-editor-modal').classList.remove('active'); }
async function saveFileFromEditor() {
    const c = getFileEditorContent();
    const res = await fetch('/api/files', {
        method:'POST',
        headers:{'Content-Type':'application/json'},
        body:JSON.stringify({filename:editingFile, content:c, project:currentProject})
    });
    if (!res.ok) {
        alert(await getErrorMessage(res, 'Save failed.'));
        return;
    }
    closeFileEditor();
    loadProject({reloadInsights: false});
}

function renderFileList() {
    const list = document.getElementById('file-list'); 
    let html = `<div class="expand-header"><h3>Source Data</h3><button class="close-expand-btn" onclick="toggleFileFullscreen()">&#x00D7;</button></div><div class="list-content">`;
    const HIDDEN_FILES = ['insights.json', 'insights_history.json', 'excluded_competitors.json'];
    Object.keys(projectData.files).filter(f => !HIDDEN_FILES.includes(f)).forEach(f => {
        html += `<div class="file-item"><input type="checkbox" ${projectData.selected_files.includes(f)?'checked':''} onchange="toggleFileSelection('${f}')"><span ondblclick="openSourceFileInArtifactView('${f}')" title="${f}">${f}</span><div class="item-actions"><button onclick="renameFile('${f}')">&#x270F;&#xFE0F;</button><button onclick="deleteFile('${f}')">&#x1F5D1;&#xFE0F;</button></div></div>`;
    });
    html += '</div>';
    list.innerHTML = html;
}

function renderChatList() {
    const list = document.getElementById('chat-list'); 
    let html = `<div class="expand-header"><h3>Sessions</h3><button class="close-expand-btn" onclick="toggleChatFullscreen()">&#x00D7;</button></div><div class="list-content">`;
    Object.entries(projectData.chats).reverse().forEach(([id, chat]) => {
        const safeId = escapeAttr(id);
        const safeName = escapeHtml(chat?.name || "Untitled Session");
        html += `<div class="chat-item ${id===currentChat?'active':''}" data-chat-id="${safeId}" onclick="selectChat(this.dataset.chatId)"><span title="${safeName}">${safeName}</span><div class="item-actions"><button onclick="event.stopPropagation(); renameChat(this.closest('.chat-item').dataset.chatId)">&#x270F;&#xFE0F;</button><button onclick="event.stopPropagation(); deleteChat(this.closest('.chat-item').dataset.chatId)">&#x1F5D1;&#xFE0F;</button></div></div>`;
    });
    html += '</div>';
    list.innerHTML = html;
}

function selectChat(id) {
    currentChat = id;
    _saveTabState();
    showTab('write');
    renderChat();
    document.querySelectorAll('#chat-list .chat-item').forEach(el => {
        el.classList.toggle('active', el.dataset.chatId === id);
    });
}
async function newChat() { const res = await fetch('/api/chats', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({project:currentProject})}); const d = await res.json(); currentChat = d.chat_id; _saveTabState(); loadProject({reloadInsights: false}); }
async function deleteChat(id) { if(confirm('Delete?')) { await fetch(`/api/chats/${id}?project=${encodeURIComponent(currentProject)}`, {method:'DELETE'}); if (currentChat === id) { currentChat = null; _saveTabState(); } } loadProject({reloadInsights: false}); }
async function renameChat(id) { const n = prompt('Name:'); if(n) await fetch(`/api/chats/${id}/rename`, {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({name:n, project:currentProject})}); loadProject({reloadInsights: false}); }
async function savePrompt(t) { await fetch(`/api/prompts/${t}_prompt`, {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({content:document.getElementById(`${t}-prompt`).value, project:currentProject})}); alert('Saved'); }

// EDITOR ACTIONS
function closeOutputView() {
    document.getElementById('output-view').classList.remove('active');
    currentArtifactId = null;
    currentSourceFilename = null;
    currentPhaseKey = null;

    // Reset save button state
    const saveBtn = document.querySelector('.editor-panel .button-group-inline button:last-child');
    if (saveBtn) {
        saveBtn.innerText = "Save to Files";
        saveBtn.onclick = saveOutput;
    }
}

function openPhaseEditor(key) {
    if (!currentInsightsData || !currentInsightsData.phases[key]) return;
    const phase = currentInsightsData.phases[key];
    if (!phase.summary || phase.summary === 'MISSING') return;

    showTab('write');
    currentArtifactId = null;
    currentSourceFilename = null;
    currentPhaseKey = key;
    // Open in rendered mode with table-safe editing controls.
    outputPreview = true;
    setOutputEditorContent(phase.summary);
    document.getElementById('chapter-nav-title').textContent = `Phase ${key}: ${phase.title}`;
    document.getElementById('output-view').classList.add('active');

    const saveBtn = document.querySelector('.editor-panel .button-group-inline button:last-child');
    if (saveBtn) {
        saveBtn.innerText = "Update Phase";
        saveBtn.onclick = updatePhaseFromEditor;
    }
}

async function updatePhaseFromEditor() {
    if (!currentPhaseKey || !currentInsightsData) return;
    // Save from whichever mode is active (rendered or raw).
    const editor = document.getElementById('output-editor');
    let content;
    if (!outputPreview && editor) {
        content = editor.innerText;
        currentOutputRaw = content;
    } else {
        content = getOutputContent();
    }
    const btn = document.querySelector('.editor-panel .button-group-inline button:last-child');
    const originalText = btn ? btn.innerText : '';
    if (btn) btn.innerText = "Saving...";

    currentInsightsData.phases[currentPhaseKey].summary = content;
    currentInsightsData.generated_at = new Date().toISOString();
    await saveInsightsToFile(currentInsightsData);

    if (btn) {
        btn.innerText = "Saved";
        setTimeout(() => { btn.innerText = originalText; }, 1500);
    }
}

async function renameCurrentChapterTile() {
    if(!currentArtifactId || !projectData.artifacts[currentArtifactId]) return;
    const oldName = projectData.artifacts[currentArtifactId].name;
    const n = prompt("Rename Artifact:", oldName);
    if(!n || n === oldName) return;

    // Update UI immediately
    document.getElementById('chapter-nav-title').textContent = n;

    const btn = document.querySelector('.editor-panel .button-group-inline button:last-child');
    const originalText = btn ? btn.innerText : '';
    if (btn) btn.innerText = "Renaming...";

    // Save with the stored raw content — avoids the lossy HTML→Markdown
    // round-trip that getOutputContent() does in preview mode, which would
    // alter the content and break the artifact↔message link in chat.
    // Pass existing created_at so the rename doesn't reset the timestamp.
    const existingArt = projectData.artifacts[currentArtifactId];
    const preservedCreatedAt = existingArt ? existingArt.created_at : null;
    await saveArtifactToServer(n, currentOutputRaw, currentArtifactId, null, preservedCreatedAt);

    if (btn) {
        btn.innerText = "✅ Renamed";
        setTimeout(() => { btn.innerText = originalText; }, 1500);
    }

    // Re-render dependent views so the new name propagates everywhere
    if (currentChat) renderChat();
    renderResearchRuns();
    if (document.getElementById('compiled-tab').classList.contains('active')) {
        renderArtifactsTab();
    }
}
function toggleChapterMode() { chapterMode = document.getElementById('chapter-mode-toggle').checked; }
function increaseFontSize() { const e = document.getElementById('output-editor'); e.style.fontSize = (parseFloat(window.getComputedStyle(e).fontSize) + 2) + 'px'; }
function decreaseFontSize() { const e = document.getElementById('output-editor'); e.style.fontSize = (parseFloat(window.getComputedStyle(e).fontSize) - 2) + 'px'; }
function resetFontSize() { const e = document.getElementById('output-editor'); if (outputDefaultFontSize) { e.style.fontSize = outputDefaultFontSize; } else { e.style.fontSize = '14px'; } }
function saveOutput() { document.getElementById('save-modal').classList.add('active'); }
function closeSaveModal() { document.getElementById('save-modal').classList.remove('active'); }
async function confirmSaveOutput() { 
    const f = document.getElementById('save-filename').value; 
    const c = getOutputContent(); 
    const res = await fetch('/api/files', {
        method:'POST',
        headers:{'Content-Type':'application/json'},
        body:JSON.stringify({filename:f, content:c, project: currentProject})
    });
    if (!res.ok) {
        alert(await getErrorMessage(res, 'Save failed.'));
        return;
    }
    closeSaveModal();
    loadProject({reloadInsights: false});
}

// The server changes Insights too (the Research tab links finished reports and writes the
// options report), so the Insights tab shows the server's copy every time it opens. Only a
// generation running for this project keeps the in-memory copy: it holds unsaved results.
function reloadInsightsUnlessGenerating() {
    if (!currentProject || getActiveGenerationForProject(currentProject)) return;
    loadProject({reloadInsights: true});
}

function showTab(name) {
    document.querySelectorAll('.tab').forEach(t => t.classList.remove('active')); 
    document.querySelectorAll('.tab-content').forEach(t => t.classList.remove('active')); 
    document.getElementById(`${name}-tab`).classList.add('active'); 
    document.querySelectorAll('.tab').forEach(t => { if (t.getAttribute('onclick')?.includes(`'${name}'`)) t.classList.add('active'); });
    
    if (name === 'compiled') renderArtifactsTab();
    if (name === 'research' && typeof boardOnTabShown === 'function') boardOnTabShown();
    if (name === 'insights') reloadInsightsUnlessGenerating();
    // Chat sessions are created lazily — only when the user actually
    // runs deep research, sends a message, or generates insights.
}

function toggleChatFullscreen() { document.getElementById('chat-list').classList.toggle('expanded-pane'); }
function toggleFileFullscreen() { document.getElementById('file-list').classList.toggle('expanded-pane'); }
function downloadInsights() { if (!currentInsightsData) return; const a = document.createElement('a'); a.href = URL.createObjectURL(new Blob([JSON.stringify(currentInsightsData,null,2)], {type:'application/json'})); a.download = 'insights.json'; a.click(); }

async function downloadInsightsAsReport() {
    if (!currentInsightsData) {
        alert("No insights data available. Generate insights first.");
        return;
    }
    const btn = document.getElementById('download-report-btn');
    const original = btn ? btn.innerText : '';
    if (btn) { btn.innerText = "Generating..."; btn.disabled = true; }

    try {
        const res = await fetch('/api/insights/report', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                insights: currentInsightsData,
                project_name: currentProject || 'Research Project'
            })
        });
        if (!res.ok) {
            const err = await res.json().catch(() => ({ error: 'Report generation failed' }));
            throw new Error(err.error || 'Report generation failed');
        }
        const blob = await res.blob();
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = `Strategic_Insights_${(currentProject || 'Report').replace(/\s+/g, '_')}.docx`;
        a.click();
        URL.revokeObjectURL(url);
    } catch (e) {
        alert("Report download failed: " + e.message);
    } finally {
        if (btn) { btn.innerText = original; btn.disabled = false; }
    }
}

function initSidebarResize() {
    const sidebar = document.getElementById('sidebar');
    const handle = document.getElementById('sidebar-resize');
    if(!sidebar || !handle) return;
    let isResizing = false;
    handle.addEventListener('mousedown', (e) => { isResizing = true; handle.classList.add('active'); document.body.style.cursor = 'col-resize'; e.preventDefault(); });
    document.addEventListener('mousemove', (e) => { if (isResizing) sidebar.style.width = Math.max(200, Math.min(500, e.clientX)) + 'px'; });
    document.addEventListener('mouseup', () => { isResizing = false; handle.classList.remove('active'); document.body.style.cursor = 'default'; });
}

// Override to render markdown in edit responses.
async function sendEditMessage() {
    const input = document.getElementById('edit-chat-input');
    const instruction = input.value;
    if (!instruction) return;
    const chatMessages = document.getElementById('edit-chat-messages');
    chatMessages.innerHTML += `<div class="message user">${escapeHtml(instruction)}</div>`;
    input.value = '';

    // Build request body - include linked source files for phase documents
    const hasSelection = lastSelection && lastSelection.trim().length > 0;
    const editBody = {
        selected_text: hasSelection ? lastSelection : "",
        instruction,
        full_chapter: getOutputContent(),
        project: currentProject,
    };
    if (currentPhaseKey && currentInsightsData) {
        const linkedIds = currentInsightsData.phases[currentPhaseKey]?.linked_file_ids || [];
        if (linkedIds.length > 0) {
            editBody.source_file_ids = linkedIds;
        }
    }

    try {
        const res = await fetch('/api/edit', { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(editBody) });
        const data = await res.json();
        if (data.error) {
            chatMessages.innerHTML += `<div class="message assistant"><em>Error: ${escapeHtml(data.error)}</em></div>`;
            chatMessages.scrollTop = chatMessages.scrollHeight;
            return;
        }
        const responseHtml = renderMarkdown(data.response || "");
        const actionLabel = hasSelection ? 'Replace Selection' : 'Append to Document';
        chatMessages.innerHTML += `<div class="message assistant"><div class="message-content markdown-body">${responseHtml}</div><button class="btn-primary" onclick="applyTextReplacement(decodeURIComponent('${encodeURIComponent(data.response)}'))">${actionLabel}</button></div>`;
    } catch (err) {
        chatMessages.innerHTML += `<div class="message assistant"><em>Request failed: ${escapeHtml(err.message)}</em></div>`;
    }
    chatMessages.scrollTop = chatMessages.scrollHeight;
}
