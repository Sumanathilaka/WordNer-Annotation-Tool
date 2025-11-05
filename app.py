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

* Flask: The web interface and routing are handled by Flask.
* SQLAlchemy: All database interactions are managed via SQLAlchemy (an
    Object-Relational Mapper). The database schema is defined in the
    ``Ambiguous`` class, which maps to the ``ambiguous`` table.
* PostgreSQL: The app is designed to connect to a PostgreSQL database.
    It reads the connection string from a ``DATABASE_URL`` environment
    variable, making it easy to use different local or remote databases.

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
navigate to ``http://localhost:8000/annotate`` to begin.
"""

import json
import os
from flask import (
    Flask,
    redirect,
    render_template_string,
    request,
    url_for,
)
from flask_sqlalchemy import SQLAlchemy
from http import HTTPStatus

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
    """Clears the existing data and creates new tables."""
    with app.app_context():
        init_db()
    print("Initialized the database.")


def get_next_item():
    """
    Fetch the next unannotated record from the database.
    """
    row = Ambiguous.query.filter_by(annotated=0).order_by(Ambiguous.id).first()

    if row is None:
        return None

    return row.to_dict()  # Use helper to convert to dict


def update_item(item_id: int, final_word: str, princeton_id: str = ""):
    """
    Update a record with the annotator's final English word.
    """
    # 1. Find the item
    item_to_update = Ambiguous.query.get(item_id)

    if item_to_update:
        # 2. Update its Python attributes
        item_to_update.final_word = final_word.strip()
        item_to_update.annotated = 1
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
        body { font-family: sans-serif; margin: 2em; background-color: #f5f5f5; }
        .container { max-width: 800px; margin: auto; background: white; padding: 20px; border-radius: 8px; box-shadow: 0 2px 5px rgba(0,0,0,0.1); }
        h1 { font-size: 1.5em; margin-bottom: 0.5em; }
        label { display: block; margin-top: 1em; font-weight: bold; }
        input[type="text"] { width: 100%; padding: 8px; margin-top: 0.5em; }
        input[type="submit"] { margin-top: 1em; padding: 10px 20px; font-size: 1em; }
        ul { padding-left: 1.2em; }
        .gloss { background-color: #eef; padding: 0.5em; border-radius: 4px; margin: 0.2em 0; }
        .suggestions { background-color: #fee; padding: 0.5em; border-radius: 4px; margin: 0.2em 0; }
    </style>
</head>
<body>
    <div class="container">
    {{ body | safe }}
    </div>
</body>
</html>
"""


@app.route("/")
@app.route("/annotate")
def annotate_page():
    item = get_next_item()

    if item is None:
        body = """<h1>All items have been annotated!</h1>
        <p>Thank you for your contributions.</p>"""
        return render_template_string(HTML_TEMPLATE, title="Done", body=body)

    gloss_lines = "".join(f"<div class='gloss'>{g}</div>" for g in item["gloss"])
    suggestion_lines = ""
    if item["eword"]:
        suggestions = ", ".join(item["eword"])
        suggestion_lines = f"<div class='suggestions'>Suggestions: {suggestions}</div>"

    body = f"""
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
def submit_page():
    try:
        item_id = int(request.form.get("id", "0"))
    except ValueError:
        return "Invalid ID", HTTPStatus.BAD_REQUEST

    final_word = request.form.get("final_word", "").strip()
    princeton_id = request.form.get("princeton_id", "").strip()

    if not final_word:
        return "Final word required", HTTPStatus.BAD_REQUEST

    update_item(item_id, final_word, princeton_id)

    return redirect(url_for("annotate_page"))


# This block is for local testing ONLY
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    print(f"Annotation server running locally on http://localhost:{port}/annotate")
    print("NOTE: Run 'flask init-db' in your terminal first to set up the DB.")
    # We must run init_db inside an app_context
    with app.app_context():
        init_db()
    app.run(host="0.0.0.0", port=port, debug=True)
