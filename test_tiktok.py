import urllib.request
import urllib.parse
import json

url = "https://www.tiktok.com/@adoresresent/video/7675456615260228877?is_from_webapp=1&sender_device=pc"
# Try removing query params
url = url.split('?')[0]

api_url = "https://www.tikwm.com/api/"
data = urllib.parse.urlencode({"url": url, "hd": 1}).encode('utf-8')
req = urllib.request.Request(api_url, data=data, headers={'User-Agent': 'Mozilla/5.0'})
try:
    with urllib.request.urlopen(req) as response:
        res_data = json.loads(response.read().decode('utf-8'))
        print(json.dumps(res_data, indent=2))
except Exception as e:
    print("Error:", e)
