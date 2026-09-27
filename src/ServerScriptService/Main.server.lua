local Players = game:GetService("Players")
local ReplicatedStorage = game:GetService("ReplicatedStorage")
local MatchManager = require(script.Parent.Modules.MatchManager)
local StatTracker = require(script.Parent.Modules.StatTracker)

local PLAYING_TEAM_NAME = "Playing"

-- Create the RemoteEvent if it doesn't exist
local StatEvent = ReplicatedStorage:FindFirstChild("StatEvent")
if not StatEvent then
    StatEvent = Instance.new("RemoteEvent")
    StatEvent.Name = "StatEvent"
    StatEvent.Parent = ReplicatedStorage
end

-- Listen for kills coming from the LocalScript
StatEvent.OnServerEvent:Connect(function(firingPlayer, killerName)
    -- Security: We only trust the firingPlayer if they are reporting their OWN death
    -- (The LocalScript will only fire this if player == LocalPlayer)
    StatTracker.AddStat(firingPlayer.Name, "Deaths", 1)

    if killerName and killerName ~= "Nobody" then
        StatTracker.AddStat(killerName, "Kills", 1)
        print("Server registered kill for:", killerName)
    end
end)

-- Team-Based Round Detector (Server-Side)
local function getPlayingCount()
    local count = 0
    for _, player in ipairs(Players:GetPlayers()) do
        if player.Team and player.Team.Name == PLAYING_TEAM_NAME then
            count += 1
        end
    end
    return count
end

local function checkRoundState()
    local playingCount = getPlayingCount()
    
    if playingCount <= 0 then
        -- Round Ends! Send the Webhook!
        MatchManager.EndRound()
    else
        -- Round Starts!
        MatchManager.StartRound(math.huge)
    end
end

-- Hook Team Changes
local function hookPlayerTeam(player)
    player:GetPropertyChangedSignal("Team"):Connect(checkRoundState)
    player:GetPropertyChangedSignal("Neutral"):Connect(checkRoundState)
end

for _, player in ipairs(Players:GetPlayers()) do
    hookPlayerTeam(player)
end

Players.PlayerAdded:Connect(function(player)
    hookPlayerTeam(player)
    task.defer(checkRoundState)
end)

Players.PlayerRemoving:Connect(function()
    task.defer(checkRoundState)
end)
