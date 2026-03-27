from flask import Flask, render_template, request, jsonify
from flask_socketio import SocketIO, emit, join_room
from flask_sqlalchemy import SQLAlchemy
import random
import string
import json
import os

app = Flask(__name__)
app.config['SECRET_KEY'] = 'poll-secret-123'

# --- 1. DATABASE SETUP ---
basedir = os.path.abspath(os.path.dirname(__file__))
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///' + os.path.join(basedir, 'poll.db')
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db = SQLAlchemy(app)
socketio = SocketIO(app, cors_allowed_origins="*")

# --- 2. MODELS (The File Cabinet Blueprints) ---
class Room(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    code = db.Column(db.String(6), unique=True, nullable=False)
    questions_json = db.Column(db.Text, nullable=False) 

class Vote(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    room_code = db.Column(db.String(6), nullable=False)
    answer = db.Column(db.String(100), nullable=False)
    question_index = db.Column(db.Integer)

# Initialize the database file on startup
with app.app_context():
    db.create_all()

# --- 3. HTML ROUTES ---
@app.route('/')
def index():
    return render_template('index.html')

@app.route('/host')
def host():
    return render_template('host.html')

@app.route('/vote')
def vote():
    return render_template('vote.html')

# --- 4. API ROUTES ---
@app.route('/create_room', methods=['POST'])
def create_room():
    code = ''.join(random.choices(string.ascii_uppercase + string.digits, k=6))
    # Start with an empty list of questions
    new_room = Room(code=code, questions_json=json.dumps([]))
    db.session.add(new_room)
    db.session.commit()
    return jsonify({'code': code})

@app.route('/start_poll', methods=['POST'])
def start_poll():
    data = request.json
    code = data.get('code')
    room = Room.query.filter_by(code=code).first()
    if room:
        # Save the host's branching questions to the database
        room.questions_json = json.dumps(data.get('questions'))
        db.session.commit()
        return jsonify({'status': 'success'})
    return jsonify({'status': 'error'}), 404

# --- 5. SOCKET EVENTS (Real-Time Communication) ---

@socketio.on('join')
def on_join(data):
    room_code = data['room']
    join_room(room_code)
    print(f">>> SUCCESS: User joined room {room_code}")
    
    # CATCH-UP LOGIC: If a voter joins late, send them Q1 immediately
    room_data = Room.query.filter_by(code=room_code).first()
    if room_data:
        questions = json.loads(room_data.questions_json)
        if len(questions) > 0:
            # Emit ONLY to the person who just joined (request.sid)
            emit('new_question', questions[0], room=request.sid)

@socketio.on('next_question')
def handle_next(data):
    room_code = data['room']
    idx = data.get('index', 0)
    
    room_data = Room.query.filter_by(code=room_code).first()
    if room_data:
        questions = json.loads(room_data.questions_json)
        if idx < len(questions):
            # Send the question to EVERYONE in the room (Launch moment)
            emit('new_question', questions[idx], to=room_code)

@socketio.on('submit_vote')
def handle_vote(data):
    room_code = data.get('room')
    answer = data.get('answer')
    next_idx = data.get('next_index') # The branching destination
    
    # 1. Save the vote permanently
    new_vote = Vote(room_code=room_code, answer=answer)
    db.session.add(new_vote)
    db.session.commit()

    # 2. Update the Host's Live Dashboard
    all_votes = Vote.query.filter_by(room_code=room_code).all()
    results = {}
    for v in all_votes:
        results[v.answer] = results.get(v.answer, 0) + 1
    emit('update_votes', results, to=room_code)

    # 3. BRANCHING: Send the NEXT question ONLY to this specific voter
    room_data = Room.query.filter_by(code=room_code).first()
    if room_data:
        questions = json.loads(room_data.questions_json)
        # Check if the 'next_index' exists in the questions list
        if next_idx is not None and 0 <= next_idx < len(questions):
            next_q = questions[next_idx]
            emit('new_question', next_q, room=request.sid)
        else:
            # No more questions in this branch path
            emit('new_question', None, room=request.sid)

if __name__ == '__main__':
    # Use debug=True to see errors in the terminal automatically
    socketio.run(app, debug=True)