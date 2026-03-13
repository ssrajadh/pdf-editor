# PDF Editor - Project Context

AI-powered PDF editor ("Nano PDF Studio") with a chat-based editing interface. Users upload PDFs, view rendered pages, and submit natural-language edit instructions processed by a two-tier editing system: programmatic PDF text editing (PyMuPDF redact-and-overlay) for text changes, and AI image generation (Gemini) for visual changes. An AI planner decomposes each instruction into the optimal mix of operations.

## Architecture

```
frontend/ (React 19 + Vite 5 + TypeScript + Tailwind CSS 3)
   ↕ REST API + WebSocket (Vite proxy in dev, nginx in Docker)
backend/  (FastAPI + Python 3.12)
   ├── Orchestrator (planning + execution coordination)
   │   ├── Planner (Gemini 2.5 Flash — text-only, structured JSON output)
   │   ├── PdfEditor (PyMuPDF redact-and-overlay — text_replace, style_change)
   │   └── Visual engine (Gemini 2.5 Flash Image — visual_regenerate)
   ├── poppler (pdftoppm for rendering)
   ├── pdfplumber (text extraction)
   └── pikepdf + reportlab (PDF export with text layer merge)
```

## Directory Structure

```
pdf-editor/
├── backend/
│   ├── app/
│   │   ├── main.py              # FastAPI app, CORS, /health, request logging middleware, session cleanup task
│   │   ├── config.py            # Pydantic Settings from .env (gemini keys, planning model, storage, etc.)
│   │   ├── routers/
│   │   │   ├── pdf.py           # PDF upload (pikepdf validation), page image, text extraction, text layer, export
│   │   │   └── edit.py          # WebSocket edit endpoint (3-arg progress: stage, message, extra), history, revert
│   │   ├── services/
│   │   │   ├── orchestrator.py  # Orchestrator: PageContext, planner (plan_edit), execute pipeline, text layer handling
│   │   │   ├── pdf_editor.py    # PdfEditor: PyMuPDF redact-and-overlay text replace + style change, font matching, bg detection
│   │   │   ├── edit_engine.py   # EditEngine: concurrency locks, delegates to Orchestrator
│   │   │   ├── pdf_service.py   # pdftoppm rendering, pdfplumber text extraction, compound degradation prevention, export
│   │   │   └── model_provider.py # Abstract ModelProvider, GeminiProvider (edit_image, analyze_image, plan_edit), ProviderFactory
│   │   ├── prompts/
│   │   │   └── orchestrator_plan.py # System + user prompt templates for the planning LLM
│   │   ├── models/
│   │   │   └── schemas.py       # Pydantic models: ExecutionPlan, TextReplaceOp, StyleChangeOp, VisualRegenerateOp,
│   │   │                        #   OperationResult, ExecutionResult, TextReplaceResult, StyleChangeResult, etc.
│   │   └── storage/
│   │       └── session.py       # SessionManager: CRUD, get_working_pdf_path (lazy copy), cleanup_old_sessions
│   ├── tests/
│   │   ├── test_pdf_editor.py         # 9 tests: text replace, overflow, style, sequential edits, original untouched
│   │   ├── test_orchestrator_e2e.py   # 3 tests: pure text, pure visual, hybrid plans (requires API key)
│   │   ├── test_pipeline_e2e.py       # 3 tests: programmatic path, visual base image, hybrid execution (requires API key)
│   │   ├── test_visual_description.py # Vision model visual element description (requires API key)
│   │   ├── test_model_provider.py     # Gemini API integration test
│   │   ├── test_edit_ws.py            # WebSocket endpoint tests
│   │   └── test_orchestrator_prompt.py # Prompt & JSON parsing tests
│   ├── requirements.txt
│   └── pyproject.toml
├── frontend/
│   ├── src/
│   │   ├── App.tsx              # Root: upload screen, editor layout, reconnect banner, keyboard shortcuts, toast, header stats
│   │   ├── main.tsx             # React entry point
│   │   ├── index.css            # Tailwind + custom animations (fadeIn, slideDown, slideUp)
│   │   ├── components/
│   │   │   ├── PdfViewer.tsx    # Page image display with loading/error states, before/after toggle, edit overlay
│   │   │   ├── PageThumbnails.tsx # Sidebar thumbnails with lazy loading, edit indicators, virtual scroll
│   │   │   ├── ChatPanel.tsx    # Chat UI: messages, suggestions, retry button, progress bubbles
│   │   │   ├── BeforeAfterToggle.tsx # Segmented control to switch between original/edited
│   │   │   └── Toast.tsx        # Auto-dismiss notification (export success, errors)
│   │   ├── hooks/
│   │   │   ├── useWebSocket.ts  # WebSocket with auto-reconnect (5 retries, exponential backoff), isReconnecting state
│   │   │   └── usePdfSession.ts # Central state: session, pages, versions, chat, edit count, session duration, retry
│   │   ├── services/
│   │   │   └── api.ts           # REST client: uploadPdf, getPageImageUrl, getPageText, getSessionInfo, exportPdf
│   │   └── types/
│   │       └── index.ts         # TypeScript interfaces matching backend schemas
│   ├── index.html
│   ├── vite.config.ts           # Vite config with /api proxy (HTTP + WebSocket) to backend
│   ├── tailwind.config.js
│   ├── postcss.config.js
│   └── tsconfig.json            # target ES2023 for findLastIndex
├── docker/
│   ├── Dockerfile.backend       # Python 3.12-slim + poppler-utils
│   ├── Dockerfile.frontend      # Multi-stage: Node 20 build → nginx:alpine serving
│   ├── docker-compose.yml       # backend:8000, frontend(nginx):3000, shared pdf_data volume
│   └── nginx.conf               # Proxy /api/ to backend with WebSocket upgrade, SPA fallback
├── .env                         # Actual config (not committed)
├── .env.example                 # Template
├── .gitignore
├── AGENTS.md
└── README.md
```

## Environment Variables

| Variable | Default | Purpose |
|----------|---------|---------|
| GEMINI_API_KEY | "" | Google Gemini API key (billing must be enabled) |
| GEMINI_MODEL | gemini-2.5-flash-image | Gemini model for image editing |
| PLANNING_MODEL | gemini-2.5-flash | Gemini model for edit planning (text-only, structured JSON) |
| PLANNING_MODEL_TEMPERATURE | 0.1 | Planning model temperature (low for deterministic plans) |
| MODEL_PROVIDER | gemini | AI provider selection |
| MODEL_TIMEOUT_SECONDS | 60 | API call timeout |
| STORAGE_PATH | /data (Docker), ./data (local) | Session file storage |
| MAX_FILE_SIZE_MB | 50 | Max PDF upload size |
| ALLOWED_ORIGINS | http://localhost:5173 | CORS origins (comma-separated) |

Config loads from `../.env` then `.env` (so it works from both `backend/` and project root).

## API Endpoints

| Method | Path | Purpose |
|--------|------|---------|
| GET | /health | Health check |
| POST | /api/pdf/upload | Upload PDF, validate (pikepdf), create session, render all pages |
| GET | /api/pdf/{session_id}/page/{page_num}/image?v= | Serve rendered page PNG (specific version or latest) |
| GET | /api/pdf/{session_id}/page/{page_num}/text | Extract text + character block positions |
| GET | /api/pdf/{session_id}/page/{page_num}/text-layer | Text layer status and content for current version |
| GET | /api/pdf/{session_id}/info | Session metadata (page count, versions) |
| POST | /api/pdf/{session_id}/export | Export PDF with all edits merged, returns file download |
| WS | /api/edit/ws/{session_id} | Real-time edit: send edit request, receive progress/complete/error |
| GET | /api/edit/{session_id}/page/{page_num}/history | Edit history for a page |
| POST | /api/edit/{session_id}/page/{page_num}/revert/{version} | Revert page to a previous version |

## Session Storage Format

```
{storage_path}/{session_id}/
├── original.pdf                    # Never modified
├── working.pdf                     # Lazy copy — accumulates programmatic edits
├── metadata.json                   # {session_id, filename, page_count, created_at, current_page_versions}
├── pages/
│   ├── page_1_v0.png              # v0 = original render
│   ├── page_1_v1.png              # v1+ = programmatic re-render or AI edit
│   ├── page_2_v0.png
│   └── ...
└── edits/
    ├── page_1_history.json         # [{version, prompt, created_at, text_layer_preserved}]
    ├── page_1_v0_vis_desc.txt      # Cached visual element description for planning
    ├── page_1_v1_text.json         # Text layer: {full_text, blocks} or {stale: true}
    └── page_1_text_layer.json      # Original text layer (fallback for export)
```

## Edit Pipeline — How It Works

### 1. Planning Phase
The orchestrator builds **PageContext** (text blocks, page dimensions, visual element description from a vision model) and sends it along with the user's instruction to the planning LLM (`gemini-2.5-flash`). The planner returns an `ExecutionPlan` — a JSON object with typed operations:

- **`text_replace`**: swap specific text in the PDF structure (programmatic, ~100ms)
- **`style_change`**: modify font, color, size (programmatic, ~100ms)
- **`visual_regenerate`**: send page to AI image model for visual changes (~5-10s)

Each operation has a `confidence` score. Operations with confidence < 0.5 skip the programmatic attempt.

### 2. Execution Phase
Operations execute in `execution_order` (programmatic first, then visual):

**Programmatic path** (`PdfEditor` — PyMuPDF redact-and-overlay):
- Opens `working.pdf` (lazy-copied from `original.pdf`) via `fitz.open()`
- Finds text via `page.search_for()` — returns bounding box rects
- Extracts text properties (font, size, color, flags, baseline origin) via `page.get_text("dict")`
- Detects background color by sampling corner pixels of a rendered clip
- Checks overflow: `fitz.get_text_length()` estimates replacement width vs original bbox
- Redacts original text with `page.add_redact_annot()` + background fill
- Overlays replacement text with `page.insert_text()` using matched standard font
- Works on ALL font types including CID (Type0) — no content stream parsing needed
- Saves working PDF incrementally and re-renders via pdftoppm

**Visual path** (Gemini Image):
- **Compound degradation prevention**: base image is always rendered from `working.pdf` (or `original.pdf` if no programmatic edits), never from a previously AI-generated image
- Sends base image + prompt to `gemini-2.5-flash-image`
- Saves result as new version PNG

**Fallback**: if a programmatic op fails with `escalate=True`, it falls back to visual with the same intent. If all operations fail, a full-page visual fallback runs.

### 3. Text Layer Handling
After execution, the text layer source is determined:

| Scenario | `text_layer_source` | Quality |
|----------|---------------------|---------|
| All programmatic | `programmatic_edit` | Perfect — extracted from working PDF |
| All visual | `ocr` | Stale — marked for future OCR |
| Mixed (programmatic + visual) | `mixed` | Stale — visual ops invalidated the text |
| No edits | `original` | Original text preserved |

### 4. WebSocket Progress
The WebSocket streams rich progress events:
```json
{"type": "progress", "stage": "planning", "message": "Analyzing edit instruction..."}
{"type": "progress", "stage": "planned", "message": "Plan: 2 ops...", "plan": {...}}
{"type": "progress", "stage": "programmatic", "message": "Text replaced: 'Q3' → 'Q4' (47ms)", "op_index": 0}
{"type": "progress", "stage": "generating", "message": "AI editing: make background blue...", "op_index": 1}
{"type": "complete", "result": {"programmatic_count": 1, "visual_count": 1, "total_time_ms": 8234, ...}}
```

## Key Design Decisions

- **Two-tier editing**: programmatic for text (fast, precise, perfect text layer) + visual for layout/charts/images (flexible, AI-powered)
- **Orchestrator planner pattern**: LLM decomposes instructions into optimal operation mix; execution engine routes each operation to the right path
- **Working PDF strategy**: `original.pdf` stays pristine; `working.pdf` (lazy copy) accumulates programmatic edits; visual edits produce images only
- **Compound degradation prevention**: visual model always sees a clean PDF-rendered image via `get_current_base_image()`, never a prior AI output — prevents quality loss over multiple edits
- **PyMuPDF redact-and-overlay editing**: finds text via `page.search_for()`, redacts the original with a background-color-matched rectangle, then overlays replacement text with a style-matched standard font. Works reliably across all font types including CID (Type0) — no content stream manipulation needed
- **Overflow rule**: replacement text width estimated via `fitz.get_text_length()` — if >115% of original bounding box width, escalates to visual
- **Font matching**: maps original fonts to closest standard PDF base font (Helvetica/Times/Courier families) based on font name heuristics and bold/italic flags
- **Background color detection**: samples corner pixels of a rendered clip around the text rect to match the fill color for redaction
- **pdftoppm (poppler) for rendering**: faster and higher quality than Python-native options at 200 DPI
- **pdfplumber for text extraction**: provides character-level position metadata (x0, y0, x1, y1, font, size)
- **Page versioning**: images are `page_{num}_v{version}.png`; metadata tracks current version per page
- **ModelProvider abstraction**: ABC with `edit_image`, `analyze_image`, `plan_edit` methods; ProviderFactory for multi-model support
- **GeminiProvider**: edit uses `responseModalities: ["TEXT", "IMAGE"]`; plan uses `responseMimeType: "application/json"` with separate planning model; retry with exponential backoff on 429/5xx
- **PDF export pipeline**: pikepdf replaces edited pages with image+text-layer overlays; unedited pages pass through byte-identical
- **Text layer merge**: reportlab generates invisible selectable text (renderMode 3), pikepdf `add_overlay` composites onto image page
- **Session cleanup**: background asyncio task runs hourly, deletes sessions >24h old
- **Per-session locking**: asyncio.Lock prevents concurrent edits on the same session
- **Upload validation**: rejects non-PDF, over-size, encrypted (pikepdf.PasswordError), and corrupt files
- **3-arg progress callback**: `(stage, message, extra_data)` passes plan JSON and op_index through to WebSocket
- **Config env_file**: `("../.env", ".env")` tuple so it works whether CWD is `backend/` or project root
- **Frontend min-width**: 1024px floor; not designed for mobile

## Implementation Status

### Done
- Full project scaffolding (backend, frontend, Docker)
- PDF upload pipeline with validation (pikepdf: encrypted, corrupt, password-protected)
- Page image serving with cache headers and version query param
- Text extraction with character-level positional data
- Session management with metadata JSON, working PDF, and auto-cleanup
- Gemini AI provider with retry/backoff on 429+5xx, timeout handling, content filter detection
- Provider factory pattern for future multi-model support
- **Orchestrator planner**: vision-based page analysis, structured JSON planning via Gemini 2.5 Flash
- **Programmatic PDF editor**: PyMuPDF redact-and-overlay (text replace, style change), overflow detection, font matching, background color detection
- **Two-tier execution pipeline**: programmatic-first execution, visual fallback, compound degradation prevention
- **Text layer handling**: programmatic_edit (perfect), ocr (stale), mixed, original
- Rich WebSocket progress with plan data and per-operation tracking
- Per-session locking to prevent concurrent edits
- Edit history tracking and revert-to-version
- PDF export with merged image+text-layer pages (reportlab + pikepdf)
- Request logging middleware
- Background session cleanup (hourly, >24h)
- Frontend: complete editor UI with upload, viewer, thumbnails, chat panel
- Before/after toggle for comparing original vs edited pages
- Lazy-loaded thumbnails with edit indicators
- Chat panel with suggestions, progress bubbles, error display, retry button
- WebSocket auto-reconnect with reconnecting banner
- Toast notifications (export success)
- Keyboard shortcuts: Left/Right arrows for page nav
- Header stats: edit count, session duration
- Docker: multi-stage frontend build (Node → nginx:alpine), backend with poppler
- nginx config with API/WebSocket proxy and SPA fallback
- Docker Compose with healthcheck, shared volume, restart policy

### TODO
- Multi-page batch edits
- Undo/redo stack in the UI
- OCR text layer recovery after visual edits (tesseract)
- Mobile-responsive layout (<1024px)

## Running Tests

```bash
cd backend

# Programmatic PDF editor — 9 tests, no API key needed
.venv/bin/python -m tests.test_pdf_editor

# Orchestrator E2E — 3 tests (pure text, pure visual, hybrid), requires API key
.venv/bin/python -m tests.test_orchestrator_e2e

# Full pipeline E2E — 3 tests (programmatic path, visual base image, hybrid), requires API key
.venv/bin/python -m tests.test_pipeline_e2e

# Visual description — requires API key
.venv/bin/python -m tests.test_visual_description

# Gemini API integration
.venv/bin/python -m tests.test_model_provider
```

## Running Locally

```bash
# Backend
cd backend && source .venv/bin/activate
uvicorn app.main:app --reload --port 8000

# Frontend
cd frontend && npm run dev

# Docker
docker compose -f docker/docker-compose.yml up --build
# Frontend: http://localhost:3000  Backend: http://localhost:8000
```

## Dependencies

**Backend**: fastapi, uvicorn[standard], websockets, python-multipart, pdfplumber, pikepdf, PyMuPDF, Pillow, reportlab, httpx, python-dotenv, pydantic, pydantic-settings

**Frontend**: react, react-dom, lucide-react, react-pdf, typescript, vite, @vitejs/plugin-react, tailwindcss, postcss, autoprefixer

**System**: poppler-utils (pdftoppm for PDF rendering)
