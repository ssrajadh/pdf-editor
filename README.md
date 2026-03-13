# Nano PDF Studio

AI-powered PDF editor with a chat-based editing interface. Upload a PDF, describe changes in natural language, and watch the AI edit your pages in real time.

The system uses a two-tier editing architecture: an AI planner decomposes each instruction into a mix of **programmatic** operations (PyMuPDF redact-and-overlay, ~10-100ms) and **visual** operations (AI image regeneration via Google Gemini). Text replacements happen instantly in the PDF structure; layout changes, chart edits, and complex visual modifications go through the image model.

## Quick Start

```bash
git clone <repo-url> && cd pdf-editor
cp .env.example .env
# Add your GEMINI_API_KEY to .env (requires billing-enabled Google AI project)
docker compose -f docker/docker-compose.yml up --build
# Open http://localhost:3000
```

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│  Browser (React + Vite + Tailwind)                          │
│  ┌──────────┬──────────────┬──────────────┐                 │
│  │Thumbnails│  PDF Viewer  │  Chat Panel  │                 │
│  │          │  + Before/   │  + WebSocket │                 │
│  │          │    After     │    progress  │                 │
│  └──────────┴──────────────┴──────────────┘                 │
└──────────────┬──────────────────────┬───────────────────────┘
               │ REST API            │ WebSocket
┌──────────────▼──────────────────────▼───────────────────────┐
│  FastAPI Backend                                            │
│  ┌──────────┬──────────────┬──────────────┐                 │
│  │PDF Router│ Edit Router  │ Edit Engine  │                 │
│  │(upload,  │ (WS, history,│ (concurrency │                 │
│  │ render,  │  revert)     │  locks)      │                 │
│  │ export)  │              │              │                 │
│  └──────────┴──────┬───────┴──────┬───────┘                 │
│                    │              │                          │
│  ┌─────────────────▼──────────────▼───────────┐             │
│  │            Orchestrator                     │             │
│  │  ┌───────────┐  ┌───────────┐  ┌─────────┐ │             │
│  │  │ Planner   │  │PdfEditor  │  │ Visual  │ │             │
│  │  │(Gemini    │  │(PyMuPDF   │  │ (Gemini │ │             │
│  │  │ 2.5 Flash)│  │ redact &  │  │ Image)  │ │             │
│  │  │           │  │ overlay)  │  │         │ │             │
│  │  └───────────┘  └───────────┘  └─────────┘ │             │
│  └─────────────────────────────────────────────┘             │
│  ┌────────────────────┐  ┌──────────────────┐               │
│  │  Session Manager   │  │  Model Provider  │               │
│  │  (file storage)    │  │  (Gemini API)    │               │
│  └────────────────────┘  └──────────────────┘               │
└─────────────────────────────────────────────────────────────┘
```

### Edit Pipeline

1. **User sends instruction** via WebSocket ("Change Q3 to Q4 and make the background blue")
2. **Planner** (Gemini 2.5 Flash) analyzes the page text, visual elements, and instruction → produces an `ExecutionPlan` with typed operations
3. **Orchestrator** executes operations in order:
   - `text_replace` / `style_change` → **PdfEditor** redacts and overlays via PyMuPDF (~10-100ms)
   - `visual_regenerate` → **Gemini Image** regenerates the page visually (~5-10s)
4. **Compound degradation prevention**: visual model always receives a PDF-rendered base image (from `working.pdf` or `original.pdf`), never a previously AI-generated one
5. **Text layer handling**: programmatic edits produce perfect text layers; visual-only edits mark the layer as stale

## Development Setup

**Backend** (requires Python 3.12+, poppler-utils):

```bash
cd backend
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000
```

**Frontend** (requires Node 20+):

```bash
cd frontend
npm install
npm run dev
```

Frontend runs at http://localhost:5173 with Vite proxying `/api` to the backend.

## Environment Variables

| Variable | Default | Purpose |
|----------|---------|---------|
| `GEMINI_API_KEY` | (required) | Google Gemini API key (billing must be enabled) |
| `GEMINI_MODEL` | `gemini-2.5-flash-image` | Gemini model for image editing |
| `PLANNING_MODEL` | `gemini-2.5-flash` | Gemini model for edit planning |
| `PLANNING_MODEL_TEMPERATURE` | `0.1` | Planning model temperature |
| `MODEL_PROVIDER` | `gemini` | AI provider selection |
| `MODEL_TIMEOUT_SECONDS` | `60` | API call timeout |
| `STORAGE_PATH` | `/data` (Docker), `./data` (local) | Session file storage |
| `MAX_FILE_SIZE_MB` | `50` | Max PDF upload size |
| `ALLOWED_ORIGINS` | `http://localhost:5173` | CORS origins (comma-separated) |

## Key Features

- **Two-tier editing** — text replacements execute programmatically in ~10-100ms (PyMuPDF redact-and-overlay); visual changes use AI image generation
- **Layout-aware AI planner** — analyzes page complexity, fonts, columns, and density to route each operation optimally
- **Chat-based editing** — describe changes in natural language
- **Real-time progress** — WebSocket streams edit stages with plan details and per-operation progress
- **Before/After toggle** — compare original and edited versions side by side
- **Text layer preservation** — programmatic edits extract perfect text layers from the modified PDF
- **Compound degradation prevention** — visual model always sees a clean PDF render, never a prior AI output
- **PDF export** — download the edited PDF with all changes merged in
- **Page versioning** — every edit is saved as a new version; revert to any previous state
- **Session cleanup** — background task removes sessions older than 24 hours

## Testing

```bash
cd backend

# PyMuPDF editor unit tests — 22 tests, no API key
.venv/bin/python -m tests.test_pdf_editor_v2

# Edge-case hardening tests — 28 tests, no API key
# (multi-match, batch replace, protected PDFs, font calibration, rect expansion)
.venv/bin/python -m tests.test_edge_cases

# Layout-aware planner tests — no API key for offline, some need API key
.venv/bin/python -m tests.test_layout_awareness

# End-to-end phase 2.5 — multi-document validation matrix (requires API key)
# Tests 4 documents × multiple edits, prints summary table with benchmarks
.venv/bin/python -m tests.test_e2e_phase2_5 --all

# Orchestrator E2E (requires API key)
.venv/bin/python -m tests.test_e2e_orchestrator --all

# Manual API tests against a running server (requires backend running on :8000)
TEST_PDF_PATH=../your-resume.pdf .venv/bin/python -m tests.test_manual_api
```

## Performance

Benchmarks from `test_e2e_phase2_5 --all` (Gemini 2.5 Flash planning + Gemini 2.5 Flash Image visual):

| Document | Edit | Path | Time |
|----------|------|------|------|
| simple_report | 2024 → 2025 (5 ops, batch) | programmatic | 46ms edit, ~7s total |
| simple_report | Report → Analysis | visual (overflow risk) | ~20s total |
| simple_report | Add blue border | visual | ~14s total |
| presentation_slide | Q3 → Q4 | programmatic | 10ms edit, ~3s total |
| presentation_slide | Chart placeholder → bar chart | visual | ~16s total |
| resume (CID fonts) | 2024 → 2025 (2 ops) | programmatic | 105ms edit, ~7s total |
| resume (CID fonts) | Software → Hardware (4 ops) | mixed (3 prog + 1 fallback) | ~26s total |
| resume (CID fonts) | GPA → long text | visual (overflow) | ~19s total |
| resume (CID fonts) | Darken header background | visual | ~17s total |
| colored_header | Project Alpha → Project Beta | mixed (1 prog + 1 fallback) | ~12s total |
| colored_header | 2024 → 2025 (2 ops) | programmatic | 30ms edit, ~4s total |

**Unit test benchmarks** (no API key, PyMuPDF only):

| Operation | Time |
|-----------|------|
| Single text replace (simple font) | ~120ms |
| Batch replace (3 ops, single page) | ~116ms |
| Resume name swap (CID fonts) | ~600ms |
| Multi-match with context disambiguation | ~140ms |

**Key observations:**
- Planning LLM latency (~3-7s) dominates total time; actual programmatic edits are 10-105ms
- PyMuPDF redact-and-overlay handles all font types including CID (Type0) — no escalation needed
- Batch text_replace ops execute atomically (one open/save cycle) for consistency
- Multi-match disambiguation uses `context_before`/`context_after`; ambiguous cases escalate cleanly
- Font size calibration ensures replacement text matches original bounding box width (±15% clamp)
- Overflow detection escalates replacements exceeding bounding box width to visual
- Protected PDFs (restricted modification permissions) produce clear error messages
- Compound degradation prevention: visual base images always rendered from PDF, never from prior AI output

## License

MIT
