# In-Game Team Assignment UI Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a graphical user interface (GUI) inside the Roblox local script that allows the executor to quickly assign players to pre-defined external teams (e.g., Cataclysm, Toasters22436).

**Architecture:** 
- A Draggable UI will be injected into `CoreGui` (or `PlayerGui`).
- The UI lists all players in the server, with buttons to assign them to "Team A" or "Team B".
- Assignments are stored in a `PlayerTeams` Lua dictionary.
- The Discord HTTP payload will grab from `PlayerTeams` and send the `Team` field.

**Tech Stack:** Roblox Lua (Luau)

## Global Constraints

- The script must remain self-contained (all UI elements created programmatically via `Instance.new`).
- The UI must be draggable so it doesn't block gameplay.

---

### Task 1: Add Team Variables and UI Scaffolding

**Files:**
- Modify: `d:\main\coding\discord-bot\roblox_script.md`

**Interfaces:**
- Produces: The base GUI instances and dictionaries.

- [ ] **Step 1: Add Configuration Variables**

At the top of the script, beneath `PLAYING_TEAM_NAME`:
```lua
local TEAM_A_NAME = "Cataclysm 🔱"
local TEAM_B_NAME = "Toasters22436 🍞"

local PlayerTeams = {} -- Format: { ["PlayerName"] = "Cataclysm 🔱" }
```

- [ ] **Step 2: Create the Team Admin UI Frame**

Inside the UI section, build a simple frame:
```lua
local AdminFrame = Instance.new("Frame")
AdminFrame.Name = "TeamAdminUI"
AdminFrame.Size = UDim2.fromOffset(300, 400)
AdminFrame.Position = UDim2.fromScale(0.1, 0.2)
AdminFrame.BackgroundColor3 = Color3.fromRGB(30, 30, 30)
AdminFrame.Active = true
AdminFrame.Draggable = true -- Allows the user to move it around!
AdminFrame.Parent = UI

local Title = Instance.new("TextLabel")
Title.Size = UDim2.new(1, 0, 0, 30)
Title.BackgroundColor3 = Color3.fromRGB(50, 50, 50)
Title.TextColor3 = Color3.fromRGB(255, 255, 255)
Title.Text = "Team Assigner"
Title.Font = Enum.Font.GothamBold
Title.TextSize = 14
Title.Parent = AdminFrame

local ScrollingFrame = Instance.new("ScrollingFrame")
ScrollingFrame.Size = UDim2.new(1, 0, 1, -30)
ScrollingFrame.Position = UDim2.fromOffset(0, 30)
ScrollingFrame.BackgroundTransparency = 1
ScrollingFrame.CanvasSize = UDim2.new(0, 0, 0, 0)
ScrollingFrame.UIListLayout = Instance.new("UIListLayout")
ScrollingFrame.UIListLayout.Padding = UDim.new(0, 5)
ScrollingFrame.UIListLayout.Parent = ScrollingFrame
ScrollingFrame.Parent = AdminFrame
```

- [ ] **Step 3: Commit**
(Note: Since this is an external script, no git commit is necessary, just saving the markdown file).

### Task 2: Populate the UI with Players

**Files:**
- Modify: `d:\main\coding\discord-bot\roblox_script.md`

**Interfaces:**
- Consumes: The `Players` service.
- Produces: Buttons for each player.

- [ ] **Step 1: Create a function to render the player list**

```lua
local function refreshTeamUI()
    -- Clear old list
    for _, child in ipairs(ScrollingFrame:GetChildren()) do
        if child:IsA("Frame") then child:Destroy() end
    end
    
    local yOffset = 0
    for _, p in ipairs(Players:GetPlayers()) do
        local pFrame = Instance.new("Frame")
        pFrame.Size = UDim2.new(1, -10, 0, 30)
        pFrame.BackgroundTransparency = 1
        pFrame.Parent = ScrollingFrame
        
        local pName = Instance.new("TextLabel")
        pName.Size = UDim2.new(0.4, 0, 1, 0)
        pName.BackgroundTransparency = 1
        pName.TextColor3 = Color3.fromRGB(255, 255, 255)
        pName.TextScaled = true
        pName.Text = p.Name
        pName.Parent = pFrame
        
        local function createBtn(text, color, xPos, teamVal)
            local btn = Instance.new("TextButton")
            btn.Size = UDim2.new(0.25, 0, 1, 0)
            btn.Position = UDim2.new(xPos, 0, 0, 0)
            btn.BackgroundColor3 = color
            btn.TextColor3 = Color3.fromRGB(255, 255, 255)
            btn.TextScaled = true
            btn.Text = text
            btn.Parent = pFrame
            
            btn.MouseButton1Click:Connect(function()
                PlayerTeams[p.Name] = teamVal
                print("Assigned " .. p.Name .. " to " .. (teamVal or "None"))
            end)
        end
        
        createBtn("Team A", Color3.fromRGB(150, 50, 50), 0.4, TEAM_A_NAME)
        createBtn("Team B", Color3.fromRGB(50, 50, 150), 0.67, TEAM_B_NAME)
        
        yOffset += 35
    end
    ScrollingFrame.CanvasSize = UDim2.new(0, 0, 0, yOffset)
end

Players.PlayerAdded:Connect(refreshTeamUI)
Players.PlayerRemoving:Connect(refreshTeamUI)
refreshTeamUI()
```

### Task 3: Hook into the Payload

**Files:**
- Modify: `d:\main\coding\discord-bot\roblox_script.md`

**Interfaces:**
- Consumes: `PlayerTeams` table.
- Produces: The JSON `stats` payload containing `Team`.

- [ ] **Step 1: Modify `getSortedStats`**

```lua
local function getSortedStats()
	local sortedStats = {}
	for playerName, data in pairs(MatchStats) do
		table.insert(sortedStats, {
            Name = playerName, 
            Kills = data.Kills or 0, 
            Deaths = data.Deaths or 0, 
            Assists = data.Assists or 0,
            Team = PlayerTeams[playerName] or "Unassigned"
        })
	end
	table.sort(sortedStats, function(a, b) return a.Kills > b.Kills end)
	return sortedStats
end
```
