# Deploy on Streamlit Community Cloud (free)

Public demo: retrieval only, saved Claude Haiku answers, **no language-model calls, no API keys**.

1. Commit and push this branch to `github.com/jasensteve10/ai_project`.
2. Sign in at https://share.streamlit.io with GitHub and authorize access to the repository.
3. **Create app** → *Deploy a public app from GitHub* → Repository `jasensteve10/ai_project`,
   Branch `<your branch>`, Main file path `app/app.py`, optional custom subdomain.
4. **Advanced settings** → Python version **3.14**; Secrets:

   ```toml
   PUBLIC_DEMO = "1"
   E5_ALLOW_DOWNLOAD = "1"
   HF_HUB_DISABLE_TELEMETRY = "1"
   ```
5. **Deploy**. First build: ~5–10 min (dependencies from `app/requirements.txt`, CPU-only torch).
   The first E5/hybrid query downloads the pinned E5 model (~470 MB), then it is cached.

Files used by Community Cloud: `app/app.py`, `app/requirements.txt` (read before the root
requirements), `.streamlit/config.toml`. Apps sleep after 12 h without traffic; memory limit 2.7 GB
(measured peak ≈1.9 GB with E5 loaded).

## Live answers with your Gemini key (protected by an access code)

In **Manage app → Settings → Secrets**, replace the secrets with the following and save (the app
restarts). Visitors without the code still get the examples, saved answers and retrieval context;
only people with the code trigger Gemini calls on your free quota.

```toml
PUBLIC_DEMO = "0"
APP_ACCESS_CODE = "choose-a-code"
E5_ALLOW_DOWNLOAD = "1"
GOOGLE_API_KEY = "your-key"
GEMINI_MODEL = "gemini-3.8-flash"
GEMINI_FALLBACK_MODELS = "gemini-3.6-flash,gemini-3.5-flash,gemini-3.1-flash-lite"
```

`GEMINI_FALLBACK_MODELS` is optional: when it is absent the app uses gemini-3.6-flash,
gemini-3.5-flash and gemini-3.1-flash-lite after `GEMINI_MODEL`; an empty value disables the chain.

### Optional: Claude as last resort (billed)

Used only when every Gemini model fails. Add to the secrets above:

```toml
LLM_FALLBACK = "claude"
ANTHROPIC_API_KEY = "your-anthropic-key"
ANTHROPIC_MODEL = "claude-haiku-4-5"
ANTHROPIC_WORKSPACE_ID = "your-workspace-id"
CLAUDE_FALLBACK_MAX_CALLS = "20"
```
Each Claude answer costs about USD 0.003 with Haiku; the cap stops paid calls per app process and
the app shows a warning whenever Claude answered.
