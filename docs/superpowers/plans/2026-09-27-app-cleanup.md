# Clean up and modularize app.py Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Refactor the monolithic 3,300+ line `clips/app.py` Flask application into a clean, maintainable structure using Flask Blueprints separated by domain logic.

**Architecture:** We will create an `api` directory inside `clips` and define distinct Blueprints for each domain (Auth, Shop, Betting, Tournaments, Clips/Media, Roblox Integration, Proxy). `app.py` will serve solely as the application factory and global dependency container (DB connections, CORS, session config).

**Tech Stack:** Python, Flask, Flask-Blueprints, PyMongo

## Global Constraints

- Do not alter the URL paths; all endpoints must retain their exact current routing so as to not break the frontend.
- Do not remove any functionality or external dependencies.
- Ensure that the `db` and `redis_connection` dependencies are either injected or imported correctly in the new modules without circular dependencies.
- Maintain existing `@app.route` decorators by converting them to `@blueprint.route` with the exact same paths.

---

### Task 1: Setup Blueprint Structure and Dependencies

**Files:**
- Create: `clips/api/__init__.py`
- Create: `clips/db_deps.py`

**Interfaces:**
- Consumes: Existing MongoDB and Redis initialization logic from `app.py`
- Produces: `get_db()`, `get_redis()`, and `get_player_ranks()` functions/variables that blueprints can import safely.

- [ ] **Step 1: Write `clips/db_deps.py`**
Move the database initialization variables (`mongo_client`, `db`, `player_ranks`, `redis_connection`) from `app.py` into a new `db_deps.py` file to avoid circular imports when blueprints need database access.

```python
# clips/db_deps.py
from pymongo import MongoClient
import os
import certifi
from dotenv import load_dotenv

env_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), '.env')
load_dotenv(env_path)

MONGO_URI = os.environ.get("MONGO_URI")
MONGO_DB_NAME = os.environ.get("MONGO_DB_NAME", "discord_bot_db")
MONGO_TIMEOUT_MS = int(os.environ.get("MONGO_TIMEOUT_MS", "10000"))

mongo_client = None
db = None
player_ranks = None
redis_connection = None

def init_db():
    global mongo_client, db, player_ranks, redis_connection
    if MONGO_URI:
        mongo_client = MongoClient(
            MONGO_URI,
            tlsCAFile=certifi.where(),
            serverSelectionTimeoutMS=MONGO_TIMEOUT_MS,
            connectTimeoutMS=MONGO_TIMEOUT_MS,
        )
        db = mongo_client[MONGO_DB_NAME]
        player_ranks = db.player_ranks
    
    if not os.environ.get("DISCORD_TOKEN"):
        from discord_jobs import create_redis_connection
        try:
            redis_connection = create_redis_connection()
            redis_connection.ping()
        except Exception:
            redis_connection = None
```

- [ ] **Step 2: Initialize module**
Create `clips/api/__init__.py` (can be empty) to make the directory a package.

- [ ] **Step 3: Commit**
```bash
git add clips/db_deps.py clips/api/__init__.py
git commit -m "chore: setup blueprint architecture and dependency injection"
```

### Task 2: Extract Auth and Admin Session Logic

**Files:**
- Create: `clips/api/auth.py`
- Modify: `clips/app.py`

**Interfaces:**
- Consumes: Global error handlers and `db_deps.db`
- Produces: `auth_bp` blueprint

- [ ] **Step 1: Create `clips/api/auth.py`**
Create the blueprint and move `/auth/redeem`, `/api/auth/me`, `/api/auth/logout`, `/checkpassword`, and `/api/clips/admin/session` endpoints into this file. 

```python
# clips/api/auth.py
from flask import Blueprint, request, jsonify, session, redirect
from db_deps import db

auth_bp = Blueprint('auth', __name__)

# Move the respective functions from app.py here
# Make sure to replace @app.route with @auth_bp.route
```

- [ ] **Step 2: Register in `app.py`**
Modify `app.py` to import and register the blueprint, and remove the extracted code.

```python
# clips/app.py
from api.auth import auth_bp
from db_deps import init_db

init_db()
# ...
app.register_blueprint(auth_bp)
```

- [ ] **Step 3: Commit**
```bash
git add clips/api/auth.py clips/app.py
git commit -m "refactor: extract authentication routes into blueprint"
```

### Task 3: Extract Profile Shop and Wallets

**Files:**
- Create: `clips/api/shop.py`
- Modify: `clips/app.py`

**Interfaces:**
- Produces: `shop_bp` blueprint

- [ ] **Step 1: Create `clips/api/shop.py`**
Create the blueprint and move `/api/profile-frames/*`, `/api/admin/profile-frames/*`, and `/api/admin/wallets/*` into this file. Make sure to import `db` from `db_deps.py` and the necessary validators from `profile_shop.py`.

- [ ] **Step 2: Register in `app.py`**
Modify `app.py` to import and register `shop_bp`, and remove the extracted code.

- [ ] **Step 3: Commit**
```bash
git add clips/api/shop.py clips/app.py
git commit -m "refactor: extract profile shop routes into blueprint"
```

### Task 4: Extract Betting and Tournaments

**Files:**
- Create: `clips/api/betting.py`
- Create: `clips/api/tournaments.py`
- Modify: `clips/app.py`

**Interfaces:**
- Produces: `betting_bp`, `tournaments_bp` blueprints

- [ ] **Step 1: Create `clips/api/betting.py`**
Extract `/api/betting/matches/*` and `/api/admin/betting/matches/*` endpoints.

- [ ] **Step 2: Create `clips/api/tournaments.py`**
Extract `/api/tournament/standings`, `/api/admin/tournament/*`, and `/api/matches/recent`.

- [ ] **Step 3: Register in `app.py`**
Register `betting_bp` and `tournaments_bp` in `app.py` and remove original routes.

- [ ] **Step 4: Commit**
```bash
git add clips/api/betting.py clips/api/tournaments.py clips/app.py
git commit -m "refactor: extract betting and tournament routes"
```

### Task 5: Extract Media/Clips Management

**Files:**
- Create: `clips/api/media.py`
- Modify: `clips/app.py`

**Interfaces:**
- Produces: `media_bp` blueprint

- [ ] **Step 1: Create `clips/api/media.py`**
Extract video upload logic (`/upload`, `/import_url`), fetching (`/api/clips`, `/api/clip/*`, `/file/*`, `/photo/*`), reactions, editing, and deleting endpoints.

- [ ] **Step 2: Register in `app.py`**
Register `media_bp` in `app.py` and remove original routes.

- [ ] **Step 3: Commit**
```bash
git add clips/api/media.py clips/app.py
git commit -m "refactor: extract media and clips management routes"
```

### Task 6: Extract Roblox Integration & Webhooks

**Files:**
- Create: `clips/api/roblox.py`
- Create: `clips/api/proxy.py`
- Modify: `clips/app.py`

**Interfaces:**
- Produces: `roblox_bp`, `proxy_bp` blueprints

- [ ] **Step 1: Create `clips/api/roblox.py`**
Extract `/api/roblox/*`, `/api/players`, `/api/avatars`, `/api/player/<user_id>/history`.

- [ ] **Step 2: Create `clips/api/proxy.py`**
Extract `/ingest/<path:proxy_path>`.

- [ ] **Step 3: Clean up `app.py`**
Register the remaining blueprints in `app.py`. `app.py` should now only contain standard setup (imports, `init_db()`, CORS setup, global before/after request handlers, error handlers, and generic UI renders like `@app.route('/clips')`).

- [ ] **Step 4: Commit**
```bash
git add clips/api/roblox.py clips/api/proxy.py clips/app.py
git commit -m "refactor: extract roblox and proxy logic, complete blueprint setup"
```
