# Q&A assistant (optional)

An optional natural-language assistant answers questions about saved results and about the project documentation. It is **off by default**. The application works exactly the same without it.

Test-first history: skeleton `383ea15`; frozen tests `6db6f82`.

## What it answers

| Question type | Example | Where the answer comes from |
|---|---|---|
| Saved results | "In the second scenario, how much shorter is the sunlight?" | `get_analysis`, `compare_analyses` and `get_recommendation`, which read results the backend already computed |
| Methods and limitations | "How is the terrain horizon computed?" | `search_docs`: BM25 keyword search over `docs/*.md` |
| Site catalog | "Which preset sites exist?" | `list_presets` (`shared/presets.json`) |

Personas: P1/P2 ask about numbers and trade-offs, P3 asks for classroom explanations, P4 asks plain-language questions. The assistant answers in the language of the question.

## Design

- **No physics in the model.** Every number in an answer must come from a tool result in the same conversation. The tools are read-only: they never start analyses, write state or call other services.
- **Retrieval without a vector store.** The documentation is about 37 files (~90k tokens). `app/doc_search.py` splits files by `#`/`##` headings and ranks sections with BM25 (k1 = 1.5, b = 0.75). Only the top 4 sections (each capped at 4,000 characters) are sent. No embeddings, extra service or second provider are needed. Results are deterministic.
- **Sources.** Each answer lists the documentation sections (`docs/<file>.md#<heading>`) and API results (`/analyses/<id>/result`, `/comparisons`, `/recommendations/<id>`, `/presets`) that its tools used.
- **Bounded cost.** Default model `claude-haiku-5-5`, effort `low`, at most 2,000 output tokens, at most 4 tool rounds, tool results capped at 12,000 characters, system prompt cached. A typical question is roughly 8–10k input tokens.
- **Failure behaviour.** A disabled assistant or missing key returns 503 with the reason. An unreachable API, rejected key, rate limit or server error also returns 503. A refusal, truncated output, exhausted tool rounds or a rejected request (for example an exhausted credit balance) returns 502 with the reason. Other endpoints are unaffected.

## External call boundary

`docs/technologies.md` states that the backend never downloads data from external sources. The assistant does not download data. It sends the question, the system prompt and tool results to the Claude API and receives text. This is the only outbound call from the backend, it is opt-in, and it needs team agreement as a stack change.

Data sent to the provider: the question, earlier turns of the same chat, documentation excerpts and summaries of saved results. Do not enable the assistant for data that must not leave the machine.

## Configuration

| Variable | Default | Meaning |
|---|---|---|
| `ASSISTANT_ENABLED` | `false` | `true/yes/1` enables it, `false/no/0`/empty disables it. Any other value stops the backend at startup. |
| `ASSISTANT_MODEL` | `claude-haiku-5-5` | Claude model ID |
| `ANTHROPIC_API_KEY` | empty | Provider key. **Never commit it.** Put it in your own `.env` (ignored by Git and Docker builds) or a secret store, and pass it to Compose as an environment variable. |
| `DOCS_DIR` | `/workspace/docs` in Compose | Documentation folder (mounted read-only) |

Restart the backend container after changing these values.

## API

`GET /assistant/status`:

```json
{"enabled": true, "model": "claude-haiku-5-5", "reason": null, "doc_sections": 412}
```

`POST /assistant/ask`:

```json
{
  "question": "How does the 48-hour delay change sunlight?",
  "analysis_ids": ["<analysis A>", "<analysis B>"],
  "recommendation_id": null,
  "history": []
}
```

`history` is optional. It alternates `user`/`assistant` turns, starts with `user`, ends with `assistant` and holds at most 10 turns. The response contains `answer`, `sources`, `model`, `tool_calls`, `input_tokens` and `output_tokens`.

## Verification

- The test suite never calls the provider. A scripted fake client covers the tool loop, error mapping and routes.
- Live checks with a real key are manual and spend credit. Run them only with the key holder's approval.

## Interface

A "Mission Control · Ask the Navigator" launcher sits in the lower right corner of every page (`frontend/src/components/AssistantPanel.tsx`). It opens a console drawer that:

- shows the link status (model and indexed documentation sections, or why the assistant is offline),
- links the open analysis (and the comparison base, when one is open) and the last recommendation, so questions about "this site" use those results,
- offers context-aware starter questions, keeps the conversation and sends completed turns as `history`,
- lists each answer's sources as `DOC`/`API` chips and the tokens it used.

Strings are in English and Turkish (`assistant.*` in `frontend/src/i18n`). Esc or the scrim closes the drawer. The panel holds no analysis logic. Per the team rule, it was verified with `npm run build` only.

## Next steps

- Tools for new modules (for example quantum/hybrid results) as their APIs settle. New documentation is searchable as soon as it is in `docs/`.
