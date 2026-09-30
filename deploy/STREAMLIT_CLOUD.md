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

## Optional: live answers with your Gemini key

Replace the secrets with the following (free tier; the app answers with real SQL). Anyone with
the URL can then use your quota, so consider making the app private (App settings → Sharing).

```toml
PUBLIC_DEMO = "0"
E5_ALLOW_DOWNLOAD = "1"
GOOGLE_API_KEY = "your-key"
GEMINI_MODEL = "gemini-3.8-flash"
GEMINI_FALLBACK_MODELS = "gemini-3.6-flash,gemini-3.5-flash,gemini-3.1-flash-lite"
```
