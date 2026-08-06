import datetime
try:
    datetime.datetime.fromisoformat(str(None))
except Exception as e:
    print(f"Type: {type(e).__name__}, Message: {e}")

try:
    datetime.datetime.fromisoformat(None)
except Exception as e:
    print(f"Type: {type(e).__name__}, Message: {e}")
