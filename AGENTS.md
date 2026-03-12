# PDF Editor - Project Context

AI-powered PDF editor ("Nano PDF Studio") with a chat-based editing interface. Users upload PDFs, view rendered pages, and submit natural-language edit instructions that are processed by Google Gemini's image generation API. Edits are visual — the AI regenerates page images — with optional text layer preservation for searchable exports.

## Architecture

```
frontend/ (React 19 + Vite 5 + TypeScript + Tailwind CSS 3)
   ↕ REST API + WebSocket (Vite proxy in dev, nginx in Docker)
backend/  (FastAPI + Python 3.12)
   ↕ Gemini API (image editing via gemini-2.5-flash-image)
   ↕ poppler (pdftoppm for rendering)
   ↕ pdfplumber (text extraction)
   ↕ pikepdf + reportlab (PDF export with text layer merge)
```

## Directory Structure

```
pdf-editor/
├── backend/
│   ├── app/
│   │   ├── main.py              # FastAPI app, CORS, /health, request logging middleware, session cleanup task
│   │   ├── config.py            # Pydantic Settings from .env
│   │   ├── routers/
│   │   │   ├── pdf.py           # PDF upload (with pikepdf validation), page image, text extraction, text layer, export
│   │   │   └── edit.py          # WebSocket edit endpoint, edit history, revert
│   │   ├── services/
│   │   │   ├── pdf_service.py   # pdftoppm rendering, pdfplumber text extraction, text layer PDF, merge, export
│   │   │   ├── edit_engine.py   # Edit orchestration: load → extract text → AI edit → save → text layer
│   │   │   └── model_provider.py # Abstract ModelProvider, GeminiProvider, ProviderFactory
│   │   ├── models/
│   │   │   └── schemas.py       # Pydantic models (UploadResponse, EditResult, ChatMessage, TextLayerResponse, etc.)
│   │   └── storage/
│   │       └── session.py       # SessionManager: CRUD + cleanup_old_sessions
│   ├── tests/
│   │   └── test_model_provider.py  # Standalone Gemini API integration test
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
├── original.pdf
├── metadata.json              # {session_id, filename, page_count, created_at, current_page_versions}
├── pages/
│   ├── page_1_v0.png          # v0 = original render, v1+ = AI edits
│   ├── page_1_v1.png
│   ├── page_2_v0.png
│   └── ...
└── edits/
    ├── page_1_history.json    # [{version, prompt, timestamp, processing_time_ms, text_layer_preserved}]
    └── page_1_v1_text.json    # Text layer data (or {"stale": true} if text changed)
```

## Key Design Decisions

- **pdftoppm (poppler) for rendering** instead of Python-native: faster, higher quality at 200 DPI
- **pdfplumber for text extraction**: provides character-level position metadata (x0, y0, x1, y1, font, size)
- **Page versioning**: images are `page_{num}_v{version}.png`; `get_page_image_path` finds latest version via glob or specific version via `?v=` query param
- **ModelProvider abstraction**: ABC with `edit_image(PIL.Image, prompt, history) -> PIL.Image`; ProviderFactory for multi-model support
- **GeminiProvider**: sends image as base64 PNG inline_data + explicit "return an image" instruction; responseModalities: ["TEXT", "IMAGE"]; retry with exponential backoff on 429/5xx; 60s timeout
- **No tesseract**: text layer preservation uses a keyword heuristic (`_prompt_changes_text`) to decide if original text is still valid. If text changed, the layer is marked stale (not searchable in export). Future: orchestrator to route text edits to programmatic editing instead of image model.
- **PDF export pipeline**: pikepdf replaces edited pages with image+text-layer overlays; unedited pages stay original
- **Text layer merge**: reportlab generates invisible selectable text (fontSize trick), pikepdf `add_overlay` composites it onto the image page
- **Session cleanup**: background asyncio task runs hourly, deletes sessions >24h old
- **Request logging middleware**: logs method, path, status code, response time for all non-health endpoints
- **Upload validation**: rejects non-PDF, over-size, encrypted (pikepdf.PasswordError), and corrupt files with specific error messages
- **WebSocket error messages**: content filter blocks, timeouts, and missing-image responses are translated to user-friendly suggestions
- **Pydantic Settings**: `allowed_origins` stored as `str`, split to list via `origins_list` property
- **Config env_file**: `("../.env", ".env")` tuple so it works whether CWD is `backend/` or project root
- **Frontend min-width**: 1024px floor; not designed for mobile

## Implementation Status

### Done
- Full project scaffolding (backend, frontend, Docker)
- PDF upload pipeline with validation (pikepdf: encrypted, corrupt, password-protected)
- Page image serving with cache headers and version query param
- Text extraction with character-level positional data
- Session management with metadata JSON and auto-cleanup
- Gemini AI provider with retry/backoff on 429+5xx, timeout handling, content filter detection
- Provider factory pattern for future multi-model support
- Edit engine: full pipeline (load image → extract text → AI edit → save version → handle text layer → update metadata)
- Per-session locking to prevent concurrent edits
- Edit history tracking and revert-to-version
- WebSocket endpoint with progress streaming (stages: loading, extracting_text, editing, saving, complete)
- Text layer preservation (heuristic-based: preserves original when edit doesn't change text)
- PDF export with merged image+text-layer pages (reportlab + pikepdf)
- Request logging middleware
- Background session cleanup (hourly, >24h)
- Frontend: complete editor UI with upload, viewer, thumbnails, chat panel
- Before/after toggle for comparing original vs edited pages
- Lazy-loaded thumbnails with edit indicators
- Chat panel with suggestions, progress bubbles, error display, retry button
- WebSocket auto-reconnect with reconnecting banner
- Image load error state with retry button
- Toast notifications (export success)
- Keyboard shortcuts: Left/Right arrows for page nav
- Header stats: edit count, session duration
- Fade/slide animations on messages and banners
- Docker: multi-stage frontend build (Node → nginx:alpine), backend with poppler
- nginx config with API/WebSocket proxy and SPA fallback
- Docker Compose with healthcheck, shared volume, restart policy

### TODO
- Orchestrator: route text-changing edits to programmatic text editing instead of image model
- Multi-page batch edits
- Undo/redo stack in the UI
- Mobile-responsive layout (<1024px)

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

## Testing

```bash
# Upload a PDF
curl -X POST http://localhost:8000/api/pdf/upload -F "file=@test.pdf"

# Get page image (latest version)
curl http://localhost:8000/api/pdf/{session_id}/page/1/image -o page1.png

# Get specific version
curl "http://localhost:8000/api/pdf/{session_id}/page/1/image?v=0" -o page1_original.png

# Get page text
curl http://localhost:8000/api/pdf/{session_id}/page/1/text

# Export edited PDF
curl -X POST http://localhost:8000/api/pdf/{session_id}/export -o edited.pdf

# Test Gemini integration
cd backend && .venv/bin/python -m tests.test_model_provider
```

## Dependencies

**Backend**: fastapi, uvicorn[standard], websockets, python-multipart, pdfplumber, pikepdf, Pillow, reportlab, httpx, python-dotenv, pydantic, pydantic-settings

**Frontend**: react, react-dom, lucide-react, typescript, vite, @vitejs/plugin-react, tailwindcss, postcss, autoprefixer

**System (Docker)**: poppler-utils
