**Introducing the new bot ranking update.**

We tweaked a bunch of things to make it fully automated so observers don't have to do as much manual work anymore.

Here's basically what changed:

───────────────────────

## **1. Auto-shifting leaderboard**
You don't need to do any math to figure out who goes where anymore. If someone beats a higher rank, the bot just swaps them in and shifts everyone else down automatically.

## **2. Simpler observer menus**
When you close a Ranked 1v1 ticket, just pick the winner from the dropdown menu and put your name. The bot handles the rest. For Personal Observations, just type the final rank they got and the bot will slot them in and push the rankings down for you.

## **3. Match limits**
People can only ask for a 1v1 if the person they're challenging is within **5 ranks** of them. Also, players are limited to **one ranked match request per day**, and **one personal observation request every two weeks**. The bot will automatically block them if they try to bypass this.

## **4. Unranked mechanic**
There is now a red **"Unrank"** button on the ticket panel. If you choose to unrank yourself, you will be removed from the leaderboard entirely. But here's the catch:
>-You **cannot get re-ranked for 1 month.**
>-You **cannot request R1s** until you are ranked back to your original rank or higher.

This is to prevent players from dodging ranked 1v1s. Observers can also see an unranked badge on a player's profile when they run `/history`.

───────────────────────
## **5. Bot Commands**
Here's a quick list of all the commands:

**For Admins/Setup:**
`/setup` - Spawns the ticket creation buttons.
`/setupranking` - Spawns the live leaderboard button.
`!sync` - Use this if slash commands aren't showing up.

**For Managing Players (Admins/Observers):**
`/setrank @user [rank]` - Forces someone into a rank and shifts the leaderboard around them.
`/removeplayer @user` - Takes someone off the leaderboard and fills the empty gap.
`/resetrequest @user` - Clears a player's daily match and observation cooldowns.
`/clearunrank @user` - Removes a player's unrank penalty so they can be re-ranked and R1 again.
`/clearhistory @user` - Wipes a player's match and observation history.
`/dbcheck` - Checks if the database is connected.

**For Everyone:**
`/history @user` - Shows a player's stats, past matches, and unranked status if applicable.
`/checkrank @user` - Quickly check a player's exact current rank.
`/botversion` - Check the current version of the bot.
`/close` - Closes the ticket channel you're currently in.
