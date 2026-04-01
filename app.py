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

# Tracks unique voter session IDs
active_voters = {}

class Room(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    code = db.Column(db.String(6), unique=True, nullable=False)
    questions_json = db.Column(db.Text, nullable=False) 

class Vote(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    room_code = db.Column(db.String(6), nullable=False)
    answer = db.Column(db.String(100), nullable=False)
    question_index = db.Column(db.Integer)

with app.app_context():
    db.create_all()

@app.route('/')
def index(): return render_template('index.html')

@app.route('/host')
def host(): return render_template('host.html')

@app.route('/vote')
def vote(): return render_template('vote.html')

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

@socketio.on('join')
def on_join(data):
    room_code = data.get('room')
    user_type = data.get('type', 'voter') 
    
    room_data = Room.query.filter_by(code=room_code).first()
    if room_data:
        join_room(room_code)
        if user_type == 'voter':
            if room_code not in active_voters: active_voters[room_code] = set()
            active_voters[room_code].add(request.sid)
            emit('user_count', {'count': len(active_voters[room_code])}, to=room_code)
        
        qs = json.loads(room_data.questions_json)
        if len(qs) > 0:
            emit('new_question', {**qs[0], 'index': 0}, room=request.sid)
    else:
        # Stop the loading spinner if code is wrong
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
    
    # Save to SQLite
    new_vote = Vote(room_code=room_code, answer=data.get('answer'), question_index=q_idx)
    db.session.add(new_vote)
    db.session.commit()

    # Update Host Dashboard
    all_votes = Vote.query.filter_by(room_code=room_code).all()
    report = {}
    for v in all_votes:
        idx_str = str(v.question_index)
        if idx_str not in report: report[idx_str] = {}
        report[idx_str][v.answer] = report[idx_str].get(v.answer, 0) + 1
    emit('update_dashboard', report, to=room_code)

    # BRANCHING LOGIC: Find what to show the voter next
    room_data = Room.query.filter_by(code=room_code).first()
    if room_data:
        qs = json.loads(room_data.questions_json)
        # If there is a valid next index, send the next question
        if next_idx is not None and 0 <= int(next_idx) < len(qs):
            emit('new_question', {**qs[int(next_idx)], 'index': int(next_idx)}, room=request.sid)
        else:
            # Tell the voter they are finished (None triggers the "Thank You" screen)
            emit('new_question', None, room=request.sid)

if __name__ == '__main__':
    socketio.run(app, debug=True)