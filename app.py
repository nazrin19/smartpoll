from flask import Flask, render_template, request, session
from flask_socketio import SocketIO, join_room, emit
import random
import string

app = Flask(__name__)
app.secret_key = "smartpoll_secret"
socketio = SocketIO(app, cors_allowed_origins="*")

# This stores all rooms in memory
rooms = {}

# Generate a random 6-character room code
def generate_code():
    return ''.join(random.choices(string.ascii_uppercase + string.digits, k=6))

# Home page
@app.route("/")
def index():
    return render_template("index.html")

# Host creates a room
@app.route("/host")
def host():
    return render_template("host.html")

# Guest joins a room
@app.route("/vote")
def vote():
    return render_template("vote.html")

# API: create a new room
@app.route("/create_room", methods=["POST"])
def create_room():
    code = generate_code()
    rooms[code] = {
        "questions": [],
        "current_q": 0,
        "votes": {}
    }
    return {"code": code}

# API: get room data
@app.route("/room/<code>")
def get_room(code):
    if code in rooms:
        return rooms[code]
    return {"error": "Room not found"}, 404

# SocketIO: host and guests join a room
@socketio.on("join")
def on_join(data):
    room = data["room"]
    join_room(room)
    emit("joined", {"room": room}, to=room)

# SocketIO: guest submits a vote
@socketio.on("vote")
def on_vote(data):
    room = data["room"]
    answer = data["answer"]
    if room in rooms:
        votes = rooms[room]["votes"]
        votes[answer] = votes.get(answer, 0) + 1
        # Broadcast updated votes to everyone in the room
        emit("update_votes", votes, to=room)

# SocketIO: host moves to next question
@socketio.on("next_question")
def on_next(data):
    room = data["room"]
    if room in rooms:
        rooms[room]["current_q"] += 1
        q_index = rooms[room]["current_q"]
        questions = rooms[room]["questions"]
        if q_index < len(questions):
            rooms[room]["votes"] = {}
            emit("new_question", questions[q_index], to=room)
        else:
            emit("poll_ended", {}, to=room)

if __name__ == "__main__":
    socketio.run(app, debug=True)