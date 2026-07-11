# AI Event Refiner

The refiner is an optional second pair of eyes for the counter. After each
line-crossing event is counted, the event's snapshot — **cropped to the
item's bounding box** (plus ~15 % margin) — is sent to a vision LLM which:

- verifies or corrects the event's **category** (drink / main / dessert …),
- optionally names the exact **menu item** ("Spritzer", "Wiener Schnitzel"),
- flags snapshots that show no food/drink item at all (`is_item=false`).

No model training is involved: the LLM sees the restaurant's own category
keys and menu names in the prompt and answers with a tiny JSON verdict.

What the refiner **never** does:

- delete or suppress a count — even `is_item=false` only annotates the event,
- touch `raw_class_name` (the detector's original output stays auditable).

Every refined event gets `refined=true` plus a short `refiner_note`
(backend/model, original category, verdict, confidence) shown as a ✨ badge
tooltip on the Events page. When the LLM's category differs from the
detector's, `category_id` is corrected in the database and a `refined` message
is pushed over `/ws/live`, so live counters move the count to the right
category without a reload. A menu-item verdict is matched
**case-insensitively** against the restaurant's menu and sets `menu_item_id`
(Phase 2 stats pick it up automatically).

## Flow

```
CameraPipeline ── bus["crossing"] ──▶ RefinerEngine (1 worker thread, rate-limited)
                                        │ 1. skip: provider off / confidence ≥ threshold
                                        │ 2. load snapshot, crop to bbox (+15 %), JPEG
                                        │ 3. LLM call (categories + menu in the prompt)
                                        │ 4. UPDATE events: refined, refiner_note,
                                        │    category_id / menu_item_id (when matched)
                                        └─▶ bus["refiner_correction"] ──▶ /ws/live {type: "refined"}
```

LLM failures (server down, bad key, unparseable reply) are **counted and
logged, never raised** — the counting pipeline does not depend on the
refiner. Status (provider, model, refined/failed/skipped counts, queue size,
last error) is visible on the System page and in `GET /api/system/health`.

## Configuration

The preferred way is the **dashboard**: the System page's "KI-Prüfer –
Einstellungen" card edits provider, model, base URL, API key, confidence
threshold, menu matching and the rate limit, offers a "Test connection"
probe, and applies changes **immediately** (persisted in the `app_settings`
table — no restart, no file edits).

Precedence: a dashboard-saved row **overrides** the `config.yaml` values
below entirely; without one, `config.yaml` (or `TOSKANA_REFINER_*`
environment variables) applies. `ANTHROPIC_API_KEY` in the environment
stays the final fallback for the API key either way.

```yaml
refiner_provider: off            # off | anthropic | openai_compatible
refiner_model: claude-haiku-4-5
refiner_base_url: http://localhost:11434/v1   # openai_compatible only
# refiner_api_key: sk-ant-...                 # or env ANTHROPIC_API_KEY
refiner_only_below_confidence: 1.0
refiner_match_menu_items: true
refiner_max_per_minute: 30
```

## Cost

With the Anthropic backend each event costs **fractions of a cent**: a
cropped item image plus a short prompt is roughly 1–2k input tokens and the
JSON reply ~50 output tokens — on Claude Haiku 4.5 that is well under half a
cent (US-$) per event; a busy 1 000-event day lands in the low single-digit
dollars. Two levers cut cost further:

- `refiner_only_below_confidence: 0.65` — only double-check detections the
  detector itself was unsure about (typically a small fraction of events).
- `refiner_max_per_minute` — hard rate cap; excess events wait in a small
  queue and are skipped when it overflows.

Local providers (Ollama, LM Studio) cost nothing per call.

## Provider setup

### Anthropic (Claude API)

1. Create an API key at <https://platform.claude.com/> (Console → API keys).
2. Provide the key — either in the environment (recommended, keeps it out of
   the config file):

   ```bash
   export ANTHROPIC_API_KEY=sk-ant-...
   ```

   or as `refiner_api_key` in `config.yaml`.
3. Enable the refiner:

   ```yaml
   refiner_provider: anthropic
   refiner_model: claude-haiku-4-5   # fast + cheap; any vision-capable Claude model works
   ```

### Ollama (local, free)

1. Install Ollama: <https://ollama.com/download>.
2. Pull a vision model:

   ```bash
   ollama pull qwen2.5-vl
   ```

3. Config snippet:

   ```yaml
   refiner_provider: openai_compatible
   refiner_base_url: http://localhost:11434/v1
   refiner_model: qwen2.5-vl
   ```

### LM Studio (local, free)

1. Install LM Studio and download a vision model (e.g. a Qwen2.5-VL build).
2. Enable the local server: **Developer → Start Server** (default port 1234)
   and note the model id it serves.
3. Config snippet:

   ```yaml
   refiner_provider: openai_compatible
   refiner_base_url: http://localhost:1234/v1
   refiner_model: <model id shown by LM Studio>
   ```

Any other OpenAI-compatible endpoint (vLLM, llama.cpp server, …) works the
same way; set `refiner_api_key` if the server requires a bearer token.

## Privacy

- **Cloud provider (Anthropic):** only the snapshot **cropped to the item's
  bounding box** is sent — not the full frame — together with the category
  keys/names and (optionally) menu item names. Staff or guests visible in the
  wider frame are excluded by the crop in the normal case; note the crop is
  taken from the annotated audit snapshot, so the drawn bbox/label may appear
  in it. Set `refiner_match_menu_items: false` if the menu itself is
  sensitive. Nothing is sent when `refiner_provider: off` (the default).
- **Local providers (Ollama, LM Studio):** everything stays on-site; no data
  leaves the machine, in line with Toskana's local-first design (see
  [privacy.md](privacy.md)).
- Snapshots remain subject to the normal retention job
  (`snapshot_retention_days`); the refiner keeps no copies.

## Troubleshooting

| Symptom | Check |
|---|---|
| System page shows provider `off` although configured | Typo in `refiner_provider` (must be `off`, `anthropic` or `openai_compatible`); config validation errors abort startup — check the logs |
| `anthropic` configured but refiner disabled | No API key found: set `refiner_api_key` or `ANTHROPIC_API_KEY` (a warning is logged at startup) |
| Failures counter climbing, last error "HTTP 401" | Wrong/expired Anthropic API key; for a local server, LM Studio's "Require API key" is on — turn it off in Server Settings, or paste its key into the dashboard's API-key field |
| Failures counter climbing, last error "unreachable" | Local server not running / wrong `refiner_base_url` (Ollama `:11434`, LM Studio `:1234`, both with `/v1`) |
| Last error "HTTP 404" on openai_compatible | `refiner_model` doesn't match a model the server has loaded (`ollama list`, LM Studio server page — copy the exact "API Model Identifier") |
| Last error "reply content is empty" | A reasoning model (e.g. Gemma via LM Studio) spent its whole token budget "thinking"; the refiner already asks for 2048 tokens and reads `reasoning_content`, so if it persists, disable the model's reasoning/thinking mode or pick a plain vision model |
| Last error "reply is not JSON" | The model ignored the JSON instruction — use a stronger vision model (small non-VL models can't read images) |
| Events stay unrefined, skipped counter climbing | `refiner_only_below_confidence` below the detector's typical confidence, or events have no snapshot (`snapshots_dir` misconfigured) |
| Queue size keeps growing | `refiner_max_per_minute` too low for the event rate — raise it or lower the confidence threshold so fewer events qualify |
| Category never corrected despite verdicts | The LLM must answer with one of the restaurant's category **keys**; check the note on the event (✨ tooltip) — `verdict=... (unknown)` means the key didn't match |
