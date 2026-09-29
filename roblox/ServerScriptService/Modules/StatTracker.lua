local StatTracker = {}
local sessionStats = {}

function StatTracker.ResetStats()
    sessionStats = {}
end

function StatTracker.GetStats()
    return sessionStats
end

function StatTracker.AddStat(playerName, statName, value)
    if not sessionStats[playerName] then
        sessionStats[playerName] = { Kills = 0, Deaths = 0, Assists = 0 }
    end
    sessionStats[playerName][statName] += value
end

function StatTracker.TagHumanoid(humanoid, attackerPlayer)
    local creatorTag = Instance.new("ObjectValue")
    creatorTag.Name = "creator"
    creatorTag.Value = attackerPlayer
    creatorTag.Parent = humanoid
    game.Debris:AddItem(creatorTag, 5) -- Assists timeout after 5 seconds
end

function StatTracker.EvaluateDeath(victimName, humanoid)
    local creators = {}
    for _, child in ipairs(humanoid:GetChildren()) do
        if child.Name == "creator" and child:IsA("ObjectValue") and child.Value then
            table.insert(creators, child.Value.Name)
        end
    end
    
    if #creators == 0 then return end
    
    -- Last hit gets the kill
    local killer = creators[#creators]
    StatTracker.AddStat(killer, "Kills", 1)
    
    -- Others get assists (deduplicated)
    local assistDict = {}
    for i = 1, #creators - 1 do
        local assister = creators[i]
        if assister ~= killer and not assistDict[assister] then
            assistDict[assister] = true
            StatTracker.AddStat(assister, "Assists", 1)
        end
    end
end

return StatTracker
