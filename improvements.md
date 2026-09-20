# Jev Integration Improvements

This document outlines architectural improvements and integration points for incorporating **Jev** (TypeSafe AI's flagship System One model) into the **LLM Journal** pipeline.

---

## 1. Why Jev?

In the current architecture, the generative model (`gemma4` via Ollama) handles all downstream tasks simultaneously:
- Noise detection
- Folder/category classification (PARA method)
- Wiki-link filtering
- Action item extraction
- Title & summary generation

While generative LLMs excel at freeform synthesis (writing titles, readable summaries, and formatting markdown), they can be nondeterministic, slow, and prone to formatting failures when performing discrete classification, filtering, or scoring.

**Jev** provides fast, deterministic, typed primitives (**`Noul`**, **`Choice`**, **`Score`**) with calibrated probabilities. Incorporating Jev offloads discrete semantic decision-making and metadata scoring into typed Python control flow before or alongside LLM generation.

---

## 2. Integration Opportunities

### A. Low-Signal & Whisper Artifact Gating (`Noul`)
- **Problem**: Accidental pocket recordings, background noise, or audio silence often cause Whisper to hallucinate repetitive phrases (e.g., "Thank you for watching") or transcribe meaningless utterances, generating clutter in the Obsidian vault.
- **Implementation**:
  ```python
  from typesafe_sdk import Noul

  is_meaningful_content = Noul(
      instructions="Does `transcript` contain coherent personal thoughts, notes, or tasks, rather than background noise, gibberish, or accidental recordings?",
      criteria={
          "true": "Speaker is expressing coherent thoughts, ideas, to-dos, or personal reflections.",
          "false": "Repetitive audio artifacts, background silence, accidental unintelligible snippets.",
      },
  )
  ```
- **Action**:
  - If probability $< 0.35$, mark status as `SKIPPED_LOW_SIGNAL` in SQLite and skip note creation and RAG indexing.

---

### B. Direct PARA Category & Folder Routing (`Choice`)
- **Problem**: Routing notes to `Projects/`, `Areas/`, `Resources/`, or `Thoughts/` is currently parsed from raw LLM JSON, which occasionally yields invalid or hallucinated categories.
- **Implementation**:
  ```python
  from typesafe_sdk import Choice

  category = Choice(
      instructions="Classify this voice note into an organizational category according to the PARA method.",
      criteria={
          "Projects": "Active efforts with specific deadlines, milestones, or deliverables.",
          "Areas": "Long-term spheres of responsibility (health, finance, home, personal growth).",
          "Resources": "Reference material, topics of interest, tools, technical concepts.",
          "Thoughts": "Reflections, stream of consciousness, quick ideas, personal musings.",
      },
  )
  ```
- **Action**:
  - Deterministically drives the destination directory (`OBSIDIAN_VAULT / category`).
  - Provides confidence score (`choice.confidence`) to fall back to an `Inbox/` or `Thoughts/` folder if ambiguous.

---

### C. Action Item Detection & Urgency Triage (`Noul` + `Score`)
- **Problem**: Voice notes often touch on tasks, but simple heuristics or generative prompts can over-extract passive thoughts as action items or miss high-priority tasks.
- **Implementation**:
  ```python
  from typesafe_sdk import Noul, Score

  questions = {
      "has_action_items": Noul(
          instructions="Does the speaker mention actionable tasks, to-dos, or commitments they need to do?"
      ),
      "urgency": Score(
          instructions="Rate the urgency and time sensitivity of any action items or commitments mentioned.",
          criteria=[
              "no action items / someday-maybe",
              "routine / low priority",
              "time-sensitive / urgent deadline",
          ],
      ),
  }
  ```
- **Action**:
  - Add Obsidian frontmatter fields: `priority: high` and tags `#urgent` or `#todo`.
  - Conditionally run task extraction prompts only when `has_action_items > 0.5`.

---

### D. Semantic RAG Re-ranking & Wiki Link Verification (`Noul`)
- **Problem**: Vector retrieval via cosine distance (`RAG_MAX_DISTANCE = 0.60`) may retrieve superficially similar notes that are semantically unrelated, leading to hallucinated or irrelevant wiki links in frontmatter.
- **Implementation**:
  - Pass ChromaDB candidate notes to Jev to verify relevance before inserting them into the LLM context:
  ```python
  questions = {
      f"relevant_{idx}": Noul(
          instructions=f"Is note '{cand['title']}' genuinely relevant context for the voice note in `transcript`?"
      )
      for idx, cand in enumerate(candidates)
  }
  ```
- **Action**:
  - Only inject verified notes into the Ollama prompt context, keeping prompts focused and preventing bad `[[wiki-links]]`.

---

### E. Mood & Sentiment Tracking (`Score`)
- **Problem**: Journal entries and voice notes lack structured emotional or cognitive state metadata over time.
- **Implementation**:
  ```python
  from typesafe_sdk import Score

  mood = Score(
      instructions="Assess the speaker's emotional state and tone in `transcript`.",
      criteria=[
          "stressed / overwhelmed / frustrated",
          "neutral / analytical / calm",
          "energized / inspired / excited",
      ],
  )
  ```
- **Action**:
  - Adds `mood_score: 1.8` and `sentiment: energized` to the note's frontmatter for Obsidian Dataview dashboards and personal analytics.

---

## 3. Recommended Implementation Architecture

### Parallel Batch Execution
All Jev evaluations should execute in a single round-trip HTTP request right after Whisper finishes transcribing:

```
[ Whisper Transcription ]
          │
          ▼
[ Jev System One (Batch Call) ]
  ├─ Noise / Artifact Gating (Noul) ──────► (If < 0.35: Skip & log)
  ├─ PARA Category Routing (Choice)
  ├─ Action Item Gating (Noul)
  ├─ Urgency & Mood Scoring (Score)
  └─ RAG Context Re-ranking (Noul)
          │
          ▼
[ Ollama (Gemma4) - Creative Synthesis Only ]
  ├─ High-signal Title
  └─ 1-2 sentence Summary
          │
          ▼
[ Obsidian Markdown Note ]
```

### Setup Requirements
1. Install `typesafe-sdk`:
   ```bash
   pip install typesafe-sdk
   ```
2. Configure `.env`:
   ```ini
   TYPESAFE_API_KEY=your_typesafe_api_key_here
   ```
3. Integrate through a dedicated module (e.g. `jev_classifier.py`) called within `processor.py`.
