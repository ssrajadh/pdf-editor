# PDF Editor

AI-powered PDF editor with a chat-based editing interface.

## Architecture

- **Frontend**: React 18 + Vite + TypeScript + Tailwind CSS
- **Backend**: FastAPI + Python 3.12

## Quick Start

### With Docker

```bash
cp .env.example .env
# Edit .env with your API keys
docker compose -f docker/docker-compose.yml up --build
```

Frontend: http://localhost:5173
Backend: http://localhost:8000
API docs: http://localhost:8000/docs

### Local Development

**Backend:**

```bash
cd backend
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000
```

**Frontend:**

```bash
cd frontend
npm install
npm run dev
```

## Environment Variables

| Variable | Description | Default |
|----------|-------------|---------|
| `GEMINI_API_KEY` | Google Gemini API key | - |
| `STORAGE_PATH` | File storage directory | `/data` |
| `MAX_FILE_SIZE_MB` | Max upload size in MB | `50` |
| `ALLOWED_ORIGINS` | CORS allowed origins | `http://localhost:5173` |
