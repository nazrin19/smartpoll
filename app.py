from flask import Flask, render_template, request, jsonify
from flask_socketio import SocketIO, emit, join_room
from flask_sqlalchemy import SQLAlchemy
import random, string, json, os

app = Flask(__name__)
app.config['SECRET_KEY'] = 'poll-secret-123'
basedir = os.path.abspath(os.path.dirname(__file__))
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///' + os.path.join(basedir, 'poll.db')
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db = SQLAlchemy(app)
socketio = SocketIO(app, cors_allowed_origins="*")

# In-memory track of active socket connections for the user counter
active_voters = {}

# --- MODELS ---

class Room(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    code = db.Column(db.String(6), unique=True, nullable=False)
    questions_json = db.Column(db.Text, nullable=False) 

class Vote(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    room_code = db.Column(db.String(6), nullable=False)
    answer = db.Column(db.String(100), nullable=False)
    question_index = db.Column(db.Integer)
    voter_id = db.Column(db.String(100)) # Crucial for identifying which vote to update

class VoterRecord(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    voter_id = db.Column(db.String(100), nullable=False)
    room_code = db.Column(db.String(6), nullable=False)
    question_index = db.Column(db.Integer, nullable=False)

with app.app_context():
    db.create_all()

# --- HELPER FUNCTION ---

def generate_report(room_code):
    """Queries the database for a fresh count of all votes in a room."""
    all_votes = Vote.query.filter_by(room_code=room_code).all()
    report = {}
    for v in all_votes:
        idx_str = str(v.question_index)
        if idx_str not in report:
            report[idx_str] = {}
        report[idx_str][v.answer] = report[idx_str].get(v.answer, 0) + 1
    return report

# --- ROUTES ---

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/host')
def host():
    return render_template('host.html')

@app.route('/vote')
def vote():
    return render_template('vote.html')

@app.route('/get_room_state/<room_code>')
def get_room_state(room_code):
    room = Room.query.filter_by(code=room_code).first()
    if room:
        return jsonify({
            'questions': json.loads(room.questions_json),
            'report': generate_report(room_code)
        })
    return jsonify({'status': 'error'}), 404

@app.route('/create_room', methods=['POST'])
def create_room():
    code = ''.join(random.choices(string.ascii_uppercase + string.digits, k=6))
    new_room = Room(code=code, questions_json=json.dumps([]))
    db.session.add(new_room)
    db.session.commit()
    active_voters[code] = set() 
    return jsonify({'code': code})

@app.route('/start_poll', methods=['POST'])
def start_poll():
    data = request.json
    room = Room.query.filter_by(code=data.get('code')).first()
    if room:
        room.questions_json = json.dumps(data.get('questions'))
        db.session.commit()
        socketio.emit('poll_started', to=room.code)
        return jsonify({'status': 'success'})
    return jsonify({'status': 'error'}), 404

# --- SOCKET EVENTS ---

@socketio.on('join')
def on_join(data):
    room_code = data.get('room')
    user_type = data.get('type', 'voter') 
    
    room_data = Room.query.filter_by(code=room_code).first()
    if room_data:
        join_room(room_code)
        if user_type == 'voter':
            if room_code not in active_voters:
                active_voters[room_code] = set()
            active_voters[room_code].add(request.sid)
            
            # Update user count for everyone in the room
            emit('user_count', {'count': len(active_voters[room_code])}, to=room_code)
            
            # IMPORTANT: Automatically send the first question so the UI populates
            qs = json.loads(room_data.questions_json)
            if len(qs) > 0:
                emit('new_question', {**qs[0], 'index': 0}, room=request.sid)
        
        elif user_type == 'host':
            # Send current results to host immediately upon joining or refreshing
            emit('update_dashboard', generate_report(room_code), room=request.sid)
    else:
        emit('error_message', {'msg': 'Invalid Room Code!'}, room=request.sid)

@socketio.on('disconnect')
def on_disconnect():
    for room_code, sids in list(active_voters.items()):
        if request.sid in sids:
            sids.remove(request.sid)
            emit('user_count', {'count': len(sids)}, to=room_code)

@socketio.on('submit_vote')
def handle_vote(data):
    room_code = data.get('room')
    q_idx = data.get('current_index')
    next_idx = data.get('next_index')
    v_id = data.get('voter_id')
    answer = data.get('answer')

    # 1. DELETE EXISTING ENTRIES (Clean up old data for this specific user/question)
    VoterRecord.query.filter_by(voter_id=v_id, room_code=room_code, question_index=q_idx).delete()
    Vote.query.filter_by(voter_id=v_id, room_code=room_code, question_index=q_idx).delete()
    
    # 2. SAVE THE NEW VOTE
    new_record = VoterRecord(voter_id=v_id, room_code=room_code, question_index=q_idx)
    new_vote = Vote(room_code=room_code, answer=answer, question_index=q_idx, voter_id=v_id)
    
    db.session.add(new_record)
    db.session.add(new_vote)
    db.session.commit()

    # 3. PUSH UPDATED TOTALS TO HOST
    emit('update_dashboard', generate_report(room_code), to=room_code)

    # 4. ADVANCE VOTER TO NEXT QUESTION
    room_data = Room.query.filter_by(code=room_code).first()
    if room_data:
        qs = json.loads(room_data.questions_json)
        try:
            target_idx = int(next_idx) if next_idx is not None else None
            if target_idx is not None and 0 <= target_idx < len(qs):
                emit('new_question', {**qs[target_idx], 'index': target_idx}, room=request.sid)
            else:
                emit('new_question', None, room=request.sid) # Signal that poll is finished
        except (ValueError, TypeError):
            emit('new_question', None, room=request.sid)

if __name__ == '__main__':
    socketio.run(app, debug=True)