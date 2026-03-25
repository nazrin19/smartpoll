from flask import Flask, render_template, request, jsonify
from flask_socketio import SocketIO, emit, join_room
import random
import string

app = Flask(__name__)
app.config['SECRET_KEY'] = 'poll-secret-123'
socketio = SocketIO(app, cors_allowed_origins="*")

# In-memory database
rooms = {}

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/host')
def host():
    return render_template('host.html')

@app.route('/vote')
def vote():
    return render_template('vote.html')

@app.route('/create_room', methods=['POST'])
def create_room():
    # Generates a 6-character code (e.g., AB1234)
    code = ''.join(random.choices(string.ascii_uppercase + string.digits, k=6))
    rooms[code] = {'questions': [], 'votes': {}}
    return jsonify({'code': code})

@app.route('/start_poll', methods=['POST'])
def start_poll():
    data = request.json
    code = data.get('code')
    if code in rooms:
        rooms[code]['questions'] = data.get('questions')
        return jsonify({'status': 'success'})
    return jsonify({'status': 'error'}), 404

# --- Socket Events ---

@socketio.on('join')
def on_join(data):
    room = data['room']
    join_room(room)

@socketio.on('next_question')
def handle_next(data):
    room = data['room']
    index = data.get('index', 0)
    if room in rooms and index < len(rooms[room]['questions']):
        question_data = rooms[room]['questions'][index]
        rooms[room]['votes'] = {}  # Reset votes for the new question
        emit('new_question', question_data, to=room)

@socketio.on('submit_vote')
def handle_vote(data):
    room = data.get('room')
    answer = data.get('answer')
    next_idx = data.get('next_index') # Get the branched path index
    
    if room in rooms:
        # 1. Update the Host's Live View
        rooms[room]['votes'][answer] = rooms[room]['votes'].get(answer, 0) + 1
        emit('update_votes', rooms[room]['votes'], to=room)

        # 2. Send the NEXT question ONLY to this specific user
        if next_idx is not None and next_idx < len(rooms[room]['questions']):
            next_q = rooms[room]['questions'][next_idx]
            emit('new_question', next_q, room=request.sid)
        else:
            # If no next question, tell them they are done
            emit('new_question', None, room=request.sid)