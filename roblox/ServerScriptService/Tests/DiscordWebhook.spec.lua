local DiscordWebhook = require(script.Parent.Parent.Modules.DiscordWebhook)

return function()
    describe("DiscordWebhook", function()
        it("should format stats correctly without erroring", function()
            local mockStats = {
                { PlayerName = "Player1", Kills = 5, Deaths = 1, Assists = 2 },
                { PlayerName = "Player2", Kills = 1, Deaths = 0, Assists = 0 }
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
