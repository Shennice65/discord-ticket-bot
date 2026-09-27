# Roblox Client-Side Tracking Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Create a client-side executor script that passively tracks the bomb holder, player locations, and intercepts kill events (via RemoteEvents or UI), then sends this data to an external API.

**Architecture:** 
Since you don't own the game, you can't use server scripts. Instead, this will be a LocalScript executed via a third-party executor. 
- Location and Bomb holding are inherently replicated to your client by Roblox, so we can track them by just observing the `Workspace`. 
- Kills are harder to track from the outside, so we will use an event listener to intercept the game's Killfeed RemoteEvent or monitor the UI.
- We will send the data out of Roblox using the executor's built-in `request()` function (since standard `HttpService` doesn't work on the client).

**Tech Stack:** Roblox Luau (Executor Environment)

## Global Constraints

- Script must be contained in a single `.lua` file so it can be copy-pasted into an executor.
- Must use executor-specific HTTP methods (`request` or `http_request`).

---

### Task 1: Foundation and Data Exporting

**Files:**
- Create: `tracker.lua`

**Interfaces:**
- Produces: A function `sendData(eventType, data)` that sends HTTP POST requests to an external URL.

- [ ] **Step 1: Write minimal implementation**

```lua
-- tracker.lua
local Players = game:GetService("Players")
local RunService = game:GetService("RunService")
local LocalPlayer = Players.LocalPlayer

local API_URL = "http://localhost:3000/api/track" -- Replace with your webhook/API

-- Executor HTTP compatibility
local http_request = (request or http_request or syn and syn.request)

local function sendData(eventType, payload)
    if not http_request then 
        warn("No HTTP request function found in executor!")
        return 
    end
    
    task.spawn(function()
        local success, result = pcall(function()
            return http_request({
                Url = API_URL,
                Method = "POST",
                Headers = {
                    ["Content-Type"] = "application/json"
                },
                Body = game:GetService("HttpService"):JSONEncode({
                    event = eventType,
                    data = payload,
                    timestamp = os.time()
                })
            })
        end)
        if not success then
            warn("Failed to send tracking data:", result)
        end
    end)
end

print("Tracker initialized.")
```

- [ ] **Step 2: Run test to verify it passes**

Run: Execute in your executor.
Expected: PASS - "Tracker initialized." is printed. If you call `sendData`, it attempts a POST request.

- [ ] **Step 3: Commit**

```bash
git add tracker.lua
git commit -m "feat: setup basic executor script with HTTP sender"
```

---

### Task 2: Track Player Locations

**Files:**
- Modify: `tracker.lua`

**Interfaces:**
- Consumes: The `Workspace` to read player character positions.
- Produces: Calls `sendData("Positions", { ... })` every second.

- [ ] **Step 1: Write minimal implementation**

```lua
-- tracker.lua (append)

task.spawn(function()
    while task.wait(1) do
        local positions = {}
        for _, player in ipairs(Players:GetPlayers()) do
            if player.Character and player.Character:FindFirstChild("HumanoidRootPart") then
                local pos = player.Character.HumanoidRootPart.Position
                positions[player.Name] = {x = pos.X, y = pos.Y, z = pos.Z}
            end
        end
        
        sendData("Positions", positions)
    end
end)
```

- [ ] **Step 2: Run test to verify it passes**

Run: Execute in game.
Expected: PASS - Sends position data to your server every second.

- [ ] **Step 3: Commit**

```bash
git add tracker.lua
git commit -m "feat: track and broadcast player locations"
```

---

### Task 3: Track Bomb Possession

**Files:**
- Modify: `tracker.lua`

**Interfaces:**
- Consumes: `Character.ChildAdded` and `ChildRemoved` for all players.
- Produces: Calls `sendData("BombEquipped", ...)` when a tool named "Bomb" is equipped.

- [ ] **Step 1: Write minimal implementation**

```lua
-- tracker.lua (append)

local currentBombHolder = nil

local function monitorCharacter(character)
    local player = Players:GetPlayerFromCharacter(character)
    if not player then return end

    character.ChildAdded:Connect(function(child)
        if child:IsA("Tool") and (child.Name == "Bomb" or child.Name:match("C4")) then
            currentBombHolder = player.Name
            sendData("BombEquipped", { player = player.Name })
        end
    end)
    
    character.ChildRemoved:Connect(function(child)
        if child:IsA("Tool") and (child.Name == "Bomb" or child.Name:match("C4")) then
            if currentBombHolder == player.Name then
                currentBombHolder = nil
                sendData("BombDropped", { player = player.Name })
            end
        end
    end)
end

-- Monitor existing characters
for _, player in ipairs(Players:GetPlayers()) do
    if player.Character then
        monitorCharacter(player.Character)
    end
    player.CharacterAdded:Connect(monitorCharacter)
end

Players.PlayerAdded:Connect(function(player)
    player.CharacterAdded:Connect(monitorCharacter)
end)
```

- [ ] **Step 2: Run test to verify it passes**

Run: Execute in game and watch someone equip the bomb.
Expected: PASS - Sends `BombEquipped` data to your server.

- [ ] **Step 3: Commit**

```bash
git add tracker.lua
git commit -m "feat: track bomb holding state via workspace"
```

---

### Task 4: Track Kills via RemoteEvent Interception

**Files:**
- Modify: `tracker.lua`

**Interfaces:**
- Consumes: The game's `RemoteEvent` used for the killfeed.
- Produces: Calls `sendData("Kill", { killer, victim })`.

- [ ] **Step 1: Write minimal implementation**

```lua
-- tracker.lua (append)

-- NOTE: You will need to use an event spy (like SimpleSpy) to find the exact name of the 
-- RemoteEvent the game uses for kills and the structure of its arguments.

local ReplicatedStorage = game:GetService("ReplicatedStorage")
local killEvent = ReplicatedStorage:WaitForChild("KillfeedEvent", 5) -- Replace with actual name!

if killEvent and killEvent:IsA("RemoteEvent") then
    killEvent.OnClientEvent:Connect(function(...)
        local args = {...}
        -- You must adjust these indexes based on what the specific game sends
        local killerName = tostring(args[1]) 
        local victimName = tostring(args[2]) 
        
        sendData("Kill", {
            killer = killerName,
            victim = victimName
        })
    end)
else
    warn("Could not find the Killfeed remote event. You may need to use hookmetamethod to intercept it.")
end
```

- [ ] **Step 2: Run test to verify it passes**

Run: Execute in game.
Expected: PASS - When someone dies, the kill is sent to your server. (Requires manual tuning of the RemoteEvent name).

- [ ] **Step 3: Commit**

```bash
git add tracker.lua
git commit -m "feat: intercept killfeed remote event"
```
