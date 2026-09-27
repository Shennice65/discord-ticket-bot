local StatTracker = require(script.Parent.Parent.Modules.StatTracker)

return function()
    describe("StatTracker", function()
        it("should track stats for players", function()
            StatTracker.ResetStats()
            StatTracker.AddStat("Player1", "Kills", 1)
            local stats = StatTracker.GetStats()
            expect(stats["Player1"].Kills).to.equal(1)
            expect(stats["Player1"].Deaths).to.equal(0)
            expect(stats["Player1"].Assists).to.equal(0)
        end)
    end)
end
