# *search — Starsearch

> Your personal search engine. Search through all your notes, documents, and files using plain English.

Ask it things like:
- *"What did I write about the French Revolution?"*
- *"Find my notes on how to fix the wifi issue"*
- *"What were the key points from my biology lecture?"*

Your documents stay on your computer. Only the text is sent to the server briefly for embedding — the server never stores your raw documents, only mathematical fingerprints derived from them.

---

## What You Need (One-Time Setup)

### On your computer
- [ ] **Python 3.10 or newer** — [download here](https://www.python.org/downloads/) — tick *"Add Python to PATH"* during install
- [ ] **Tesseract** *(optional — only for searching photos/scans)* — [download here](https://github.com/UB-Mannheim/tesseract/wiki)

No Ollama needed. Embedding runs on the server.

### Provided by the server host
- API URL (e.g. `https://search.example.com` or `http://100.x.x.x:6400`)
- Your API key (a long random string)

---

## Setup (Do This Once)

### Step 1 — Download the client

Get the `client/` folder from your server host and save it somewhere permanent (e.g. `C:\starsearch\`).

### Step 2 — Run setup

Open a terminal and run:

```
python setup.py
```

It will:
1. Check your Python version
2. Install required packages
3. Ask for your API URL and key
4. Create your documents folder
5. Create a "Start Starsearch" shortcut

---

## Adding Your Documents

Drop files into `~/starsearch-docs/` (created during setup).

| File type | Examples |
|---|---|
| PDF | Lecture slides, textbooks, scanned notes |
| Word (.docx) | Essays, notes, reports |
| Text / Markdown | Notes, journal entries |
| Images | Photos of handwritten notes (requires Tesseract) |

Subfolders are supported — organise however you like.

---

## Running the Watcher

Double-click **Start Starsearch.bat** (Windows) or run `./start_starsearch.sh` (Mac/Linux).

The watcher will:
1. Index all existing files in your documents folder (may take a few minutes)
2. Watch for new files and index them automatically (~30 seconds after drop)

**The watcher only needs to run when you're adding new files.** Searches work even when it's not running — your documents are already indexed on the server.

---

## Searching

Open a browser and go to the URL your server host gave you.

1. Paste your API key and click **Save key** (stored in your browser — one time only)
2. Type your question in plain English
3. Results appear instantly

Toggle **AI answer** for a synthesised response from the top results (slower — uses the server GPU).

### What it's good at

| ✓ Works well | ✗ Doesn't work |
|---|---|
| Finding facts from your notes | Files not yet indexed |
| Summarising a topic across files | Exact word-for-word quotes |
| Cross-referencing documents | Very short / low-quality scans |

---

## Privacy

- Your raw documents stay on your computer
- Text is sent to the server **only during indexing** to generate embeddings, then discarded from memory
- What's stored on the server: mathematical vector representations of your text chunks, and the chunk text itself (needed to show you results)
- Your data is isolated by your API key — other users cannot access your index

> If you need zero-text-on-server privacy, run your own Ollama locally and embed before sending (advanced).

---

## Troubleshooting

**"No config found"**
→ Run `setup.py` first.

**"Upload failed"**
→ Server is down or unreachable. Check the API URL in `~/.starsearch/config.json`. Contact your server host.

**"No results"**
→ Make sure the watcher has run at least once and shows "Scan complete" in the log.

**Images not indexed**
→ Tesseract isn't installed. [Download here](https://github.com/UB-Mannheim/tesseract/wiki).

---

## Server Setup (Host Only)

### Requirements
- Docker and Docker Compose
- [Ollama](https://ollama.com) running somewhere reachable (locally or on another machine)
- The embedding model pulled: `ollama pull nomic-embed-text`
- *(Optional)* A language model for AI answers: `ollama pull qwen3:32b` (or any LLM you prefer)

### Deploy

```bash
git clone https://github.com/aabdelrahim/starsearch
cd starsearch
docker compose up -d --build
```

Starts:
- **starsearch-api** on port `6400` — the only port to expose
- **qdrant** — internal only, not accessible from outside Docker

### Environment variables (`docker-compose.yml` or `.env`)

| Variable | Default | Notes |
|---|---|---|
| `OLLAMA_URL` | `http://host-gateway:11434` | Ollama on the Docker host (or any reachable URL) |
| `EMBED_MODEL` | `nomic-embed-text` | Must be pulled on your Ollama instance |
| `LLM_MODEL` | `qwen3:32b` | Used for AI answer synthesis — any Ollama model works |
| `SIGNUP_SECRET` | *(unset)* | Set this — without it `/signup` is open to anyone |

### Adding a user

With `SIGNUP_SECRET` set, generate a key:

```bash
curl -X POST https://your-url/signup \
  -H "Authorization: Bearer YOUR_SIGNUP_SECRET"
```

Send the returned `key` to the user. That's all — no other setup needed on your end.

### Expose via Cloudflare Tunnel

```bash
cloudflared tunnel --url http://localhost:6400
```

Or configure a named tunnel in your Cloudflare dashboard pointing to `localhost:6400`.

### Data

| Path | Contents |
|---|---|
| `./qdrant_storage/` | All user indexes — back this up |

### Stop / restart

```bash
docker compose restart api     # restart API only
docker compose restart qdrant  # restart Qdrant (data preserved)
```

Never run `docker compose down` unless you intend to stop everything.
