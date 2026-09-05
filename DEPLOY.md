# Deploying this project

Two things can be hosted, and they serve different purposes.

## 1. The results page (static, free, no keys)

`docs/site/index.html` is a self-contained page with the measured results. It needs no
server and no API key.

**GitHub Pages** — in the repo's *Settings → Pages*, set source to `main` and folder to
`/docs`, then open `https://<user>.github.io/<repo>/site/`. That is the fastest link to
put in a submission.

## 2. The interactive app (Streamlit)

`app.py` is the full explorer: upload a chapter, watch the pipeline run, browse questions,
evidence and validation.

**Streamlit Community Cloud** (free):

1. Push this repo to GitHub.
2. At [share.streamlit.io](https://share.streamlit.io) → *New app*, pick the repo and set
   the main file to `app.py`.
3. Deploy. It installs `requirements.txt` automatically.

**Demo Mode needs no key.** The processed runs in `outputs/` are committed on purpose, so
*Load AGP Demo Sample* works immediately on a fresh deploy — a reviewer can explore both
chapters, and download the questions-and-answers PDF, without any credentials and at zero
cost.

### To also allow live uploads

Only needed if a reviewer should process their own file. In the Streamlit Cloud app menu,
*Settings → Secrets*, add:

```toml
GEMINI_API_KEY = "your-key"
```

Locally the same value goes in a `.env` file. Never commit either.

### Notes

- `docling` is commented out in `requirements.txt` because it pulls in torch and would make
  a hosted build slow and large. Without it the router falls back to the vision parser, so
  the app still runs; uncomment it to enable the zero-API-cost born-digital path.
- Page rasters under `outputs/*/preprocessed/` are gitignored — they are large
  intermediates and nothing in the UI reads them.
- On Windows without Developer Mode, model downloads need `HF_HUB_DISABLE_SYMLINKS=1`,
  otherwise Hugging Face fails with `WinError 1314`. Not an issue on Streamlit Cloud.

## Verify before pushing

```bash
python -m pytest tests/ -q
```

56 tests, no API key required.
