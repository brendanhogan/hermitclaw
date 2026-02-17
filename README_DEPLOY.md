# HermitClaw — Production Deployment

## Architecture

```
GitHub push to main
    → GitHub Actions builds Docker image
    → pushes to ghcr.io/tjlong84/hermitclaw:main
    → watchtower polls GHCR every 5 min
    → watchtower pulls new image, restarts container
    → change is live
```

Traffic path:
```
https://hermitclaw.rokospaperclip.com
    → Cloudflare Zero Trust Tunnel
    → cloudflared container (same compose stack)
    → hermitclaw container port 8000
```

Persistent data:
```
/media/serverfiles/hermitclaw/boxes/  (host)
    → /data/boxes                     (container, BOX_ROOT)
    → hermit_box/, coral_box/, ...    (each crab's files)
```

---

## First Deploy

### 1. Create host directories

```bash
ssh openmediavault
mkdir -p /media/serverfiles/hermitclaw/boxes
```

### 2. Clone the fork

```bash
cd /media/serverfiles
git clone https://github.com/tjlong84/hermitclaw hermitclaw-src
cd hermitclaw-src
```

### 3. Create .env

```bash
cp .env.example .env
nano .env   # fill in OPENAI_API_KEY, CLOUDFLARED_TUNNEL_TOKEN, GHCR_TOKEN
```

### 4. Create Cloudflare Tunnel (if new)

1. Go to [Cloudflare Zero Trust](https://one.dash.cloudflare.com) → Networks → Tunnels
2. Create tunnel → name it `hermitclaw`
3. Under **Install and run connector** copy the **token** value (the long `eyJ...` string)
4. Paste it as `CLOUDFLARED_TUNNEL_TOKEN` in `.env`
5. Under **Public Hostname** add:
   - Subdomain: `hermitclaw`
   - Domain: `rokospaperclip.com`
   - Service: `http://hermitclaw:8000`
   - (WebSocket support is on by default in Cloudflare tunnels)

### 5. Login to GHCR (pull the pre-built image)

```bash
echo $GHCR_TOKEN | docker login ghcr.io -u tjlong84 --password-stdin
```

### 6. Start the stack

```bash
cd /media/serverfiles/hermitclaw-src
docker compose up -d
```

### 7. Verify

```bash
# All 3 services running?
docker compose ps

# Health check passing?
docker exec hermitclaw python -c "import urllib.request; print(urllib.request.urlopen('http://localhost:8000/health').read())"

# UI reachable?
curl -s https://hermitclaw.rokospaperclip.com/health

# Logs
docker logs hermitclaw -f
docker logs hermitclaw-cloudflared -f
```

### 8. Create your first crab

No crabs exist on fresh deploy. Create one via the API or the UI:

```bash
curl -s -X POST http://localhost:8000/api/crabs \
  -H 'Content-Type: application/json' \
  -d '{"name": "Hermit"}'
```

Or just open `https://hermitclaw.rokospaperclip.com` — the UI has a create button.

---

## Auto-Update Flow

Every push to `main` in the fork:
1. GitHub Actions builds a new image tagged `:main` and `:<sha>`
2. Watchtower polls GHCR every 5 minutes
3. When it sees a new `:main`, it pulls and restarts `hermitclaw`
4. Crab boxes in `/media/serverfiles/hermitclaw/boxes/` persist unchanged

No server action needed.

---

## Rollback

Pin to a specific image SHA:

```bash
# List available tags
docker image ls ghcr.io/tjlong84/hermitclaw

# Edit docker-compose.yml image tag
# Change: image: ghcr.io/tjlong84/hermitclaw:main
# To:     image: ghcr.io/tjlong84/hermitclaw:<sha>

docker compose up -d hermitclaw

# Stop watchtower from auto-updating while pinned
docker compose stop watchtower
```

To resume auto-updates:
```bash
# Restore :main tag in docker-compose.yml
docker compose up -d
```

---

## Update Cloudflare Config

If you need to add or change hostnames:
1. Cloudflare Zero Trust → Tunnels → hermitclaw → Edit
2. Change Public Hostname entries
3. No container restart needed — cloudflared picks up changes live

---

## Notes

- **Crab boxes:** `/media/serverfiles/hermitclaw/boxes/<name>_box/` on host. Never deleted by watchtower restarts or image updates.
- **GHCR visibility:** The package is private by default. Make it public in GitHub → Packages → hermitclaw → Package settings → Change visibility, or keep it private and use GHCR_TOKEN auth everywhere.
- **WebSockets:** Cloudflare tunnels support WebSockets natively. No extra config needed.
- **Access policy:** Consider adding a Cloudflare Access application rule at `hermitclaw.rokospaperclip.com` to restrict access to your email/IP.
