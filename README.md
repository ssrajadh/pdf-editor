# Nano PDF Studio

AI-powered PDF editor with a chat-based editing interface. Upload a PDF, describe changes in natural language, and watch the AI edit your pages in real time. Unlike traditional PDF editors, edits are made visually through Google Gemini's image generation — no need to wrestle with text boxes and layers.

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
┌─────────────────────────────────────────────────────────┐
│  Browser (React + Vite + Tailwind)                      │
│  ┌──────────┬──────────────┬──────────────┐             │
│  │Thumbnails│  PDF Viewer  │  Chat Panel  │             │
│  │          │  + Before/   │  + WebSocket │             │
│  │          │    After     │    progress  │             │
│  └──────────┴──────────────┴──────────────┘             │
└──────────────┬──────────────────────┬───────────────────┘
               │ REST API            │ WebSocket
┌──────────────▼──────────────────────▼───────────────────┐
│  FastAPI Backend                                        │
│  ┌──────────┬──────────────┬──────────────┐             │
│  │PDF Router│ Edit Router  │ Edit Engine  │             │
│  │(upload,  │ (WS, history,│ (pipeline,   │             │
│  │ render,  │  revert)     │  text layer) │             │
│  │ export)  │              │              │             │
│  └──────────┴──────┬───────┴──────┬───────┘             │
│                    │              │                      │
│  ┌─────────────────▼──┐  ┌───────▼──────────┐          │
│  │  Session Manager   │  │  Model Provider  │          │
│  │  (file storage)    │  │  (Gemini API)    │          │
│  └────────────────────┘  └──────────────────┘          │
└─────────────────────────────────────────────────────────┘
```

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
| `MODEL_PROVIDER` | `gemini` | AI provider selection |
| `MODEL_TIMEOUT_SECONDS` | `60` | API call timeout |
| `STORAGE_PATH` | `/data` (Docker), `./data` (local) | Session file storage |
| `MAX_FILE_SIZE_MB` | `50` | Max PDF upload size |
| `ALLOWED_ORIGINS` | `http://localhost:5173` | CORS origins (comma-separated) |

## Key Features

- **Chat-based editing** — describe changes in natural language
- **Real-time progress** — WebSocket streams edit stages as they happen
- **Before/After toggle** — compare original and edited versions side by side
- **Text layer preservation** — non-text edits keep the original searchable text layer intact
- **PDF export** — download the edited PDF with all changes merged in
- **Page versioning** — every edit is saved as a new version; revert to any previous state
- **Session cleanup** — background task removes sessions older than 24 hours

## License

MIT
