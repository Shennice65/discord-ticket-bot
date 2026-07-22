"""
Diagnostic script to find ticket history conflicts for a specific user.
Identifies tickets where opponent_id incorrectly points to the user,
pulling other players' matches into their history.

Usage:
    python scripts/check_ticket_conflicts.py
    
    Set TARGET_USER_ID below to the user you want to investigate.
"""
import asyncio
import os
import sys

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from database import Database

# ===== CONFIGURE THIS =====
TARGET_USER_ID = 1467026732425941003  # Lwky's user ID from the screenshot
# ===========================

async def main():
    db = Database()
    if not await db.init():
        print("Failed to connect to database!")
        return
    
    print(f"=== Checking ticket conflicts for User ID: {TARGET_USER_ID} ===\n")
    
    # 1. Find all closed ranked tickets where this user is the ticket CREATOR (user_id)
    user_tickets = await db.tickets.find({
        "status": "closed",
        "ticket_type": "Ranked 1v1",
        "user_id": TARGET_USER_ID
    }).sort("closed_at", -1).to_list(length=None)
    
    # 2. Find all closed ranked tickets where this user is the OPPONENT (opponent_id)
    opp_tickets = await db.tickets.find({
        "status": "closed",
        "ticket_type": "Ranked 1v1",
        "opponent_id": TARGET_USER_ID
    }).sort("closed_at", -1).to_list(length=None)
    
    print(f"Tickets where user is CREATOR (user_id): {len(user_tickets)}")
    print(f"Tickets where user is OPPONENT (opponent_id): {len(opp_tickets)}")
    print()
    
    # 3. Check for overlapping ticket IDs (same ticket in both queries)
    user_ticket_ids = {t["id"] for t in user_tickets}
    opp_ticket_ids = {t["id"] for t in opp_tickets}
    overlap = user_ticket_ids & opp_ticket_ids
    
    if overlap:
        print(f"!!! DUPLICATE TICKETS (appear in both queries): {overlap}")
        for t in user_tickets + opp_tickets:
            if t["id"] in overlap:
                print(f"    Ticket #{t['id']}: user_id={t['user_id']}, opponent_id={t.get('opponent_id')}, opponent_name={t.get('opponent_name')}")
        print()
    
    # 4. Check opponent tickets for suspicious data
    print("--- Tickets where user is OPPONENT (potential conflicts) ---")
    conflicts_found = 0
    
    for t in opp_tickets:
        result = await db.ranked_results.find_one({"ticket_id": t["id"]})
        
        # Check if user was actually involved in this match
        is_winner = result and result.get("winner_id") == TARGET_USER_ID if result else False
        is_loser = not is_winner if result else False
        
        # Flag suspicious: if user_id == opponent_id (self-match)
        is_self_match = t["user_id"] == TARGET_USER_ID
        
        # Flag suspicious: if neither winner_id matches this user
        winner_id = result.get("winner_id") if result else None
        winner_name = result.get("winner") if result else None
        neither_match = winner_id != TARGET_USER_ID and t["user_id"] != TARGET_USER_ID
        
        suspicious = is_self_match or neither_match
        
        marker = " ⚠️ SUSPICIOUS" if suspicious else " ✓"
        
        print(f"\n  Ticket #{t['id']}{marker}")
        print(f"    Creator (user_id): {t['user_id']}")
        print(f"    Opponent (opponent_id): {t.get('opponent_id')}")
        print(f"    Opponent Name: {t.get('opponent_name')}")
        print(f"    Closed At: {t.get('closed_at')}")
        
        if result:
            print(f"    Winner ID: {result.get('winner_id')}")
            print(f"    Winner Name: {result.get('winner')}")
            print(f"    Winner Rank: {result.get('winner_old')} -> {result.get('winner_new')}")
            print(f"    Loser Rank: {result.get('loser_old')} -> {result.get('loser_new')}")
            print(f"    Observer: {result.get('observer_name')}")
            if result.get('note'):
                print(f"    Note: {result.get('note')}")
        else:
            print(f"    Result: NO RESULT FOUND")
        
        if suspicious:
            conflicts_found += 1
            if is_self_match:
                print(f"    REASON: user_id == opponent_id (self-match)")
            if neither_match:
                print(f"    REASON: Target user ({TARGET_USER_ID}) is neither the creator nor the winner - possible wrong opponent_id")
    
    print(f"\n\n=== SUMMARY ===")
    print(f"Total matches in history: {len(user_tickets) + len(opp_tickets)}")
    print(f"  - As creator: {len(user_tickets)}")
    print(f"  - As opponent: {len(opp_tickets)}")
    print(f"  - Duplicates: {len(overlap)}")
    print(f"  - Suspicious conflicts: {conflicts_found}")
    
    if conflicts_found > 0:
        print(f"\n⚠️  Found {conflicts_found} suspicious ticket(s) that may not belong to this user's history!")
        print(f"   Review the tickets marked with ⚠️ above and fix the opponent_id if needed.")
    else:
        print(f"\n✓ No obvious conflicts found in ticket data.")
        print(f"  The issue might be in the ranked_results data — check winner_id values.")

    # 5. Also print the combined history as the bot would show it
    print(f"\n\n--- Combined history (as displayed by /history) ---")
    all_tickets = user_tickets + opp_tickets
    # Deduplicate
    seen = set()
    deduped = []
    for t in all_tickets:
        if t["id"] not in seen:
            seen.add(t["id"])
            deduped.append(t)
    deduped.sort(key=lambda x: x.get("closed_at", ""), reverse=True)
    
    for i, t in enumerate(deduped[:10], 1):
        result = await db.ranked_results.find_one({"ticket_id": t["id"]})
        winner_id = result.get("winner_id") if result else None
        is_win = winner_id == TARGET_USER_ID
        
        was_opponent = t.get("opponent_id") == TARGET_USER_ID
        
        date = t.get("closed_at", "")[:10] if t.get("closed_at") else "Unknown"
        result_text = "WON" if is_win else "LOST"
        
        if was_opponent:
            opponent_display = f"User {t['user_id']} (ticket creator)"
        else:
            opponent_display = t.get("opponent_name", "Unknown")
        
        if result:
            if is_win:
                rank_change = f"{result.get('winner_old', '?')} -> {result.get('winner_new', '?')}"
            else:
                rank_change = f"{result.get('loser_old', '?')} -> {result.get('loser_new', '?')}"
            observer = result.get("observer_name", "?")
            note = result.get("note", "")
        else:
            rank_change = "No result"
            observer = "?"
            note = ""
        
        src = "OPP" if was_opponent else "USR"
        print(f"  #{i} [{src}] {date} | {result_text} | Rank: {rank_change} | vs {opponent_display} | obs: {observer}" + (f" | note: {note}" if note else ""))

if __name__ == "__main__":
    asyncio.run(main())
