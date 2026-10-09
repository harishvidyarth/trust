# Run everything with one command

From the project folder:

    .venv/bin/python scripts/run_all.py

This starts the API on `127.0.0.1:8000` and serves the web console from `web/` on `127.0.0.1:8081`.
Open the console URL it prints (`http://localhost:8081/console/`). Press Ctrl+C to stop both.
It refuses to start if either port is already in use. Use `--api-port` and `--web-port` to pick other ports.

## Accounts and sign-in

- Accounts are saved in `.state/users.json` (private file, not in git) unless you set `FIREWALL_USERS_FILE` yourself.
  The audit trail goes to `.state/audit.jsonl` unless you set `FIREWALL_AUDIT_FILE`.
- Without Redis, sign-in sessions live only in memory. Restarting the API signs everyone out.
  Accounts stay, but people must log in again.
- With Redis (`FIREWALL_REDIS_URL`), sessions are kept in Redis, so a restart keeps people signed in.
  If Redis is down, the app quietly falls back to memory.

## Settings (environment variables)

Set these before running the command. All are optional.

- `FIREWALL_REQUIRE_AUTH` - set to `1` to require login. Without it (and without an admin user) the app is open, for demos only.
- `FIREWALL_ADMIN_USER` and `FIREWALL_ADMIN_PASSWORD` - the first admin account. You choose both. Setting them also turns login on.
- `FIREWALL_REDIS_URL` - for example `redis://127.0.0.1:6379/0`. Turns on Redis for sessions, login limits, audit log, the application store, intake records, caches and the delivery queue.
- `FIREWALL_ENRICH` - set to `1` to allow public-record lookups (only for borderline scores, and only for candidates who agreed).
- `FIREWALL_LLM` - set to `1` to let a local model reword explanations. It never decides a route.
- `FIREWALL_SEMANTIC_DUP` - set to `1` to turn on the meaning-based duplicate check.
- `FIREWALL_ROUTES` - JSON (or a path to a JSON file) saying where each decision is sent.
- `FIREWALL_LEVER_WEBHOOK_SECRET` - the shared secret used to check Lever webhooks.

The runner never prints passwords or secrets.

## Face match models (optional)

The optional photo match in the identity check needs two small model files and the OpenCV package. Nothing is downloaded by the app.

- Put `yunet.onnx` and `sface.onnx` in `.state/face/` or set `FIREWALL_FACE_MODEL_DIR` to another folder that holds them.
- `yunet.onnx` is the YuNet face finder from the OpenCV Zoo. Its licence is MIT.
- `sface.onnx` is the SFace face recognition model from the OpenCV Zoo. Its licence is Apache 2.0.
- Install the packages with `pip install -r requirements.txt`. This adds `opencv-python-headless` and `pillow`.
- If a file or a package is missing the photo step turns itself off and the rest of the identity check works as before.
- Restart the API after adding the files so that it picks them up.
