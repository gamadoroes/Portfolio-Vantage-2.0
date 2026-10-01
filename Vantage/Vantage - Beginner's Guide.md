# Vantage — Beginner's Guide

*A complete step-by-step walkthrough for someone brand new*

---

## What Is This Tool?

Vantage is a web app that helps you research competitors in Australian higher education. You give it questions, and it uses AI to conduct Deep Research queries, and generates insights based on this output. The interface allows a human-in-the-loop to easily edit the generated source files.

---

## Before You Start: Opening the App

1. From the Desktop of the Virtual Machine, select the researchapl folder
2. Double click on the 'run' file in the folder. That will open a command line window.
3. Once this has opened, you can go to `http://localhost:5000` in your browser (like a website address).
4. Open a browser (Chrome works best).
5. Type or paste that address into the address bar and press Enter.
6. You should see the Vantage interface.

---

## Understanding the Screen

The screen is divided into two main areas:

```
┌─────────────────┬──────────────────────────────┐
│   LEFT SIDEBAR  │        MAIN AREA              │
│                 │  [tabs across the top]        │
│  • Workflows    │                               │
│  • Research     │  This is where you work       │
│    Sessions     │                               │
│  • Source Data  │                               │
└─────────────────┴──────────────────────────────┘
```

**Left Sidebar** has three sections:
- **Workflows** — your research projects (like folders)
- **Research Sessions** — your conversation history with the AI
- **Source Data** — the documents you've uploaded

**Main Area** has six tabs:
- **Builder** — chat with the AI, generate content
- **Prompt Developer** — build structured research prompts and systematically go through the seven stages of the research
- **Insights** — automated strategic dashboard
- **Methodology** — rules that shape how the AI behaves
- **Objective** — what your research is trying to achieve
- **Artifacts** — saved outputs and reports

---

## Step 1 — Create Your First Workflow

A **Workflow** is like a project folder. Everything you do lives inside one.

1. In the left sidebar, find the **Workflows** section at the top.
2. Click the **+** button next to "Workflows".
3. A prompt will ask you to name it. Use something descriptive, like `MBA_Competitors_2024`.
4. Press Enter. Your new workflow is now selected.

> **Tip:** Each workflow is completely separate. Create one per research topic or degree program you're investigating.

---

## Step 2 — Set Your Research Objective

Before uploading anything, tell the AI what you're trying to find out.

1. Click the **Objective** tab at the top.
2. In the text box, type a plain-English description of your goal. For example:
   > *"We are researching competitors offering online MBA programs in Australia. We want to understand their pricing, marketing, student targeting, and academic content so OES can identify gaps and opportunities."*
3. Click **Save Objective**.

> This acts like a briefing note. The AI reads this every time it answers your questions.

---

## Step 3 — Set the Methodology 

This tells the AI how to behave — its "persona" and rules.

1. Click the **Methodology** tab.
2. There may already be text here. If you're a beginner, leave it as-is.
3. If you want to add rules (e.g. *"Always cite your sources"* or *"Focus only on Australian providers"*), type them here.
4. Click **Save Rules**.

> If it's blank, the AI will still work fine — it just won't have specific constraints.

---

## Step 4 — Upload Source Documents

Source documents are the raw material the AI works from. These can be:
- Competitor website text (copy-pasted into a `.txt` file)
- PDF reports converted to text
- Marketing brochures saved as text
- CSV data files
- Any `.txt`, `.md`, `.csv`, `.docx`, or `.json` file

**To upload a file:**

1. In the left sidebar, find the **Source Data** section.
2. Click the **+** button next to "Source Data".
3. A file picker will open. Select your file.
4. It will appear in the list under Source Data.

**To make a file "active" (used by the AI):**

- You'll see a checkbox next to each file. Files with **☑** (checked) are included when the AI answers your questions.
- Click a filename to check/uncheck it.
- Only check the files relevant to what you're currently researching — don't check everything at once.

> **Important:** The AI only reads checked files. If a file is unchecked, the AI ignores it.

---

## Step 5 — Start a Research Session (Chat with the AI)

1. In the left sidebar, click the **+** button next to "Research Sessions".
2. A new blank chat opens in the **Builder** tab.
3. In the text box at the bottom, type your question. For example:
   > *"Summarise the key features of each competitor based on the uploaded documents."*
4. Click **Generate**.
5. The AI will respond in real time — you'll see text appearing as it writes.

**Tips for asking good questions:**
- Be specific: *"What is RMIT's tuition fee for their online MBA?"* works better than *"Tell me about RMIT"*
- Refer to the documents: *"Based on the uploaded documents, what..."*
- Ask follow-ups: Each session remembers the conversation history

**To stop the AI mid-response:** Click the red **Stop** button that appears while it's generating.

---

## Step 6 — Save Your Work as an Artifact

An **Artifact** is a saved document — a piece of analysis or writing you want to keep.

**Method 1 — Artifact Mode:**

1. In the Builder tab, tick the checkbox labelled **Artifact Mode (Generate Files)** before you send your message.
2. When the AI responds, it generates a formatted document instead of a chat reply.
3. An editor panel opens on the right showing the document.
4. Click **Save to Files** to name and save it.

**Method 2 — Refine with AI:**

1. If an artifact is open, you can edit it directly in the text editor.
2. Use the **AI Assistant** panel on the right (click "AI" on the edge if it's hidden) to ask the AI to refine specific parts.
3. Highlight text, click **Use Selection**, then type instructions like *"Make this more concise"* and click **Refine**.

---

## Step 7 — Use the Prompt Developer (For Structured Research)

The **Prompt Developer** tab helps you build research prompts using OES's 7-phase research framework, without needing to know how to write AI prompts.

1. Click the **Prompt Developer** tab.
2. Under **Select Framework**, choose a research phase from the dropdown. For example:
   - *Phase 1: The Landscape* — for a broad market overview
   - *Phase 2: The Student* — for student persona analysis
   - *Phase 4: Product Features* — for comparing course features
3. Fill in the form that appears (e.g. competitor names, degree type, market).
4. Click **Generate Master Prompt**.
5. A suggested prompt appears. You can edit it if needed.
6. You have two options:
   - **Copy** — paste it into the Builder tab manually
   - **Run Deep Research** — sends it directly to a powerful web-research AI (OpenAI) that searches the internet for you

---

## Step 8 — Run Deep Research (Internet Search)

**Deep Research** is different from normal chat. Instead of analysing your uploaded files, it searches the **live internet** and produces a long, cited report. It takes several minutes.

1. In the Prompt Developer tab, generate a prompt (Step 7 above).
2. Click **Run Deep Research**.
3. A progress indicator appears at the bottom right of the screen showing *"X runs in progress"*.
4. Click that indicator (or go to the **Prompt Developer** tab) to check the **Research Runs** list.
5. When complete, the run will show as finished and you can view the report.

> Deep research is best for: *"What are all Australian universities offering online MBAs?"* — things that require broad web knowledge, not just your uploaded files.

---

## Step 9 — Use the Insights Dashboard

The **Insights** tab gives you an automated strategic analysis dashboard across 7 phases.

1. Make sure you have source files uploaded and checked (Step 4).
2. Click the **Insights** tab.
3. Click **Refresh All** to generate the full analysis.
4. An instruction box will appear — you can add specific guidance (e.g. *"Focus on pricing"*) or leave it blank and click **Run All**.
5. The AI will populate each phase one by one. This takes a few minutes.
6. Once done, you'll see:
   - **Competitor Landscape** — a grid of identified competitors with key details
   - **7-Phase Strategic Analysis** — expandable sections for each research phase

**To export:**
- Click **Report** to download a Word document (`.docx`)
- Click **JSON** to download the raw data

---

## Step 10 — View and Manage Artifacts

All saved outputs live in the **Artifacts** tab.

1. Click the **Artifacts** tab.
2. You'll see a list of everything you've saved.
3. Click any artifact to open and read it.
4. Artifacts can also be checked as **Source Data** — meaning you can feed a previous report back into the AI as context for the next question.

---

## Quick Reference Card

| Task | Where to go |
|---|---|
| Start a new project | Sidebar → Workflows → **+** |
| New chat/question | Sidebar → Research Sessions → **+** |
| Upload a document | Sidebar → Source Data → **+** |
| Set research goals | **Objective** tab → Save |
| Set AI behaviour | **Methodology** tab → Save |
| Ask the AI a question | **Builder** tab → type → Generate |
| Build a structured prompt | **Prompt Developer** tab |
| Search the internet | Prompt Developer → Run Deep Research |
| Strategic dashboard | **Insights** tab → Refresh All |
| See saved outputs | **Artifacts** tab |

---

## Common Questions

**Q: The AI gave me a wrong answer. What do I do?**
Check which files are active (checked) in Source Data. If the relevant document isn't checked, the AI can't see it. Also check your Objective and Methodology — they shape how the AI interprets questions.

**Q: Where is my work saved?**
Everything is saved automatically on the server. When you come back, select your Workflow from the dropdown and your sessions, files, and artifacts will be there.

**Q: Can I edit an artifact after saving it?**
Yes. Go to Artifacts, open the artifact, and click back through to the Builder tab's output view to edit it with AI assistance.

**Q: What's the difference between the AI chat and Deep Research?**
Chat (Builder tab) reads your uploaded files and answers based on them. Deep Research (Prompt Developer tab) searches the live internet and writes a full report — it's slower but much broader.

**Q: I got an error. What do I do?**
Tell someone on the AI team. Describe what you clicked and what the error message said.
