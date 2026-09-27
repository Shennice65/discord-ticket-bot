--// =========================================================
--// NAMETAGS + BOMB TRACKER + TEAM ROUND DETECTOR + LIVE DISCORD KDA
--// =========================================================

local Players = game:GetService("Players")
local HttpService = game:GetService("HttpService")
local LocalPlayer = Players.LocalPlayer

--// =========================================================
--// SETTINGS
--// =========================================================

local PLAYING_TEAM_NAME = "Playing"
local TEAM_A_NAME = "Cataclysm "
local TEAM_B_NAME = "Toasters22436"

local PlayerTeams = {} -- Format: { ["PlayerName"] = "Cataclysm " }

local function updatePlayerNametag(player)
    if player.Character and player.Character:FindFirstChild("Head") then
        local tag = player.Character.Head:FindFirstChild("TrackerNametag")
        if tag and tag:FindFirstChild("TeamLabel") then
            local team = PlayerTeams[player.Name]
            if team then
                tag.TeamLabel.Text = "[" .. team .. "]"
                tag.TeamLabel.TextColor3 = (team == TEAM_A_NAME) and Color3.fromRGB(255, 100, 100) or Color3.fromRGB(100, 100, 255)
                tag.TeamLabel.Visible = true
            else
                tag.TeamLabel.Visible = false
            end
        end
    end
end

-- IMPORTANT: Set this to your Discord bot's IP/URL and Port!
local BOT_API_URL = "https://atlclips.site/api/roblox/match-stats"
local BOT_LIVE_URL = "https://atlclips.site/api/roblox/live-update"

local MatchStats = {}
local RoundActive = false
local deadPlayersThisRound = {}
local BombHistory = {} -- Simple list of who held the bomb!


--// =========================================================
--// UI & KILLFEED
--// =========================================================

local UI = Instance.new("ScreenGui")
UI.Name = "TrackerUI"
UI.ResetOnSpawn = false
UI.IgnoreGuiInset = true
pcall(function() UI.Parent = game:GetService("CoreGui") end)
if not UI.Parent then UI.Parent = LocalPlayer:WaitForChild("PlayerGui") end

local KillFeed = Instance.new("TextLabel")
KillFeed.Name = "KillFeed"
KillFeed.AnchorPoint = Vector2.new(0.5, 0)
KillFeed.Position = UDim2.new(0.5, 0, 0, 50)
KillFeed.Size = UDim2.fromOffset(500, 40)
KillFeed.BackgroundTransparency = 1
KillFeed.Font = Enum.Font.GothamBlack
KillFeed.TextSize = 22
KillFeed.TextColor3 = Color3.fromRGB(255, 80, 80)
KillFeed.TextStrokeTransparency = 0
KillFeed.Text = ""
KillFeed.Visible = false
KillFeed.ZIndex = 50
KillFeed.Parent = UI

local AdminFrame = Instance.new("Frame")
AdminFrame.Name = "TeamAdminUI"
AdminFrame.Size = UDim2.fromOffset(300, 400)
AdminFrame.Position = UDim2.fromScale(0.1, 0.2)
AdminFrame.BackgroundColor3 = Color3.fromRGB(30, 30, 30)
AdminFrame.Active = true
AdminFrame.Draggable = true
AdminFrame.Parent = UI

local Title = Instance.new("TextLabel")
Title.Size = UDim2.new(1, 0, 0, 30)
Title.BackgroundColor3 = Color3.fromRGB(50, 50, 50)
Title.TextColor3 = Color3.fromRGB(255, 255, 255)
Title.Text = "Team Assigner (Drag me)"
Title.Font = Enum.Font.GothamBold
Title.TextSize = 14
Title.Parent = AdminFrame

local ScrollingFrame = Instance.new("ScrollingFrame")
ScrollingFrame.Size = UDim2.new(1, 0, 1, -30)
ScrollingFrame.Position = UDim2.fromOffset(0, 30)
ScrollingFrame.BackgroundTransparency = 1
ScrollingFrame.CanvasSize = UDim2.new(0, 0, 0, 0)
ScrollingFrame.Parent = AdminFrame

local UIListLayout = Instance.new("UIListLayout")
UIListLayout.Padding = UDim.new(0, 5)
UIListLayout.Parent = ScrollingFrame

local function refreshTeamUI()
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
            return btn
        end
        
        local btnCat = createBtn("Cataclysm", Color3.fromRGB(100, 30, 30), 0.4, TEAM_A_NAME)
        local btnToa = createBtn("Toasters", Color3.fromRGB(30, 30, 100), 0.67, TEAM_B_NAME)
        
        local function updateBtnColors()
            local t = PlayerTeams[p.Name]
            btnCat.BackgroundColor3 = (t == TEAM_A_NAME) and Color3.fromRGB(255, 50, 50) or Color3.fromRGB(100, 30, 30)
            btnToa.BackgroundColor3 = (t == TEAM_B_NAME) and Color3.fromRGB(100, 100, 255) or Color3.fromRGB(30, 30, 100)
        end
        
        btnCat.MouseButton1Click:Connect(function()
            PlayerTeams[p.Name] = TEAM_A_NAME
            updateBtnColors()
            updatePlayerNametag(p)
        end)
        
        btnToa.MouseButton1Click:Connect(function()
            PlayerTeams[p.Name] = TEAM_B_NAME
            updateBtnColors()
            updatePlayerNametag(p)
        end)
        
        updateBtnColors()
        
        yOffset = yOffset + 35
    end
    ScrollingFrame.CanvasSize = UDim2.new(0, 0, 0, yOffset)
end

Players.PlayerAdded:Connect(refreshTeamUI)
Players.PlayerRemoving:Connect(refreshTeamUI)
task.spawn(refreshTeamUI)

local function announceKill(victim, killer)
	local message = "💥 " .. killer .. " blew up " .. victim .. "!"
	print("[KILLFEED] " .. message)
	KillFeed.Text = message
	KillFeed.Visible = true

	task.delay(4, function()
		if KillFeed.Parent and KillFeed.Text == message then
			KillFeed.Visible = false
		end
	end)
end


--// =========================================================
--// DISCORD HTTP HANDLERS
--// =========================================================

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

local function sendLiveUpdateToDiscord(killfeedMsg)
	local httpRequest = (syn and syn.request) or (http and http.request) or http_request or (fluxus and fluxus.request) or request
	if httpRequest then
		local response = httpRequest({
			Url = BOT_LIVE_URL,
			Method = "POST",
			Headers = { ["Content-Type"] = "application/json" },
			Body = HttpService:JSONEncode({ stats = getSortedStats(), killfeed_message = killfeedMsg })
		})
		print("[LIVE UPDATE HTTP RESPONSE]:", response and response.StatusCode, response and response.Body)
	else
	    warn("No httpRequest function found! Cannot send webhook.")
	end
end

local function sendMatchStatsToDiscord()
	local httpRequest = (syn and syn.request) or (http and http.request) or http_request or (fluxus and fluxus.request) or request
	if httpRequest then
		local response = httpRequest({
			Url = BOT_API_URL,
			Method = "POST",
			Headers = { ["Content-Type"] = "application/json" },
			Body = HttpService:JSONEncode({ stats = getSortedStats() })
		})
		print("[MATCH STATS HTTP RESPONSE]:", response and response.StatusCode, response and response.Body)
	else
	    warn("No httpRequest function found! Cannot send webhook.")
	end
end


--// =========================================================
--// SIMPLE DEATH LOGIC
--// =========================================================

local function handleDeath(victimName)
	if not RoundActive then return end
	if deadPlayersThisRound[victimName] then return end
	
	-- ONLY trigger a kill if they were the LAST person to hold the bomb!
	if BombHistory[#BombHistory] ~= victimName then return end

	deadPlayersThisRound[victimName] = true

	-- The second-to-last person is the killer!
	local killer = victimName
	if #BombHistory >= 2 then
		killer = BombHistory[#BombHistory - 1]
	end

	if killer == victimName then
		announceKill(victimName, "Themself") 
	else
		announceKill(victimName, killer)
	end
	
	-- // TRACK KDA FOR DISCORD //
	if not MatchStats[victimName] then MatchStats[victimName] = {Kills = 0, Deaths = 0, Assists = 0} end
	MatchStats[victimName].Deaths += 1

	if killer ~= victimName then
		if not MatchStats[killer] then MatchStats[killer] = {Kills = 0, Deaths = 0, Assists = 0} end
		MatchStats[killer].Kills += 1
	end
	
	-- Assists for everyone else (ONLY if they are on the same team as the killer!)
	local recordedAssists = {}
	local killerTeam = PlayerTeams[killer]

	for i = 1, #BombHistory - 2 do
		local assister = BombHistory[i]
		if assister ~= killer and assister ~= victimName and not recordedAssists[assister] then
			-- Only give the assist if both have a team, and the teams match
			if killerTeam and PlayerTeams[assister] == killerTeam then
				recordedAssists[assister] = true
				if not MatchStats[assister] then MatchStats[assister] = {Kills = 0, Deaths = 0, Assists = 0} end
				MatchStats[assister].Assists += 1
			end
		end
	end

	-- Erase bomb history since it blew up
	BombHistory = {}

	-- Send the LIVE UPDATE to Discord!
	if killer == victimName then
		sendLiveUpdateToDiscord(victimName .. " blew themselves up!")
	else
		sendLiveUpdateToDiscord(killer .. " blew up " .. victimName .. "!")
	end
end


local function getHeldObject(character)
	if not character then return nil end
	for _, child in ipairs(character:GetChildren()) do
		local name = string.lower(child.Name)
		if child:IsA("Tool") or string.find(name, "bomb") or string.find(name, "timebomb") or string.find(name, "juggernaut") then
			return child
		end
	end
	return nil
end


--// =========================================================
--// NAMETAGS & BOMB TRACKER
--// =========================================================

local function createNametag(player)
	if player == LocalPlayer then return end

	local function setupCharacter(character)
		local head = character:WaitForChild("Head", 5)
		local humanoid = character:WaitForChild("Humanoid", 5)
		if not head or not humanoid then return end

		local oldTag = head:FindFirstChild("TrackerNametag")
		if oldTag then oldTag:Destroy() end

		local Billboard = Instance.new("BillboardGui")
		Billboard.Name = "TrackerNametag"
		Billboard.Size = UDim2.fromOffset(220, 80)
		Billboard.StudsOffset = Vector3.new(0, 2.7, 0)
		Billboard.AlwaysOnTop = true
		Billboard.Parent = head

		local NameLabel = Instance.new("TextLabel")
		NameLabel.Size = UDim2.fromScale(1, 0.33)
		NameLabel.Position = UDim2.fromScale(0, 0)
		NameLabel.BackgroundTransparency = 1
		NameLabel.Text = player.DisplayName .. " (@" .. player.Name .. ")"
		NameLabel.TextColor3 = Color3.fromRGB(255, 255, 255)
		NameLabel.Font = Enum.Font.GothamBold
		NameLabel.TextSize = 13
		NameLabel.Parent = Billboard
        
        local TeamLabel = Instance.new("TextLabel")
        TeamLabel.Name = "TeamLabel"
        TeamLabel.Size = UDim2.fromScale(1, 0.33)
        TeamLabel.Position = UDim2.fromScale(0, 0.33)
        TeamLabel.BackgroundTransparency = 1
        TeamLabel.Font = Enum.Font.GothamBlack
        TeamLabel.TextSize = 14
        TeamLabel.Visible = false
        TeamLabel.Parent = Billboard

		local ToolLabel = Instance.new("TextLabel")
		ToolLabel.Size = UDim2.fromScale(1, 0.33)
		ToolLabel.Position = UDim2.fromScale(0, 0.66)
		ToolLabel.BackgroundTransparency = 1
		ToolLabel.TextColor3 = Color3.fromRGB(255, 85, 85)
		ToolLabel.Font = Enum.Font.GothamBold
		ToolLabel.TextSize = 12
		ToolLabel.Visible = false
		ToolLabel.Parent = Billboard
        
        updatePlayerNametag(player)

		local function updateToolStatus()
			local heldObject = getHeldObject(character)
			if heldObject then
				ToolLabel.Text = "[Holding: " .. heldObject.Name .. "]"
				ToolLabel.Visible = true

				-- If this player just grabbed the bomb, add them to the history
				if BombHistory[#BombHistory] ~= player.Name then
					table.insert(BombHistory, player.Name)
				end
			else
				ToolLabel.Text = ""
				ToolLabel.Visible = false
			end
		end

		updateToolStatus()
		character.ChildAdded:Connect(function() updateToolStatus() end)
		character.ChildRemoved:Connect(function() updateToolStatus() end)

		-- Catch physical deaths or character despawns
		humanoid.Died:Connect(function() handleDeath(player.Name) end)
		character.AncestryChanged:Connect(function(_, parent)
			if not parent then handleDeath(player.Name) end
		end)
	end

	if player.Character then task.spawn(setupCharacter, player.Character) end
	player.CharacterAdded:Connect(setupCharacter)
end

for _, player in ipairs(Players:GetPlayers()) do createNametag(player) end
Players.PlayerAdded:Connect(createNametag)


--// =========================================================
--// ROUND DETECTOR
--// =========================================================

local function getPlayingCount()
	local count = 0
	for _, p in ipairs(Players:GetPlayers()) do
		if p.Team and p.Team.Name == PLAYING_TEAM_NAME then count += 1 end
	end
	return count
end

local function checkRoundState()
	if getPlayingCount() <= 0 then
		if RoundActive then
			RoundActive = false
			print("[ROUND END]")
			sendMatchStatsToDiscord()
			MatchStats = {}
			BombHistory = {}
		end
		return
	end

	if not RoundActive then
		RoundActive = true
		print("[ROUND START]")
		deadPlayersThisRound = {}
		BombHistory = {}
	end
end

local function hookPlayerTeam(player)
	local wasPlaying = (player.Team and player.Team.Name == PLAYING_TEAM_NAME)
	player:GetPropertyChangedSignal("Team"):Connect(function()
		local isPlaying = (player.Team and player.Team.Name == PLAYING_TEAM_NAME)
		
		-- If they are removed from playing mid-round, they were eliminated
		if wasPlaying and not isPlaying and RoundActive then
			handleDeath(player.Name)
		end
		
		wasPlaying = isPlaying
		checkRoundState()
	end)
	player:GetPropertyChangedSignal("Neutral"):Connect(function() checkRoundState() end)
end

for _, player in ipairs(Players:GetPlayers()) do hookPlayerTeam(player) end
Players.PlayerAdded:Connect(function(player)
	hookPlayerTeam(player)
	task.defer(function() checkRoundState() end)
end)
Players.PlayerRemoving:Connect(function(player)
	if RoundActive then handleDeath(player.Name) end
	task.defer(function() checkRoundState() end)
end)

task.defer(function() checkRoundState() end)
print("[SIMPLE BOMB TRACKER - LIVE UPDATE MODE] Loaded.")
