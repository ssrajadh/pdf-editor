# PDF Editor - Project Context

AI-powered PDF editor with a chat-based editing interface. Users upload PDFs, view rendered pages, and submit natural-language edit instructions that are processed by Google Gemini's image generation API.

## Architecture

```
frontend/ (React 19 + Vite 5 + TypeScript + Tailwind CSS 3)
   ↕ REST API + WebSocket
backend/  (FastAPI + Python 3.12)
   ↕ Gemini API (image editing)
   ↕ poppler (pdftoppm for rendering)
   ↕ pdfplumber (text extraction)
```

## Directory Structure

```
pdf-editor/
├── backend/
│   ├── app/
│   │   ├── main.py              # FastAPI app, CORS, /health endpoint
│   │   ├── config.py            # Pydantic Settings from .env
│   │   ├── routers/
│   │   │   ├── pdf.py           # PDF upload, page image, text extraction, session info
│   │   │   └── edit.py          # Edit submission + WebSocket (stub)
│   │   ├── services/
│   │   │   ├── pdf_service.py   # pdftoppm rendering, pdfplumber text extraction
│   │   │   ├── edit_engine.py   # Edit orchestration (stub)
│   │   │   └── model_provider.py # Abstract ModelProvider, GeminiProvider, ProviderFactory
│   │   ├── models/
│   │   │   └── schemas.py       # Pydantic models (UploadResponse, TextBlock, etc.)
│   │   └── storage/
│   │       └── session.py       # SessionManager: create/read/update/delete sessions
│   ├── tests/
│   │   └── test_model_provider.py  # Standalone Gemini API integration test
│   ├── requirements.txt
│   └── pyproject.toml
├── frontend/
│   ├── src/
│   │   ├── App.tsx              # Root component (currently just heading)
│   │   ├── main.tsx             # React entry point
│   │   ├── components/
│   │   │   ├── PdfViewer.tsx    # PDF page display (stub)
│   │   │   ├── PageThumbnails.tsx # Sidebar thumbnails (stub)
│   │   │   ├── ChatPanel.tsx    # Edit instruction chat UI (stub)
│   │   │   └── BeforeAfterToggle.tsx # Version comparison toggle (stub)
│   │   ├── hooks/
│   │   │   ├── useWebSocket.ts  # WebSocket connection hook
│   │   │   └── usePdfSession.ts # PDF session state hook
│   │   ├── services/
│   │   │   └── api.ts           # REST API client (uploadPdf, getPages, submitEdit)
│   │   └── types/
│   │       └── index.ts         # TypeScript interfaces matching backend schemas
│   ├── index.html
│   ├── vite.config.ts           # Vite config with /api proxy to backend
│   ├── tailwind.config.js
│   ├── postcss.config.js
│   └── tsconfig.json
├── docker/
│   ├── Dockerfile.backend       # Python 3.12-slim + poppler-utils + tesseract-ocr
│   ├── Dockerfile.frontend      # Node 20-slim + Vite dev server
│   └── docker-compose.yml       # backend + frontend services, shared pdf_data volume
├── .env                         # Actual config (not committed)
├── .env.example                 # Template
├── .gitignore
└── README.md
```

## Environment Variables

| Variable | Default | Purpose |
|----------|---------|---------|
| GEMINI_API_KEY | "" | Google Gemini API key |
| GEMINI_MODEL | gemini-2.0-flash-exp | Gemini model for image editing |
| MODEL_PROVIDER | gemini | AI provider selection |
| MODEL_TIMEOUT_SECONDS | 60 | API call timeout |
| STORAGE_PATH | /data (Docker), ./data (local) | Session file storage |
| MAX_FILE_SIZE_MB | 50 | Max PDF upload size |
| ALLOWED_ORIGINS | http://localhost:5173 | CORS origins (comma-separated) |

Config loads from `../.env` then `.env` (so it works from both `backend/` and project root).

## API Endpoints

| Method | Path | Status | Purpose |
|--------|------|--------|---------|
| GET | /health | Done | Health check |
| POST | /api/pdf/upload | Done | Upload PDF, create session, render all pages |
| GET | /api/pdf/{session_id}/page/{page_num}/image | Done | Serve rendered page PNG (latest version) |
| GET | /api/pdf/{session_id}/page/{page_num}/text | Done | Extract text + character block positions |
| GET | /api/pdf/{session_id}/info | Done | Session metadata (page count, versions) |
| POST | /api/edit/submit | Stub | Submit edit instruction |
| WS | /api/edit/ws/{session_id} | Stub | Real-time edit progress |

## Session Storage Format

```
{storage_path}/{session_id}/
├── original.pdf
├── metadata.json          # {session_id, filename, page_count, created_at, current_page_versions}
├── pages/
│   ├── page_1_v0.png      # v0 = original render, v1+ = edits
│   ├── page_2_v0.png
│   └── ...
└── edits/                 # Reserved for edit history
```

## Key Design Decisions

- **pdftoppm (poppler) for rendering** instead of Python-native: faster, higher quality at 200 DPI
- **pdfplumber for text extraction**: provides character-level position metadata (x0, y0, x1, y1, font, size)
- **Page versioning**: images are `page_{num}_v{version}.png`; `get_page_image_path` finds latest version via glob
- **ModelProvider abstraction**: ABC with `edit_image(PIL.Image, prompt, history) -> PIL.Image`; ProviderFactory for multi-model support
- **GeminiProvider**: sends image as base64 PNG inline_data + text prompt; responseModalities: ["TEXT", "IMAGE"]; retry with exponential backoff on 429; 60s timeout
- **Pydantic Settings**: `allowed_origins` stored as `str`, split to list via `origins_list` property (avoids pydantic-settings JSON parsing issue with env vars)
- **Config env_file**: `("../.env", ".env")` tuple so it works whether CWD is `backend/` or project root

## Implementation Status

### Done
- Full project scaffolding (backend, frontend, Docker)
- PDF upload pipeline: upload → validate → create session → render all pages → return metadata
- Page image serving with cache headers
- Text extraction with character-level positional data
- Session management with metadata JSON
- Gemini AI provider with retry/backoff/timeout/error handling
- Provider factory pattern for future multi-model support
- Docker Compose with health checks
- Frontend scaffolding with hooks, services, types

### TODO
- Edit engine: wire Gemini provider into edit submission endpoint
- WebSocket progress streaming during edits
- Frontend UI: compose components into App layout
- PdfViewer: render page images from API
- ChatPanel: message input/history, submit edit instructions
- PageThumbnails: fetch/display thumbnails, page selection
- BeforeAfterToggle: toggle between page versions
- Edit history tracking in edits/ directory

## Running Locally

```bash
# Backend
cd backend && source .venv/bin/activate
uvicorn app.main:app --reload --port 8000

# Frontend
cd frontend && npm run dev

# Docker (use v2 plugin, NOT docker-compose v1)
docker compose -f docker/docker-compose.yml up --build
```

## Testing

```bash
# Upload a PDF
curl -X POST http://localhost:8000/api/pdf/upload -F "file=@test.pdf"

# Get page image
curl http://localhost:8000/api/pdf/{session_id}/page/1/image -o page1.png

# Get page text
curl http://localhost:8000/api/pdf/{session_id}/page/1/text

# Test Gemini integration
cd backend && .venv/bin/python -m tests.test_model_provider
```

## Dependencies

**Backend**: fastapi, uvicorn[standard], websockets, python-multipart, pdfplumber, pikepdf, Pillow, reportlab, pytesseract, httpx, python-dotenv, pydantic, pydantic-settings

**Frontend**: react, react-dom, react-pdf, lucide-react, typescript, vite, @vitejs/plugin-react, tailwindcss, postcss, autoprefixer

**System (Docker)**: poppler-utils, tesseract-ocr
