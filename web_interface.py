
from flask import Flask, render_template_string
import threading
import asyncio
from datetime import datetime
import pytz

app = Flask(__name__)

@app.route('/')
def home():
    template = """
    <!DOCTYPE html>
    <html>
    <head>
        <title>TOP Engaged Bot - Admin Panel</title>
        <style>
            body { font-family: Arial, sans-serif; margin: 40px; background-color: #f5f5f5; }
            .container { max-width: 800px; margin: 0 auto; background: white; padding: 30px; border-radius: 10px; box-shadow: 0 2px 10px rgba(0,0,0,0.1); }
            h1 { color: #333; text-align: center; }
            .status { padding: 15px; background: #d4edda; border: 1px solid #c3e6cb; border-radius: 5px; margin: 20px 0; }
            .info { background: #e7f3ff; border: 1px solid #b6d7ff; padding: 15px; border-radius: 5px; margin: 10px 0; }
        </style>
    </head>
    <body>
        <div class="container">
            <h1>🔝 TOP Engaged Bot - Admin Panel</h1>
            <div class="status">
                <h3>Bot Status: ✅ Running</h3>
                <p>Last updated: {{ current_time }}</p>
            </div>
            <div class="info">
                <h3>Bot Information</h3>
                <p><strong>Bot Name:</strong> TOP Engaged Bot</p>
                <p><strong>Purpose:</strong> Tracks user engagement and announces weekly top users</p>
                <p><strong>Database:</strong> SQLite (top_engaged_db.sqlite)</p>
            </div>
            <div class="info">
                <h3>Available Commands</h3>
                <ul>
                    <li>/start - Start the bot</li>
                    <li>/help - Show help message</li>
                    <li>/my_messages - Show user's message count</li>
                    <li>/top_this_week - Show current week's top users</li>
                    <li>/history_top - Show previous winners</li>
                </ul>
            </div>
        </div>
    </body>
    </html>
    """
    saudi_time = datetime.now(pytz.timezone('Asia/Riyadh'))
    return render_template_string(template, current_time=saudi_time.strftime('%Y-%m-%d %H:%M:%S'))

def run_web_server():
    app.run(host='0.0.0.0', port=5000, debug=False)

if __name__ == '__main__':
    run_web_server()
