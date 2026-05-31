# 217 转换工具

Streamlit app for uploading or pasting CNGF order numbers and calling the Dispatch API to update orders to 217.

## Run Locally

```bash
python3 -m pip install -r requirements.txt
UNIUNI_USER=your_dispatch_username UNIUNI_PASS=your_dispatch_password \
python3 -m streamlit run app_217.py
```

## Deploy

See [DEPLOY.md](DEPLOY.md).
