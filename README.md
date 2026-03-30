# Multifamily Deal Screener

First-cut screening tool for multifamily acquisitions. Upload an OM, rent roll, and/or T-12 and get an instant investment memo with trailing NOI analysis, expense normalization, replacement cost math, Bear/Base/Bull scenarios, three sensitivity tables, and a downloadable PDF memo.

## Setup

### 1. Install dependencies
```bash
pip install -r requirements.txt
```

### 2. Add your API key
Create `.streamlit/secrets.toml` and add:
```toml
ANTHROPIC_API_KEY = "sk-ant-your-key-here"
```

### 3. Run
```bash
streamlit run app.py
```

## Deploying to Streamlit Cloud

1. Push this repo to GitHub (make sure `secrets.toml` is in `.gitignore`)
2. Go to [share.streamlit.io](https://share.streamlit.io) and connect your repo
3. Add `ANTHROPIC_API_KEY` under **Settings → Secrets** in the Streamlit Cloud dashboard
