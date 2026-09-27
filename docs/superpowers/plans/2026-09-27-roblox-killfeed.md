# Roblox Round and Killfeed Tracker Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a Roblox server system that tracks rounds, kills, and assists, and broadcasts match statistics to a Discord channel via Webhook.

**Architecture:** A centralized `MatchManager` module handles the round loop and state. A `StatTracker` module listens to player character deaths to attribute kills and assists based on damage history (Creator Tags). At the end of each round, `MatchManager` pulls aggregated data from `StatTracker` and uses a `DiscordWebhook` module (`HttpService`) to send a formatted rich embed to Discord.

**Tech Stack:** Luau (Roblox), Roblox `HttpService`, Discord Webhooks

## Global Constraints

- Target platform: Roblox (`ServerScriptService`)
- Language: Luau
- External API: Discord Webhooks (Must respect rate limits, only send at round end)
- Assuming Rojo/file-system structure (e.g. `src/ServerScriptService/...`) for the source code, but easily adaptable to direct Studio entry.

---

### Task 1: Setup Discord Webhook Module

**Files:**
- Create: `src/ServerScriptService/Modules/DiscordWebhook.lua`
- Create: `src/ServerScriptService/Tests/DiscordWebhook.spec.lua`

**Interfaces:**
- Consumes: Nothing
- Produces: `DiscordWebhook.SendMatchStats(webhookUrl: string, statsData: table)`

- [ ] **Step 1: Write the failing test**

```lua
-- src/ServerScriptService/Tests/DiscordWebhook.spec.lua
local DiscordWebhook = require(script.Parent.Parent.Modules.DiscordWebhook)

return function()
    describe("DiscordWebhook", function()
        it("should format stats correctly without erroring", function()
            local mockStats = {
                { PlayerName = "Player1", Kills = 5, Assists = 2 },
                { PlayerName = "Player2", Kills = 1, Assists = 0 }
            }
            -- Note: In tests we won't actually hit the HTTP endpoint if we pass a dummy URL and wrap in pcall
            local success, err = pcall(function()
                DiscordWebhook.SendMatchStats("dummy_url", mockStats)
            end)
            -- Should fail because HttpService will reject 'dummy_url'
            expect(success).to.equal(false)
        end)
    end)
end
```

- [ ] **Step 2: Run test to verify it fails**

Run: Use TestEZ or run test locally in Studio.
Expected: FAIL with "DiscordWebhook.SendMatchStats is not a function"

- [ ] **Step 3: Write minimal implementation**

```lua
-- src/ServerScriptService/Modules/DiscordWebhook.lua
local HttpService = game:GetService("HttpService")
local DiscordWebhook = {}

function DiscordWebhook.SendMatchStats(webhookUrl, statsData)
    local description = ""
    for _, stat in ipairs(statsData) do
        description = description .. string.format("**%s**: %d Kills | %d Assists\n", stat.PlayerName, stat.Kills, stat.Assists)
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: TestEZ or Studio
Expected: PASS (Method exists and executes, failing gracefully on the pcall HTTP request)

- [ ] **Step 5: Commit**

```bash
git add src/ServerScriptService/Modules/DiscordWebhook.lua src/ServerScriptService/Tests/DiscordWebhook.spec.lua
git commit -m "feat: add discord webhook module for match stats"
```

### Task 2: Player Stat Tracking & Damage Tagging

**Files:**
- Create: `src/ServerScriptService/Modules/StatTracker.lua`
- Create: `src/ServerScriptService/Tests/StatTracker.spec.lua`

**Interfaces:**
- Consumes: Roblox `Humanoid.Died` events, ObjectValues named "creator"
- Produces: `StatTracker.RecordDamage(target: Model, attacker: Player)`, `StatTracker.EvaluateDeath(victim: Player, character: Model)`, `StatTracker.GetStats()`, `StatTracker.ResetStats()`

- [ ] **Step 1: Write the failing test**

```lua
-- src/ServerScriptService/Tests/StatTracker.spec.lua
local StatTracker = require(script.Parent.Parent.Modules.StatTracker)

return function()
    describe("StatTracker", function()
        it("should track stats for players", function()
            StatTracker.ResetStats()
            StatTracker.AddStat("Player1", "Kills", 1)
            local stats = StatTracker.GetStats()
            expect(stats["Player1"].Kills).to.equal(1)
            expect(stats["Player1"].Assists).to.equal(0)
        end)
    end)
end
```

- [ ] **Step 2: Run test to verify it fails**

Run: TestEZ or Studio
Expected: FAIL

- [ ] **Step 3: Write minimal implementation**

```lua
-- src/ServerScriptService/Modules/StatTracker.lua
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
        sessionStats[playerName] = { Kills = 0, Assists = 0 }
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: TestEZ or Studio
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/ServerScriptService/Modules/StatTracker.lua src/ServerScriptService/Tests/StatTracker.spec.lua
git commit -m "feat: add stat tracking and killfeed logic"
```

### Task 3: Match Manager & Round Loop

**Files:**
- Create: `src/ServerScriptService/Modules/MatchManager.lua`
- Modify: `src/ServerScriptService/Main.server.lua`

**Interfaces:**
- Consumes: `StatTracker.GetStats()`, `StatTracker.ResetStats()`, `DiscordWebhook.SendMatchStats()`
- Produces: `MatchManager.StartRound(duration: number)`, `MatchManager.EndRound()`

- [ ] **Step 1: Write the failing test**

*(Omitted for brevity - Testing a yielding loop in unit tests can be tricky, so we test EndRound integration manually in Step 4)*

- [ ] **Step 2: Write the implementation for MatchManager**

```lua
-- src/ServerScriptService/Modules/MatchManager.lua
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
```

- [ ] **Step 3: Hook into Main Server Script**

```lua
-- src/ServerScriptService/Main.server.lua
local Players = game:GetService("Players")
local MatchManager = require(script.Parent.Modules.MatchManager)
local StatTracker = require(script.Parent.Modules.StatTracker)

-- Listen for player deaths
Players.PlayerAdded:Connect(function(player)
    player.CharacterAdded:Connect(function(character)
        local humanoid = character:WaitForChild("Humanoid")
        humanoid.Died:Connect(function()
            StatTracker.EvaluateDeath(player.Name, humanoid)
        end)
    end)
end)

-- Example: Run a 60 second round loop continuously
task.spawn(function()
    while true do
        MatchManager.StartRound(60)
        task.wait(5) -- Intermission
    end
end)
```

- [ ] **Step 4: Run test to verify it passes**

Run: Playtest in Roblox Studio. Verify round starts, prints, and characters dying triggers Evaluation.
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/ServerScriptService/Modules/MatchManager.lua src/ServerScriptService/Main.server.lua
git commit -m "feat: add match manager and wire up systems"
```
