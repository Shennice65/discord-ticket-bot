import urllib.request

urls_to_test = [
    "https://www.tiktxk.com/@poopdealer_lol/video/7331589578144025899",
    "https://www.tnktok.com/@poopdealer_lol/video/7331589578144025899",
    "https://www.tikt0k.com/@poopdealer_lol/video/7331589578144025899"
]

for url in urls_to_test:
    try:
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) Discordbot/2.0'})
        with urllib.request.urlopen(req) as response:
            html = response.read().decode('utf-8')
            if 'og:video' in html:
                print(f"{url} works and has og:video!")
            else:
                print(f"{url} works but NO og:video")
    except Exception as e:
        print(f"{url} failed: {e}")
