# BakaBaka

Latvian bachelor's thesis assistant — a local web application for topic planning, supervisor workflow, and draft checking.

**Demo prototype. Synthetic data. Not connected to any university.**

---

## Requirements

- Docker with Compose (Docker Desktop or Docker Engine ≥ 24)
- Internet access for the initial image build
- OpenAI API key for AI features (topic generation, document advice). All other features work without one.

---

## Setup

```bash
cd starter
cp .env.example .env
```

Open `.env` and set your key:
```
OPENAI_API_KEY=sk-...
```

Build and run:
```bash
docker compose up --build
```

First build takes ~2 minutes. Open **http://localhost:8000** once the health check passes.

---

## Demo accounts

All accounts use password **`BakaDemo-2026!`**

| Username | Role | State |
|---|---|---|
| **anna** | Student | Draft topic, 3 saved sources, supervisor accepted |
| **janis** | Student | Separate empty project |
| **ilze** | Supervisor | Accepted on Anna's project |
| martins, laura, eva, andris | Supervisor | Available |

Use a separate private window to run student and supervisor simultaneously.

---

## Test documents

Upload these from `fixtures/documents/` to test the document workflow:

- `draft_v1.docx` — has structural and formatting issues
- `draft_v2.docx` — passes all rules
- `draft_v1.pdf` — formatting check shows "not assessed" (PDF)

---

## Reset demo state

```bash
docker compose exec web python manage.py demo_reset --confirm
docker compose exec web python init_demo.py
```

Full wipe:
```bash
docker compose down -v
docker compose up
```

---

## Stop

```bash
docker compose stop      # pause, data preserved
docker compose down      # remove containers, data preserved
docker compose down -v   # remove everything
```

---

## Without an OpenAI key

Topic generation and document advice will show "AI nav konfigurēts". All other flows — source search, supervisor assignment, document upload, rule checks, comments — work fully.
