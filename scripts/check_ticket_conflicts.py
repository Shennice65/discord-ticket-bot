"""
Diagnostic & fix script for ticket history conflicts.
Identifies tickets where opponent_id incorrectly points to a user,
pulling other players' matches into their history.

Usage:
    # Diagnose a specific user (shows all suspect tickets)
    python scripts/check_ticket_conflicts.py <USER_ID>
    
    # Fix: nullify opponent_id on suspect tickets for a user
    python scripts/check_ticket_conflicts.py <USER_ID> --fix
    
    # Scan ALL users for conflicts
    python scripts/check_ticket_conflicts.py --all
"""
import asyncio
import os
import sys

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from database import Database


async def diagnose_user(db, user_id: int, do_fix: bool = False):
    """Check and optionally fix ticket conflicts for a specific user."""
    print(f"\n{'='*60}")
    print(f"Checking User ID: {user_id}")
    print(f"{'='*60}")
    
    # Find tickets where this user is the OPPONENT
    opp_tickets = await db.tickets.find({
        "status": "closed",
        "ticket_type": "Ranked 1v1",
        "opponent_id": user_id
    }).sort("closed_at", -1).to_list(length=None)
    
    if not opp_tickets:
        print(f"  No opponent-side tickets found. History is clean.")
        return 0
    
    print(f"  Found {len(opp_tickets)} ticket(s) where user is the opponent.\n")
    
    suspect_tickets = []
    
    for t in opp_tickets:
        result = await db.ranked_results.find_one({"ticket_id": t["id"]})
        
        winner_id = result.get("winner_id") if result else None
        ticket_user_id = t["user_id"]
        
        # A valid ticket: winner_id should be either user_id or opponent_id
        is_valid = (winner_id == user_id or winner_id == ticket_user_id)
        
        # Extra check: if user_id == opponent_id, that's a self-match (always invalid)
        is_self_match = (ticket_user_id == user_id)
        
        suspicious = not is_valid or is_self_match
        
        marker = "⚠️  SUSPECT" if suspicious else "✓  OK"
        print(f"  Ticket #{t['id']} [{marker}]")
        print(f"    Creator (user_id):  {ticket_user_id}")
        print(f"    Opponent (opp_id):  {t.get('opponent_id')}")
        print(f"    Opponent Name:      {t.get('opponent_name')}")
        print(f"    Closed At:          {t.get('closed_at', 'N/A')}")
        
        if result:
            print(f"    Winner ID:          {winner_id}")
            print(f"    Winner Name:        {result.get('winner')}")
            print(f"    Winner Rank:        {result.get('winner_old')} -> {result.get('winner_new')}")
            print(f"    Loser Rank:         {result.get('loser_old')} -> {result.get('loser_new')}")
            print(f"    Observer:           {result.get('observer_name')}")
            if result.get('note'):
                print(f"    Note:               {result.get('note')}")
        else:
            print(f"    Result:             NO RESULT FOUND")
        
        if suspicious:
            reasons = []
            if is_self_match:
                reasons.append("user_id == opponent_id (self-match)")
            if not is_valid:
                reasons.append(f"winner_id ({winner_id}) doesn't match user_id ({ticket_user_id}) or opponent_id ({user_id})")
            print(f"    REASON:             {'; '.join(reasons)}")
            suspect_tickets.append(t)
        
        print()
    
    print(f"  Summary: {len(suspect_tickets)} suspect / {len(opp_tickets)} total opponent-side tickets")
    
    if suspect_tickets and do_fix:
        print(f"\n  --- FIXING {len(suspect_tickets)} suspect ticket(s) ---")
        for t in suspect_tickets:
            await db.tickets.update_one(
                {"_id": t["_id"]},
                {"$set": {"opponent_id": None}}
            )
            print(f"  ✓ Ticket #{t['id']}: Set opponent_id to null")
        print(f"\n  Done! {len(suspect_tickets)} ticket(s) fixed.")
        print(f"  These matches will no longer appear in User {user_id}'s history.")
    elif suspect_tickets and not do_fix:
        print(f"\n  To fix these, run:")
        print(f"    python scripts/check_ticket_conflicts.py {user_id} --fix")
    
    return len(suspect_tickets)


async def scan_all(db):
    """Scan all closed ranked tickets for any with mismatched winner_id."""
    print("Scanning ALL closed ranked tickets for conflicts...\n")
    
    pipeline = [
        {"$match": {
            "status": "closed",
            "ticket_type": "Ranked 1v1",
            "opponent_id": {"$ne": None}
        }},
        {"$lookup": {
            "from": "ranked_results",
            "localField": "id",
            "foreignField": "ticket_id",
            "as": "result"
        }},
        {"$unwind": {"path": "$result", "preserveNullAndEmptyArrays": False}}
    ]
    
    tickets = await db.tickets.aggregate(pipeline).to_list(length=None)
    print(f"Total closed ranked tickets with results: {len(tickets)}\n")
    
    # Find tickets where winner_id doesn't match either user_id or opponent_id
    affected_users = {}
    
    for t in tickets:
        winner_id = t["result"].get("winner_id")
        user_id = t["user_id"]
        opponent_id = t.get("opponent_id")
        
        is_self_match = (user_id == opponent_id)
        is_valid = (winner_id == user_id or winner_id == opponent_id)
        
        if not is_valid or is_self_match:
            tid = t["id"]
            print(f"  ⚠️  Ticket #{tid}:")
            print(f"    user_id={user_id}, opponent_id={opponent_id}, winner_id={winner_id}")
            print(f"    opponent_name={t.get('opponent_name')}, winner={t['result'].get('winner')}")
            print(f"    closed_at={t.get('closed_at')}")
            print()
            
            # Track affected users
            if opponent_id and opponent_id != user_id:
                affected_users.setdefault(opponent_id, []).append(tid)
            if is_self_match:
                affected_users.setdefault(user_id, []).append(tid)
    
    if affected_users:
        print(f"\nAffected users ({len(affected_users)}):")
        for uid, tids in affected_users.items():
            print(f"  User {uid}: {len(tids)} suspect ticket(s) — IDs: {tids}")
        print(f"\nRun with a specific user ID to fix:")
        print(f"  python scripts/check_ticket_conflicts.py <USER_ID> --fix")
    else:
        print("✓ No conflicts found! All tickets look clean.")


async def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return
    
    db = Database()
    if not await db.init():
        print("Failed to connect to database!")
        return
    
    if sys.argv[1] == "--all":
        await scan_all(db)
    else:
        try:
            user_id = int(sys.argv[1])
        except ValueError:
            print(f"Invalid user ID: {sys.argv[1]}")
            return
        
        do_fix = "--fix" in sys.argv
        await diagnose_user(db, user_id, do_fix)


if __name__ == "__main__":
    asyncio.run(main())
