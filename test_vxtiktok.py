import urllib.request

url = "https://vm.vxtiktok.com/ZMeabcd/" # Or some shortlink
# Actually, I'll just check if vxtiktok has a vm subdomain
try:
    req = urllib.request.Request(url, headers={'User-Agent': 'Discordbot/2.0'})
    with urllib.request.urlopen(req) as response:
        print(response.geturl())
except Exception as e:
    print(e)
