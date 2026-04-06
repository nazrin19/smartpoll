from flask import Flask, render_template, request, jsonify, redirect, url_for
from flask_socketio import SocketIO, emit, join_room
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, UserMixin, login_user, logout_user, login_required, current_user
from werkzeug.security import generate_password_hash, check_password_hash
import random, string, json, os

app = Flask(__name__)
app.config['SECRET_KEY'] = 'poll-secret-123'
basedir = os.path.abspath(os.path.dirname(__file__))
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///' + os.path.join(basedir, 'poll.db')
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db = SQLAlchemy(app)
socketio = SocketIO(app, cors_allowed_origins="*")

# --- LOGIN MANAGER ---
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login'

@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))

# --- MODELS ---

class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    password_hash = db.Column(db.String(120), nullable=False)
    rooms = db.relationship('Room', backref='owner', lazy=True)

class Room(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    code = db.Column(db.String(6), unique=True, nullable=False)
    questions_json = db.Column(db.Text, nullable=False, default='[]')
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True)

class Vote(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    room_code = db.Column(db.String(6), nullable=False)
    answer = db.Column(db.String(100), nullable=False)
    question_index = db.Column(db.Integer)
    voter_id = db.Column(db.String(100))

class VoterRecord(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    voter_id = db.Column(db.String(100), nullable=False)
    room_code = db.Column(db.String(6), nullable=False)
    question_index = db.Column(db.Integer, nullable=False)

with app.app_context():
    db.create_all()

# --- GLOBALS ---
active_voters = {} # room_code -> set of SIDs

# --- HELPERS ---

def generate_report(room_code):
    all_votes = Vote.query.filter_by(room_code=room_code).all()
    report = {}
    for v in all_votes:
        idx_str = str(v.question_index)
        if idx_str not in report: report[idx_str] = {}
        report[idx_str][v.answer] = report[idx_str].get(v.answer, 0) + 1
    return report

# --- ROUTES ---

@app.route('/')
def index(): return render_template('index.html')

@app.route('/host')
@login_required
def host(): return render_template('host.html')

@app.route('/vote')
def vote(): return render_template('vote.html')

@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        if User.query.filter_by(username=username).first():
            return "User exists", 400
        hashed = generate_password_hash(password, method='pbkdf2:sha256')
        db.session.add(User(username=username, password_hash=hashed))
        db.session.commit()
        return redirect(url_for('login'))
    return render_template('register.html')

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        user = User.query.filter_by(username=request.form.get('username')).first()
        if user and check_password_hash(user.password_hash, request.form.get('password')):
            login_user(user)
            return redirect(url_for('host'))
    return render_template('login.html')

@app.route('/logout')
def logout():
    logout_user()
    return redirect(url_for('index'))

@app.route('/get_room_state/<room_code>')
@login_required # Only logged in users can ask for room states
def get_room_state(room_code):
    room = Room.query.filter_by(code=room_code).first()
    # SECURITY: Check if the logged-in user actually owns this room
    if room and room.user_id == current_user.id:
        return jsonify({
            'questions': json.loads(room.questions_json),
            'report': generate_report(room_code)
        })
    return jsonify({'status': 'error'}), 403

@app.route('/create_room', methods=['POST'])
@login_required
def create_room():
    code = ''.join(random.choices(string.ascii_uppercase + string.digits, k=6))
    new_room = Room(code=code, user_id=current_user.id)
    db.session.add(new_room)
    db.session.commit()
    active_voters[code] = set() 
    return jsonify({'code': code})

@app.route('/start_poll', methods=['POST'])
@login_required
def start_poll():
    data = request.json
    room = Room.query.filter_by(code=data.get('code'), user_id=current_user.id).first()
    if room:
        room.questions_json = json.dumps(data.get('questions'))
        db.session.commit()
        socketio.emit('poll_started', to=room.code)
        return jsonify({'status': 'success'})
    return jsonify({'status': 'error'}), 404

# --- SOCKETS ---

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
            if qs: emit('new_question', {**qs[0], 'index': 0}, room=request.sid)
        elif user_type == 'host':
            emit('update_dashboard', generate_report(room_code), room=request.sid)

@socketio.on('disconnect')
def on_disconnect():
    for room_code, sids in active_voters.items():
        if request.sid in sids:
            sids.remove(request.sid)
            emit('user_count', {'count': len(sids)}, to=room_code)

@socketio.on('submit_vote')
def handle_vote(data):
    room_code, q_idx, v_id, answer = data.get('room'), data.get('current_index'), data.get('voter_id'), data.get('answer')
    VoterRecord.query.filter_by(voter_id=v_id, room_code=room_code, question_index=q_idx).delete()
    Vote.query.filter_by(voter_id=v_id, room_code=room_code, question_index=q_idx).delete()
    db.session.add(VoterRecord(voter_id=v_id, room_code=room_code, question_index=q_idx))
    db.session.add(Vote(room_code=room_code, answer=answer, question_index=q_idx, voter_id=v_id))
    db.session.commit()
    emit('update_dashboard', generate_report(room_code), to=room_code)
    
    room_data = Room.query.filter_by(code=room_code).first()
    if room_data:
        qs = json.loads(room_data.questions_json)
        try:
            next_idx = int(data.get('next_index'))
            if 0 <= next_idx < len(qs):
                emit('new_question', {**qs[next_idx], 'index': next_idx}, room=request.sid)
            else: emit('new_question', None, room=request.sid)
        except: emit('new_question', None, room=request.sid)

if __name__ == '__main__':
    socketio.run(app, debug=True)