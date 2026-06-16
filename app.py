from flask import Flask, render_template, Response, request, redirect, url_for, flash, jsonify
import cv2
import numpy as np
import os
import json
import threading
import time
import re
import base64
from datetime import datetime
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.image import MIMEImage

try:
    import winsound
except ImportError:
    winsound = None

from face_engine import FaceEngine

def play_warning_beep():
    if winsound:
        try:
            # Play a double-siren alert beep: 1000Hz then 800Hz
            winsound.Beep(1000, 300)
            winsound.Beep(850, 400)
        except Exception as e:
            print("Server beep failed:", e)

app = Flask(__name__)
app.secret_key = "aura_shield_secret_key"

# Paths
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")
KNOWN_FACES_DIR = os.path.join(DATA_DIR, "known_faces")
INTRUDERS_DIR = os.path.join(DATA_DIR, "intruders")
DB_PATH = os.path.join(DATA_DIR, "db.json")

# Ensure folders exist
os.makedirs(KNOWN_FACES_DIR, exist_ok=True)
os.makedirs(INTRUDERS_DIR, exist_ok=True)

# Global variables
face_engine = FaceEngine(DATA_DIR)
camera = None
lock = threading.Lock()
latest_frame = None

# SSE notification queue
sse_clients = []

# Alert cooling period (in seconds) - avoid spamming alerts for the same intruder
ALERT_COOLDOWN = 15
last_alert_time = 0

def load_db():
    if not os.path.exists(DB_PATH):
        # Create default
        data = {
            "settings": {
                "intruder_detection_enabled": True,
                "recognition_threshold": 45,
                "email_notifications": False,
                "email_smtp_server": "smtp.gmail.com",
                "email_smtp_port": 587,
                "email_sender": "",
                "email_receiver": "",
                "email_password": ""
            },
            "known_faces": [],
            "logs": []
        }
        with open(DB_PATH, 'w') as f:
            json.dump(data, f, indent=2)
        return data
    
    try:
        with open(DB_PATH, 'r') as f:
            return json.load(f)
    except Exception as e:
        print(f"Error loading db.json: {e}")
        return {"settings": {}, "known_faces": [], "logs": []}

def save_db(data):
    try:
        with open(DB_PATH, 'w') as f:
            json.dump(data, f, indent=2)
    except Exception as e:
        print(f"Error saving db.json: {e}")

def send_sse_event(event_type, data):
    """Notify all connected clients via SSE."""
    global sse_clients
    payload = json.dumps({"type": event_type, **data})
    for client in list(sse_clients):
        try:
            client.put(payload)
        except Exception:
            sse_clients.remove(client)

def log_message(msg_type, recipient, content, status):
    """Add a notification dispatch record to db.json."""
    db_data = load_db()
    if "messages" not in db_data:
        db_data["messages"] = []
    
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    db_data["messages"].append({
        "timestamp": timestamp,
        "type": msg_type,
        "recipient": recipient,
        "content": content,
        "status": status
    })
    save_db(db_data)

def send_email_alert(image_path, timestamp):
    """Sends email alert asynchronously with the intruder snapshot."""
    db_data = load_db()
    settings = db_data.get("settings", {})
    
    sender = settings.get("email_sender")
    password = settings.get("email_password")
    receiver = settings.get("email_receiver")
    smtp_server = settings.get("email_smtp_server")
    smtp_port = settings.get("email_smtp_port")

    msg_body = f"🚨 Aura Shield Security Alert: Intruder detected at {timestamp}! Please check your home security camera dashboard immediately."

    if not settings.get("email_notifications"):
        return

    if not sender or not password or not receiver:
        log_message("EMAIL", receiver or "user@domain.com", msg_body, "Simulated")
        print("SMTP Email Configuration missing fields. Logged as Simulated.")
        return

    try:
        msg = MIMEMultipart()
        msg['Subject'] = f"🚨 SECURITY ALERT: Intruder Detected at {timestamp}"
        msg['From'] = sender
        msg['To'] = receiver

        # Body
        body = f"""
        <h3>Aura Shield Intruder Alert</h3>
        <p>An unrecognized face was detected by your Smart Home Security Camera.</p>
        <p><strong>Time:</strong> {timestamp}</p>
        <p>See attached snapshot for identification.</p>
        <br>
        <p>This is an automated message sent by Aura Shield Smart Security.</p>
        """
        msg.attach(MIMEText(body, 'html'))

        # Attachment
        with open(image_path, 'rb') as f:
            img_data = f.read()
        
        image = MIMEImage(img_data, name=os.path.basename(image_path))
        msg.attach(image)

        # Connect and Send
        with smtplib.SMTP(smtp_server, int(smtp_port)) as server:
            server.starttls()
            server.login(sender, password)
            server.send_message(msg)
            
        log_message("EMAIL", receiver, msg_body, "Delivered")
        print(f"Alert email sent successfully to {receiver}")
    except Exception as e:
        log_message("EMAIL", receiver, msg_body, f"Error: {str(e)[:40]}")
        print(f"Failed to send alert email: {e}")

def send_sms_alert(timestamp):
    """Sends SMS alert via Twilio, or simulates it if not configured or twilio package is missing."""
    db_data = load_db()
    settings = db_data.get("settings", {})
    
    if not settings.get("sms_notifications"):
        return
        
    sid = settings.get("twilio_sid")
    token = settings.get("twilio_auth_token")
    from_num = settings.get("twilio_from")
    to_num = settings.get("twilio_to")
    
    msg_content = f"🚨 Aura Shield Security Alert: Intruder detected at {timestamp}! Please check your home security camera dashboard immediately."
    
    if not sid or not token or not from_num or not to_num:
        log_message("SMS", to_num or "+91XXXXXXXXXX", msg_content, "Simulated")
        print("Twilio SMS Configuration missing fields. Logged as Simulated.")
        return
        
    try:
        from twilio.rest import Client as TwilioClient
        client = TwilioClient(sid, token)
        client.messages.create(
            body=msg_content,
            from_=from_num,
            to=to_num
        )
        log_message("SMS", to_num, msg_content, "Delivered")
        print(f"SMS Alert sent successfully to {to_num}")
    except ImportError:
        log_message("SMS", to_num, msg_content + " (twilio module missing)", "Simulated")
        print("Twilio package not installed. Logged as Simulated.")
    except Exception as e:
        log_message("SMS", to_num, msg_content, f"Error: {str(e)[:40]}")
        print(f"Failed to send Twilio SMS: {e}")

def trigger_intruder_alert(frame, timestamp):
    """Save snapshot, update database, and notify via SSE, Email, and SMS."""
    global last_alert_time
    now = time.time()
    
    # Cooldown check
    if now - last_alert_time < ALERT_COOLDOWN:
        return
    
    last_alert_time = now
    
    # Format file path
    filename = f"intruder_{timestamp.replace(':', '-').replace(' ', '_')}.jpg"
    filepath = os.path.join(INTRUDERS_DIR, filename)
    
    # Save frame
    cv2.imwrite(filepath, frame)
    
    # Update DB
    db_data = load_db()
    
    email_active = db_data["settings"].get("email_notifications", False)
    sms_active = db_data["settings"].get("sms_notifications", False)
    
    action = "Logged"
    if email_active and sms_active:
        action = "Logged, Email & SMS Dispatched"
    elif email_active:
        action = "Logged & Email Dispatched"
    elif sms_active:
        action = "Logged & SMS Dispatched"

    # Save log
    log_entry = {
        "timestamp": timestamp,
        "is_intruder": True,
        "name": "Unknown",
        "confidence": 100,
        "image": filename,
        "action": action
    }
    db_data["logs"].append(log_entry)
    save_db(db_data)
    
    # Play warning alarm sound on the server side (Windows host)
    threading.Thread(target=play_warning_beep, daemon=True).start()
    
    # Dispatch Email/SMS in separate threads
    if email_active:
        threading.Thread(target=send_email_alert, args=(filepath, timestamp), daemon=True).start()
    else:
        # If disabled but alert is triggered, log to internal messages that it was bypassed
        log_message("EMAIL", "None", f"Intruder detected at {timestamp}. Email alerts disabled.", "Simulated")

    if sms_active:
        threading.Thread(target=send_sms_alert, args=(timestamp,), daemon=True).start()
    else:
        # If disabled but alert is triggered, log to internal messages that it was bypassed
        log_message("SMS", "None", f"Intruder detected at {timestamp}. SMS alerts disabled.", "Simulated")
    
    # Send SSE push notification
    send_sse_event("intruder_alert", {
        "time": timestamp,
        "image": filename
    })

def trigger_known_face_log(name, confidence, filename, timestamp):
    """Logs the presence of a known family member in db.json."""
    db_data = load_db()
    
    # To prevent logs from flooding, log the same person max once every 2 minutes
    recent_logs = [l for l in db_data["logs"] if l["name"] == name]
    if recent_logs:
        # Check time of the last log
        last_log_time_str = recent_logs[-1]["timestamp"]
        try:
            last_log_dt = datetime.strptime(last_log_time_str, "%Y-%m-%d %H:%M:%S")
            diff = (datetime.now() - last_log_dt).total_seconds()
            if diff < 120:  # 2 minutes cooldown
                return
        except ValueError:
            pass

    log_entry = {
        "timestamp": timestamp,
        "is_intruder": False,
        "name": name,
        "confidence": confidence,
        "image": filename,
        "action": "Authorized Access"
    }
    
    db_data["logs"].append(log_entry)
    save_db(db_data)

    # Log details of the detected authorized face in the message center
    msg_content = f"🔓 Aura Shield Info: Registered user '{name}' was recognized at the front door (Confidence: {confidence}%)."
    log_message("SMS" if db_data["settings"].get("sms_notifications") else "EMAIL", "In-App Console", msg_content, "Simulated")

# Camera Capture Thread
def camera_stream_loop():
    global camera, latest_frame
    
    # Try opening camera 0
    camera = cv2.VideoCapture(0)
    
    # Simulated Camera states (fallbacks)
    use_mock_stream = False
    if not camera.isOpened():
        print("Physical camera not found. Launching Simulated/Mock Camera Feed.")
        use_mock_stream = True
        
    # Variables for mock simulation
    mock_x, mock_y = 100, 100
    mock_dx, mock_dy = 6, 4
    mock_face_type = 0 # 0 = Empty, 1 = Known Person, 2 = Intruder
    state_timer = time.time()
    
    while True:
        timestamp_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        db_data = load_db()
        settings = db_data.get("settings", {})
        detection_enabled = settings.get("intruder_detection_enabled", True)
        threshold = settings.get("recognition_threshold", 60)
        
        if not use_mock_stream:
            # Physical camera stream
            success, frame = camera.read()
            if not success:
                print("Failed to grab physical camera frame. Falling back to simulated stream.")
                use_mock_stream = True
                continue
        else:
            # Simulated stream: Draw a moving face on a dark stylish background
            frame = np.zeros((480, 640, 3), dtype=np.uint8)
            # Create a subtle radial background gradient
            for r in range(480):
                color_val = int(12 + (r/480)*15)
                frame[r, :] = (color_val + 8, color_val, color_val + 18)
            
            # Grid overlay
            for x in range(0, 640, 80):
                cv2.line(frame, (x, 0), (x, 480), (30, 25, 45), 1)
            for y in range(0, 480, 60):
                cv2.line(frame, (0, y), (640, y), (30, 25, 45), 1)
                
            # Camera details HUD
            cv2.putText(frame, "AURA SHIELD SECURITY - SIMULATED FEED", (20, 35), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (139, 92, 246), 1, cv2.LINE_AA)
            cv2.putText(frame, timestamp_str, (20, 60), cv2.FONT_HERSHEY_SIMPLEX, 0.4, (150, 150, 150), 1, cv2.LINE_AA)
            
            # Cycle through simulation states:
            # every 10 seconds: empty room -> known face -> intruder face
            now = time.time()
            if now - state_timer > 10:
                mock_face_type = (mock_face_type + 1) % 3
                state_timer = now
            
            if mock_face_type == 0:
                # Room empty state
                cv2.putText(frame, "STATUS: SECURE (NO MOTION)", (20, 450), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (16, 185, 129), 1)
            else:
                # Face simulation
                mock_x += mock_dx
                mock_y += mock_dy
                
                # Boundary collisions
                if mock_x < 80 or mock_x > 560:
                    mock_dx = -mock_dx
                if mock_y < 80 or mock_y > 400:
                    mock_dy = -mock_dy
                
                # Draw simulated face features (Circle for face, dots for eyes, curved line for mouth)
                cv2.circle(frame, (mock_x, mock_y), 50, (200, 200, 200), -1) # Head
                cv2.circle(frame, (mock_x - 15, mock_y - 12), 6, (40, 40, 40), -1) # Left Eye
                cv2.circle(frame, (mock_x + 15, mock_y - 12), 6, (40, 40, 40), -1) # Right Eye
                cv2.circle(frame, (mock_x, mock_y + 10), 8, (40, 40, 40), 2) # Mouth/Nose
                
                # Detect and recognize the simulated face
                if mock_face_type == 1:
                    # Known person simulation
                    known_users = db_data.get("known_faces", [])
                    name = known_users[0]["name"] if known_users else "Rajeev Kumar"
                    color = (0, 255, 0)
                    cv2.rectangle(frame, (mock_x - 55, mock_y - 55), (mock_x + 55, mock_y + 55), color, 2)
                    cv2.putText(frame, f"{name} (92%)", (mock_x - 55, mock_y - 65), cv2.FONT_HERSHEY_SIMPLEX, 0.5, color, 2)
                    
                    # Log presence of known user
                    user_img = known_users[0]["image"] if known_users else "default.jpg"
                    trigger_known_face_log(name, 92, user_img, timestamp_str)
                else:
                    # Intruder simulation
                    color = (0, 0, 255)
                    cv2.rectangle(frame, (mock_x - 55, mock_y - 55), (mock_x + 55, mock_y + 55), color, 2)
                    cv2.putText(frame, "Intruder (Unknown)", (mock_x - 55, mock_y - 65), cv2.FONT_HERSHEY_SIMPLEX, 0.5, color, 2)
                    
                    if detection_enabled:
                        trigger_intruder_alert(frame, timestamp_str)
                        
        # Apply face recognition engine on physical camera frame
        if not use_mock_stream:
            try:
                annotated_frame, detections = face_engine.detect_and_recognize(frame, threshold)
                
                # Handle detections logic
                for det in detections:
                    if det["is_intruder"]:
                        if detection_enabled:
                            trigger_intruder_alert(frame, timestamp_str)
                    else:
                        # Find filename for known face
                        name = det["name"]
                        filename = f"{name.replace(' ', '_')}.jpg"
                        # Verify physical file exists
                        if not os.path.exists(os.path.join(KNOWN_FACES_DIR, filename)):
                            # Check files in folder
                            files = os.listdir(KNOWN_FACES_DIR)
                            matching = [f for f in files if f.startswith(name.replace(' ', '_'))]
                            filename = matching[0] if matching else "default.jpg"
                            
                        trigger_known_face_log(name, det["confidence"], filename, timestamp_str)
                        
                frame = annotated_frame
            except Exception as e:
                print(f"Error processing frame: {e}")
        
        # Save frame to global latest
        with lock:
            latest_frame = frame.copy()
            
        time.sleep(0.04) # cap around 25 FPS

def generate_video_stream():
    """Generates the MJPEG stream from latest_frame."""
    global latest_frame
    while True:
        with lock:
            if latest_frame is None:
                # Render loading frame
                loading_frame = np.zeros((480, 640, 3), dtype=np.uint8)
                cv2.putText(loading_frame, "CONNECTING TO SURVEILLANCE FEED...", (120, 240), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (139, 92, 246), 2)
                _, buffer = cv2.imencode('.jpg', loading_frame)
            else:
                _, buffer = cv2.imencode('.jpg', latest_frame)
                
        yield (b'--frame\r\n'
               b'Content-Type: image/jpeg\r\n\r\n' + buffer.tobytes() + b'\r\n')
        time.sleep(0.04)

# Web Endpoints
@app.route('/')
def index():
    return redirect(url_for('dashboard'))

@app.route('/dashboard')
def dashboard():
    db_data = load_db()
    known_faces = db_data.get("known_faces", [])
    logs = db_data.get("logs", [])
    settings = db_data.get("settings", {})
    
    # Get statistics
    registered_count = len(known_faces)
    intruder_count = sum(1 for log in logs if log.get("is_intruder"))
    recent_intruders = [log for log in logs if log.get("is_intruder")][-5:]
    recent_intruders.reverse() # Show newest first

    return render_template('dashboard.html',
                           active_page='dashboard',
                           registered_count=registered_count,
                           intruder_count=intruder_count,
                           email_notifications=settings.get("email_notifications", False),
                           recent_intruders=recent_intruders)

@app.route('/video_feed')
def video_feed():
    return Response(generate_video_stream(), mimetype='multipart/x-mixed-replace; boundary=frame')

@app.route('/database')
def database():
    db_data = load_db()
    known_faces = []
    
    # Populate known faces list with image details
    files = os.listdir(KNOWN_FACES_DIR)
    for f in files:
        if f.lower().endswith(('.jpg', '.jpeg', '.png')):
            name = os.path.splitext(f)[0].split('_')[0].replace('-', ' ')
            known_faces.append({
                "name": name,
                "image": f
            })

    return render_template('database.html',
                           active_page='database',
                           known_faces=known_faces)

@app.route('/register_user', methods=['POST'])
def register_user():
    username = request.form.get('username').strip()
    input_mode = request.form.get('input_mode')
    
    if not username:
        flash("Username is required.", "danger")
        return redirect(url_for('database'))
    
    filename = f"{username.replace(' ', '_')}.jpg"
    filepath = os.path.join(KNOWN_FACES_DIR, filename)
    
    try:
        if input_mode == 'upload':
            file = request.files.get('userimage')
            if not file or file.filename == '':
                flash("No image file uploaded.", "danger")
                return redirect(url_for('database'))
            file.save(filepath)
        elif input_mode == 'capture':
            base64_data = request.form.get('captured_image_data')
            if not base64_data:
                flash("Failed to capture snapshot from webcam.", "danger")
                return redirect(url_for('database'))
            
            # Decode base64 image data
            image_data = re.sub('^data:image/.+;base64,', '', base64_data)
            img_bytes = base64.b64decode(image_data)
            
            with open(filepath, 'wb') as f:
                f.write(img_bytes)
        
        # Retrain the face engine with the new image
        retrained = face_engine.train_engine()
        if retrained:
            flash(f"Successfully registered and trained {username}!", "success")
        else:
            flash(f"Registered {username}, but face recognition engine could not find a clear face to train on.", "warning")
            
    except Exception as e:
        flash(f"Error registering user: {str(e)}", "danger")
        
    return redirect(url_for('database'))

@app.route('/delete_user/<filename>', methods=['POST'])
def delete_user(filename):
    filepath = os.path.join(KNOWN_FACES_DIR, filename)
    try:
        if os.path.exists(filepath):
            os.remove(filepath)
            
        # Retrain face recognition
        face_engine.train_engine()
        
        # Sync with database known faces names
        db_data = load_db()
        name_to_remove = os.path.splitext(filename)[0].split('_')[0].replace('-', ' ')
        db_data["known_faces"] = [n for n in db_data["known_faces"] if n != name_to_remove]
        save_db(db_data)
        
        flash("User successfully deleted and recognition retrained.", "success")
    except Exception as e:
        flash(f"Error deleting user: {str(e)}", "danger")
        
    return redirect(url_for('database'))

@app.route('/logs')
def logs():
    db_data = load_db()
    logs_list = db_data.get("logs", [])
    return render_template('logs.html', active_page='logs', logs=logs_list)

@app.route('/clear_logs', methods=['POST'])
def clear_logs():
    db_data = load_db()
    db_data["logs"] = []
    save_db(db_data)
    
    # Delete all photos in the intruders folder
    try:
        for f in os.listdir(INTRUDERS_DIR):
            file_path = os.path.join(INTRUDERS_DIR, f)
            if os.path.isfile(file_path):
                os.remove(file_path)
        flash("Activity history and snapshots cleared.", "success")
    except Exception as e:
        flash(f"Error clearing logs: {str(e)}", "danger")
        
    return redirect(url_for('logs'))

@app.route('/settings')
def settings():
    db_data = load_db()
    return render_template('settings.html', active_page='settings', settings=db_data.get("settings", {}))

@app.route('/save_settings', methods=['POST'])
def save_settings():
    db_data = load_db()
    settings = db_data.get("settings", {})
    
    settings["intruder_detection_enabled"] = 'intruder_detection_enabled' in request.form
    settings["recognition_threshold"] = int(request.form.get('recognition_threshold', 45))
    settings["email_notifications"] = 'email_notifications' in request.form
    settings["email_smtp_server"] = request.form.get('email_smtp_server', '').strip()
    settings["email_smtp_port"] = int(request.form.get('email_smtp_port', 587))
    settings["email_sender"] = request.form.get('email_sender', '').strip()
    settings["email_receiver"] = request.form.get('email_receiver', '').strip()
    
    # If a new password is submitted, update it; otherwise keep the old one
    new_password = request.form.get('email_password', '').strip()
    if new_password:
        settings["email_password"] = new_password

    # SMS settings
    settings["sms_notifications"] = 'sms_notifications' in request.form
    settings["twilio_sid"] = request.form.get('twilio_sid', '').strip()
    settings["twilio_auth_token"] = request.form.get('twilio_auth_token', '').strip()
    settings["twilio_from"] = request.form.get('twilio_from', '').strip()
    settings["twilio_to"] = request.form.get('twilio_to', '').strip()
        
    db_data["settings"] = settings
    save_db(db_data)
    
    flash("Settings updated successfully.", "success")
    return redirect(url_for('settings'))

@app.route('/messages')
def messages_page():
    db_data = load_db()
    messages_list = db_data.get("messages", [])
    return render_template('messages.html', active_page='messages', messages=messages_list)

@app.route('/clear_messages', methods=['POST'])
def clear_messages():
    db_data = load_db()
    db_data["messages"] = []
    save_db(db_data)
    flash("Message dispatch archives cleared.", "success")
    return redirect(url_for('messages_page'))

# Serve images from data folder securely
@app.route('/data/known_faces/<filename>')
def get_known_face_image(filename):
    from flask import send_from_directory
    return send_from_directory(KNOWN_FACES_DIR, filename)

@app.route('/data/intruders/<filename>')
def get_intruder_image(filename):
    from flask import send_from_directory
    return send_from_directory(INTRUDERS_DIR, filename)

# Server-Sent Events endpoint for real-time notifications
@app.route('/event_stream')
def event_stream():
    def event_generator():
        from queue import Queue
        q = Queue()
        sse_clients.append(q)
        try:
            while True:
                # blocks until queue receives message from triggers
                message = q.get()
                yield f"data: {message}\n\n"
        except GeneratorExit:
            sse_clients.remove(q)

    return Response(event_generator(), mimetype="text/event-stream")

# Start the background thread for capturing video frames (outside __main__ to support Gunicorn/Render imports)
capture_thread = threading.Thread(target=camera_stream_loop, daemon=True)
capture_thread.start()

if __name__ == '__main__':
    # Run the web server locally
    app.run(host='0.0.0.0', port=5000, debug=False)
