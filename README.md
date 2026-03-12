# Nano PDF Studio

AI-powered PDF editor with a chat-based editing interface. Upload a PDF, describe changes in natural language, and watch the AI edit your pages in real time.

The system uses a two-tier editing architecture: an AI planner decomposes each instruction into a mix of **programmatic** operations (direct PDF structure edits via pikepdf, ~100ms) and **visual** operations (AI image regeneration via Google Gemini). Text replacements happen instantly in the PDF structure; layout changes, chart edits, and complex visual modifications go through the image model.

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
│  │  │(Gemini    │  │(pikepdf   │  │ (Gemini │ │             │
│  │  │ 2.5 Flash)│  │ content   │  │ Image)  │ │             │
│  │  │           │  │ streams)  │  │         │ │             │
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
   - `text_replace` / `style_change` → **PdfEditor** modifies the PDF content stream directly (~100ms)
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

- **Two-tier editing** — text replacements execute programmatically in ~100ms; visual changes use AI image generation
- **AI planner** — decomposes natural language instructions into optimal operation mixes (programmatic + visual)
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

# Programmatic PDF editor tests (9 tests)
.venv/bin/python -m tests.test_pdf_editor

# Orchestrator end-to-end tests (requires API key, 3 tests)
.venv/bin/python -m tests.test_orchestrator_e2e

# Full pipeline test — programmatic + visual + hybrid (requires API key, 3 tests)
.venv/bin/python -m tests.test_pipeline_e2e

# Gemini API integration test
.venv/bin/python -m tests.test_model_provider

# Visual description test (requires API key)
.venv/bin/python -m tests.test_visual_description

# REST API manual testing
curl -X POST http://localhost:8000/api/pdf/upload -F "file=@test.pdf"
curl http://localhost:8000/api/pdf/{session_id}/page/1/image -o page1.png
curl -X POST http://localhost:8000/api/pdf/{session_id}/export -o edited.pdf
```

## License

MIT
