from flask import Flask, render_template, request, redirect, url_for, flash
import threading
from datetime import datetime
from note_writer.bot_engine import CommunityNotesBot
from data_models import NoteResult

app = Flask(__name__)
app.secret_key = 'supersecretkey'  # Change this for production

# Global history of runs (in-memory for now)
HISTORY = []

def run_bot_background(num_posts, dry_run):
    """Run the bot in a background thread and append results to history."""
    try:
        bot = CommunityNotesBot()
        results = bot.run(num_posts=num_posts, dry_run=dry_run)
        
        run_data = {
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "num_posts": num_posts,
            "dry_run": dry_run,
            "results": results,
            "status": "Completed"
        }
        HISTORY.insert(0, run_data)
    except Exception as e:
        run_data = {
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "status": f"Failed: {e}",
            "results": []
        }
        HISTORY.insert(0, run_data)

@app.route('/')
def index():
    return render_template('index.html', history=HISTORY)

@app.route('/run', methods=['POST'])
def run_bot():
    num_posts = int(request.form.get('num_posts', 5))
    dry_run = 'dry_run' in request.form
    
    # Start in background to not block the UI
    thread = threading.Thread(target=run_bot_background, args=(num_posts, dry_run))
    thread.start()
    
    flash(f"Bot started! Processing {num_posts} posts (Dry Run: {dry_run}). Refresh page to see results.")
    return redirect(url_for('index'))

if __name__ == '__main__':
    app.run(debug=True, port=5000)
