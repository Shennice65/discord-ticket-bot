local StatTracker = require(script.Parent.StatTracker)
local DiscordWebhook = require(script.Parent.DiscordWebhook)

-- ADD YOUR WEBHOOK URL HERE OR IN A CONFIG FILE
local WEBHOOK_URL = "YOUR_DISCORD_WEBHOOK_URL_HERE"

local MatchManager = {}
MatchManager.IsRoundActive = false

function MatchManager.StartRound(duration)
    if MatchManager.IsRoundActive then return end
    MatchManager.IsRoundActive = true
    StatTracker.ResetStats()
    
    print("Round Started!")
    task.wait(duration)
    
    MatchManager.EndRound()
end

function MatchManager.EndRound()
    if not MatchManager.IsRoundActive then return end
    MatchManager.IsRoundActive = false
    print("Round Ended!")
    
    local rawStats = StatTracker.GetStats()
    local formattedStats = {}
    
    for playerName, data in pairs(rawStats) do
        table.insert(formattedStats, {
            PlayerName = playerName,
            Kills = data.Kills,
            Deaths = data.Deaths,
            Assists = data.Assists
        })
    end
    
    -- Sort by Kills descending
    table.sort(formattedStats, function(a, b)
        return a.Kills > b.Kills
    end)
    
    if WEBHOOK_URL ~= "YOUR_DISCORD_WEBHOOK_URL_HERE" then
        DiscordWebhook.SendMatchStats(WEBHOOK_URL, formattedStats)
    else
        warn("Webhook URL not set, stats not sent to Discord.")
    end
end

return MatchManager
