# 217 Streamlit Deployment

This app runs with Streamlit:

```bash
python3 -m streamlit run app_217.py
```

## Required environment variables

Set these in the deployment platform, not in source code:

```bash
UNIUNI_USER=your_dispatch_username
UNIUNI_PASS=your_dispatch_password
```

The local script can fall back to `auto_jump.py`, but cloud deployment must use the environment variables above because the local file path will not exist on the server.

## Streamlit Community Cloud

1. Push this folder to a private GitHub repository.
2. Go to Streamlit Community Cloud and create a new app.
3. Select:
   - Main file path: `app_217.py`
   - Python dependencies file: `requirements.txt`
4. Add secrets or environment variables:
   - `UNIUNI_USER`
   - `UNIUNI_PASS`
5. Deploy.

## Render

Use a Web Service with:

```bash
pip install -r requirements.txt
```

Start command:

```bash
python -m streamlit run app_217.py --server.port $PORT --server.address 0.0.0.0 --server.headless true
```

Add environment variables:

```bash
UNIUNI_USER=your_dispatch_username
UNIUNI_PASS=your_dispatch_password
```

## Local network sharing

For temporary use on the same Wi-Fi/network:

```bash
python3 -m streamlit run app_217.py --server.address 0.0.0.0 --server.port 8501
```

Then open:

```text
http://YOUR_MAC_IP:8501
```

Do not use this as a public deployment unless the machine and network are properly secured.
