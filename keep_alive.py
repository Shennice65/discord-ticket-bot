from flask import Flask
from threading import Thread
import logging

# Disable Flask's default logging to keep console clean
log = logging.getLogger('werkzeug')
log.setLevel(logging.ERROR)

app = Flask('')

@app.route('/')
def home():
    return """
    <html>
        <head>
            <title>Ticket Bot Status</title>
            <style>
                body { 
                    font-family: Arial, sans-serif; 
                    display: flex; 
                    justify-content: center; 
                    align-items: center; 
                    height: 100vh; 
                    margin: 0; 
                    background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
                    color: white;
                }
                .container { text-align: center; }
                h1 { font-size: 3em; margin-bottom: 0.2em; }
                p { font-size: 1.2em; opacity: 0.9; }
                .status { color: #4ade80; font-weight: bold; }
            </style>
        </head>
        <body>
            <div class="container">
                <h1>🎫 Ticket Bot</h1>
                <p>Status: <span class="status">Online ✅</span></p>
                <p>Last checked: <span id="time"></span></p>
            </div>
            <script>
                document.getElementById('time').textContent = new Date().toLocaleString();
            </script>
        </body>
    </html>
    """

@app.route('/health')
def health():
    return {"status": "online", "bot": "TicketBot"}

def run():
    # Use port 8080 for Render compatibility
    # 0.0.0.0 makes it accessible externally
    app.run(host='0.0.0.0', port=8080)

def keep_alive():
    """Starts a Flask web server in a separate thread"""
    t = Thread(target=run)
    t.daemon = True  # Thread will close when main program exits
    t.start()
    print("Keep-alive web server started on port 8080")