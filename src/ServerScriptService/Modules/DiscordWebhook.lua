local HttpService = game:GetService("HttpService")
local DiscordWebhook = {}

function DiscordWebhook.SendMatchStats(webhookUrl, statsData)
    local description = ""
    for _, stat in ipairs(statsData) do
        description = description .. string.format("**%s**: %d Kills | %d Deaths | %d Assists\n", stat.PlayerName, stat.Kills, stat.Deaths, stat.Assists)
    end
    
    if description == "" then
        description = "No stats recorded this round."
    end

    local payload = {
        embeds = {{
            title = "🏁 Match Concluded!",
            description = description,
            color = tonumber(0x00FF00)
        }}
    }

    local jsonData = HttpService:JSONEncode(payload)
    -- Wrap in pcall to avoid crashing the server on HTTP failure
    pcall(function()
        HttpService:PostAsync(webhookUrl, jsonData, Enum.HttpContentType.ApplicationJson)
    end)
end

return DiscordWebhook
