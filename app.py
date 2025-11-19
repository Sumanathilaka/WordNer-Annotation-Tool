"""
Annotator App
~~~~~~~~~~~~~~~~~~

This script implements a simple annotation tool for disambiguating Sinhala
lexical items. It uses the Flask web framework to present a minimal web
interface and SQLAlchemy to connect to a PostgreSQL database.

The application populates its database by reading a source JSON file
(``ambigous.json``) via a one-time setup command. Annotators can then
assign a final English translation to each record. Once a row is
annotated, it will no longer be offered for annotation.

Application Structure
---------------------

* Flask: Handles web routing and session management.
* SQLAlchemy: All database interactions are managed via SQLAlchemy (an
  * SQLAlchemy: Manages all database interactions via the `Ambiguous` model.
* PostgreSQL: The backend database.

Usage (Local Testing)
---------------------

To run the annotation server locally, you must have a local PostgreSQL
server running.

1.  Create a database: Use a tool like pgAdmin to create a new,
    empty database (e.g., ``sinhala_annotator``).

2.  Install dependencies:
    (It is highly recommended to use a virtual environment)
    ```bash
    pip install -r requirements.txt
    ```

3.  Set Environment Variables: You must tell the app how to connect
    to your database.

    On PowerShell:
    ```powershell
    $env:DATABASE_URL = "postgresql://YourUser:YourPassword@localhost/sinhala_annotator"
    $env:FLASK_APP = "annotator_app.py" (Only if your main python file was not named as `app.py`)
    ```
    On bash/zsh (macOS/Linux):
    ```bash
    export DATABASE_URL="postgresql://YourUser:YourPassword@localhost/sinhala_annotator"
    export FLASK_APP="annotator_app.py" (Only if your main python file was not named as `app.py`)
    ```

4.  Initialize the Database (One-time step):
    This command creates the tables and loads the data from ``ambigous.json``.
    ```bash
    flask init-db
    ```

5.  Run the Server:
    ```bash
    python app.py
    ```
    or (If your main python file was not named as `app.py`)
    ```bash
    python annotator_app.py
    ```

By default, it binds to ``localhost:8000``. Open your browser and
navigate to ``http://localhost:8000/`` to begin.
"""

import json
import os
from datetime import timedelta, datetime, timezone
from functools import wraps
from http import HTTPStatus
from flask import (
    session,
    Flask,
    redirect,
    render_template_string,
    request,
    url_for,
)
from flask_sqlalchemy import SQLAlchemy
from dotenv import load_dotenv

load_dotenv()

# --- Configuration ---
DATABASE_URL = os.environ.get("DATABASE_URL")
if DATABASE_URL is None:
    raise ValueError("FATAL: DATABASE_URL environment variable is not set.")

# Fix for Heroku/Render PostgreSQL URL mismatch with SQLAlchemy
# SQLAlchemy expects 'postgresql://...' not 'postgres://...'
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

JSON_PATH = os.path.join(os.path.dirname(__file__), "ambigous.json")

# Initialize the Flask app
app = Flask(__name__)

# Session configurations
app.config["SECRET_KEY"] = os.environ.get(
    "SECRET_KEY", "a-very-strong-random-key-for-local-testing"
)
app.config["PERMANENT_SESSION_LIFETIME"] = timedelta(days=30)

# Configure SQLAlchemy
app.config["SQLALCHEMY_DATABASE_URI"] = DATABASE_URL
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False  # Disable noisy warnings
db = SQLAlchemy(app)


# --- Database Model ---
class Ambiguous(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    sense_id = db.Column(db.Text)
    word = db.Column(db.Text)
    tag = db.Column(db.Text)
    gloss = db.Column(db.Text)
    eword = db.Column(db.Text)
    princetonID = db.Column(db.Text)
    no = db.Column(db.Integer)
    final_word = db.Column(db.Text)
    annotated = db.Column(db.Integer, default=0)

    # For tracking the final annotation
    annotated_by_email = db.Column(db.String(120), index=True, nullable=True)

    # For concurrency locking
    locked_by_email = db.Column(db.String(120), index=True, nullable=True)
    locked_at = db.Column(db.DateTime(timezone=True), nullable=True)

    def to_dict(self):
        """Helper to convert the object to a dict, parsing JSON fields."""
        return {
            "id": self.id,
            "sense_id": self.sense_id,
            "word": self.word,
            "tag": self.tag,
            "gloss": json.loads(self.gloss),
            "eword": json.loads(self.eword),
            "princetonID": self.princetonID,
            "no": self.no,
            "final_word": self.final_word,
            "annotated": self.annotated,
            "annotated_by_email": self.annotated_by_email,
            "locked_by_email": self.locked_by_email,
            "locked_at": self.locked_at,
        }


# --- Database Helper Functions ---
def init_db():
    """
    Initialise the SQLAlchemy database schema and populate it from the JSON file.
    This function is called via a CLI command: 'flask init-db'
    """
    # Create all tables defined in our models
    db.create_all()

    # Check if table already has data
    if Ambiguous.query.count() > 0:
        print("Database already populated.")
        return

    print("Database is empty. Loading from JSON...")
    # Load data from JSON file
    if not os.path.exists(JSON_PATH):
        raise FileNotFoundError(f"JSON source file not found: {JSON_PATH}")

    with open(JSON_PATH, "r", encoding="utf-8") as f:
        records = json.load(f)

    # Insert each record
    for rec in records:
        new_item = Ambiguous(
            sense_id=",".join(rec.get("sense_id", []) or []),
            word=",".join(rec.get("word", []) or []),
            tag=",".join(rec.get("tag", []) or []),
            gloss=json.dumps(rec.get("gloss", []), ensure_ascii=False),
            eword=json.dumps(rec.get("eWord", []), ensure_ascii=False),
            princetonID=",".join(rec.get("princetonID", []) or []),
            no=rec.get("no"),
        )
        db.session.add(new_item)  # Add the object to the session

    db.session.commit()  # Commit all new objects to the DB at once
    print("Database loaded successfully.")


# Create a Flask CLI command to run init_db
@app.cli.command("init-db")
def init_db_command():
    """Creates database tables and loads initial data."""
    with app.app_context():
        init_db()
    print("Initialized the database.")


## --- Authentication & Helpers ---
# Wrapper to check for email
def email_required(f):
    """
    A decorator to ensure a user has an email in their session.
    Redirects to the index page if not.
    """

    @wraps(f)
    def decorated_function(*args, **kwargs):
        if "email" not in session:
            return redirect(url_for("index"))
        return f(*args, **kwargs)

    return decorated_function


## --- Core Application Logic ---
def get_next_item(user_email):
    """
    Finds and locks the next available item for a user.
    This must be atomic to prevent race conditions.
    """

    # 1. Handle Stale Locks
    # Unlock any times that were locked > 30 mins ago
    stale_time = datetime.now(timezone.utc) - timedelta(minutes=30)
    Ambiguous.query.filter(Ambiguous.locked_at < stale_time).update(
        {"locked_by_email": None, "locked_at": None}
    )
    db.session.commit()

    # 2. Find and Lock the next time
    item = (
        Ambiguous.query.filter_by(annotated=0, locked_by_email=None)
        .order_by(Ambiguous.id)
        .first()
    )

    if item:
        try:
            item.locked_by_email = user_email
            item.locked_at = datetime.now(timezone.utc)
            db.session.commit()
            return item.to_dict()  # Return the locked item
        except:
            db.session.rollback()
            return None

    return None  # No available items


def update_item(item_id: int, final_word: str, email: str, princeton_id: str = ""):
    """
    Update a record with the annotator's final English word.
    """
    # 1. Find the item
    item_to_update = Ambiguous.query.get(item_id)

    # SECURITY CHECK: Is this item locked by the person submitting it?
    if item_to_update.locked_by_email != email:
        return (
            "Error: This item is not locked by you or the lock expired.",
            HTTPStatus.FORBIDDEN,
        )

    if item_to_update:
        # 2. Update its Python attributes
        item_to_update.final_word = final_word.strip()
        item_to_update.annotated = 1
        item_to_update.annotated_by_email = email

        # --- UNLOCK THE ITEM ---
        item_to_update.locked_by_email = None
        item_to_update.locked_at = None

        if princeton_id.strip():  # Only update if a new ID was provided
            item_to_update.princetonID = princeton_id.strip()

        # 3. Commit the session
        db.session.commit()


# --- Web Server (Flask) ---

# HTML template
HTML_TEMPLATE = """<!doctype html>
<html lang="en">
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>{{ title }}</title>
    <style>
        body { font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; margin: 2em; background-color: #f5f5f5; color: #333; line-height: 1.6; }
        .container { max-width: 800px; margin: auto; background: white; padding: 40px; border-radius: 8px; box-shadow: 0 4px 15px rgba(0,0,0,0.1); }
        h1 { color: #2c3e50; text-align: center; margin-bottom: 0.5em; }
        h2 { color: #2c3e50; border-bottom: 2px solid #eee; padding-bottom: 10px; margin-top: 30px; }
        h3 { color: #34495e; margin-top: 20px; }
        
        /* Login Box Styles */
        .login-box { background-color: #e8f4f8; padding: 25px; border-radius: 8px; border: 1px solid #bce0fd; margin-bottom: 40px; }
        .login-box h2 { margin-top: 0; border-bottom: none; color: #0277bd; }
        label { display: block; margin-top: 1em; font-weight: bold; }
        input[type="text"], input[type="email"] { width: 100%; padding: 10px; margin-top: 0.5em; border: 1px solid #ccc; border-radius: 4px; box-sizing: border-box; }
        input[type="submit"] { margin-top: 1.5em; padding: 12px 25px; font-size: 1.1em; background-color: #0277bd; color: white; border: none; border-radius: 4px; cursor: pointer; width: 100%; }
        input[type="submit"]:hover { background-color: #01579b; }

        /* Annotation Interface Styles */
        ul, ol { padding-left: 1.5em; }
        li { margin-bottom: 0.5em; }
        .gloss { background-color: #eef; padding: 15px; border-radius: 4px; margin: 10px 0; border-left: 4px solid #0277bd; }
        .suggestions { background-color: #fff3cd; padding: 15px; border-radius: 4px; margin: 10px 0; border-left: 4px solid #ffc107; }
        
        /* Contact Info */
        .contact-list { list-style: none; padding: 0; }
        .contact-list li { margin-bottom: 0.2em; }
        a { color: #0277bd; text-decoration: none; }
        a:hover { text-decoration: underline; }
    </style>
</head>
<body>
    <div class="container">
    {{ body | safe }}
    </div>
</body>
</html>
"""


@app.route("/", methods=["GET", "POST"])
def index():
    if request.method == "POST":
        email = request.form.get("email")
        # Basic email validation (you can make this stricter)
        if email and "@" in email:
            session["email"] = email
            # This makes it last for the 30 days we configured
            session.permanent = True
            return redirect(url_for("annotate_page"))

    # If user is already "logged in", send them to annotate
    if "email" in session:
        return redirect(url_for("annotate_page"))

    # Show login form with instructions
    body = """
    <h1>Sinhala WordNet Annotation Tool</h1>
    <p style="text-align: center; font-size: 1.1em; color: #666; margin-bottom: 30px;">
        Welcome to the platform dedicated to building the most comprehensive Sinhala WordNet.
    </p>

    <div class="login-box">
        <h2>Log In to Start</h2>
        <p>Please log in using your email address to begin annotating.</p>
        <form method="post" action="/">
            <label for="email">Email Address:</label>
            <input type="email" id="email" name="email" required placeholder="Enter your email address...">
            <input type="submit" value="Start Annotating">
        </form>
    </div>

    <h2>Introduction</h2>
    <p>A <strong>WordNet</strong> is a large lexical database where words are grouped into sets of synonyms (called <em>synsets</em>) that represent distinct meanings. WordNets help computers understand human language more accurately and are essential for many applications in Natural Language Processing (NLP), such as machine translation, search engines, and language learning tools.</p>
    <p>Your contribution will directly support the development of high-quality Sinhala language resources for researchers and the community.</p>

    <h2>How to Use the Annotation Tool</h2>
    <ol>
        <li><strong>Log In:</strong> Use the form above to log in with your email.</li>
        <li><strong>Read the Provided Sinhala Word:</strong> Each task will present you with a Sinhala word and its meaning in Sinhala.</li>
        <li><strong>Select the Most Suitable English Word:</strong> 
            <ul>
                <li>Your job is to choose the <strong>correct English translation</strong> that fits the meaning given.</li>
                <li>Some Sinhala words may have multiple possible senses, so make sure to pick the <strong>most accurate English word</strong> based on the context provided.</li>
            </ul>
        </li>
        <li><strong>Use Suggestions (Optional):</strong> In some cases, the tool will show suggested English words. You may select one of the suggestions or type a more suitable English word if needed.</li>
        <li><strong>Submit the Annotation:</strong> After selecting or entering the English word, submit your answer to move to the next word.</li>
    </ol>

    <h2>Rewards 🏆</h2>
    <p>To appreciate your contribution:</p>
    <ul>
        <li>The <strong>top 3 users</strong> with the highest number of annotations will receive a <strong>cash prize</strong>.</li>
        <li><em>Make sure to use a valid email address so we can contact you.</em></li>
    </ul>

    <h2>Need Help?</h2>
    <p>If you have any questions or need clarification, feel free to contact:</p>
    <ul class="contact-list">
        <li><strong>Deshan Sumanathilaka</strong> – <a href="mailto:deshan.s@iit.ac.lk">deshan.s@iit.ac.lk</a></li>
        <li><strong>Binuka Rajapaksha</strong> – <a href="mailto:binuka.20221332@iit.ac.lk">binuka.20221332@iit.ac.lk</a></li>
        <li><strong>Siluni Keerthiratne</strong> – <a href="mailto:siluni.20220641@iit.ac.lk">siluni.20220641@iit.ac.lk</a></li>
    </ul>
    """
    return render_template_string(
        HTML_TEMPLATE, title="Welcome | Sinhala WordNet", body=body
    )


@app.route("/logout")
def logout():
    """Clears the email from the session."""
    session.pop("email", None)
    return redirect(url_for("index"))


@app.route("/annotate")
@email_required
def annotate_page():
    # Get email from session
    email = session["email"]

    # Check for an existing locked word
    existing_lock = (
        Ambiguous.query.filter_by(locked_by_email=email, annotated=0)
        .order_by(Ambiguous.id)
        .first()
    )

    if existing_lock:
        item = existing_lock.to_dict()
    else:
        item = get_next_item(email)

    if item is None:
        # This now means there was no existing lock AND no new items were found.
        body = f"""
            <h1>All items are annotated or in progress!</h1>
            <p>Thank you for your contributions.</p>
            <p>You are annotating as: <strong>{email}</strong> (<a href="{url_for('logout')}">Logout</a>)</p>
            """
        return render_template_string(HTML_TEMPLATE, title="Done", body=body)

    gloss_lines = "".join(f"<div class='gloss'>{g}</div>" for g in item["gloss"])
    suggestion_lines = ""
    if item["eword"]:
        suggestions = ", ".join(item["eword"])
        suggestion_lines = f"<div class='suggestions'>Suggestions: {suggestions}</div>"

    body = f"""
        <p style="text-align:right;">
            Annotating as: <strong>{email}</strong> (<a href="{url_for('logout')}">Logout</a>)
        </p>
        <h1>Annotate word</h1>
        <p><strong>Sinhala word:</strong> {item['word']}<br>
            <strong>Sense ID:</strong> {item['sense_id']}<br>
            <strong>Tag:</strong> {item['tag']}<br>
        </p>
        <p><strong>Gloss:</strong></p>
        {gloss_lines}
        {suggestion_lines}
        <form method="post" action="{url_for('submit_page')}">
            <input type="hidden" name="id" value="{item['id']}">
            <label for="final_word">Final English word</label>
            <input type="text" id="final_word" name="final_word" required>
            <label for="princeton_id">Princeton ID (optional)</label>
            <input type="text" id="princeton_id" name="princeton_id">
            <input type="submit" value="Save & Next">
        </form>
    """
    return render_template_string(HTML_TEMPLATE, title="Annotate", body=body)


@app.route("/submit", methods=["POST"])
@email_required
def submit_page():
    # Get email from the session
    email = session["email"]

    try:
        item_id = int(request.form.get("id", "0"))
    except ValueError:
        return "Invalid ID", HTTPStatus.BAD_REQUEST

    final_word = request.form.get("final_word", "").strip()
    princeton_id = request.form.get("princeton_id", "").strip()

    if not final_word:
        return "Final word required", HTTPStatus.BAD_REQUEST

    update_item(item_id, final_word, email, princeton_id)

    return redirect(url_for("annotate_page"))


# This block is for local testing ONLY
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    print(f"Annotation server running locally on http://localhost:{port}/")
    print("NOTE: Run 'flask init-db' in your terminal first to set up the DB.")
    app.run(host="0.0.0.0", port=port, debug=True)
