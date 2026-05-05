import os

app_id = os.environ.get("RAKUTEN_APP_ID")

if app_id:
    print(f"RAKUTEN_APP_ID: {app_id}")
else:
    print("ERROR: RAKUTEN_APP_ID が設定されていません")
