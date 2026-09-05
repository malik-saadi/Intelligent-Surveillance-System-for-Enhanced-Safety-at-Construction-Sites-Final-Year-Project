import os
# Prevent TensorFlow (DeepFace) from hugging all VRAM, leaving space for PyTorch (YOLO)
os.environ['TF_FORCE_GPU_ALLOW_GROWTH'] = 'true'
import cv2
import time
import collections
import threading
import traceback
import subprocess
import numpy as np
from flask import Flask, Response, jsonify, request
from flask_cors import CORS
from ultralytics import YOLO
from deepface import DeepFace
import torch
import urllib.request
import json
import uuid

# ── DEVICE DETECTION (GPU/CPU) ────────────────────────────────────────────────
if torch.cuda.is_available():
    DEVICE = 0
    torch.backends.cudnn.benchmark = True  # Optimize for fixed input sizes
    print(f"✅ GPU DETECTED: {torch.cuda.get_device_name(0)}. Using GPU for AI.")
else:
    DEVICE = 'cpu'
    print("ℹ️ NO GPU DETECTED. Using CPU (this may be slower).")

MODEL_PATH = 'best.pt'
PORT = 5001
FFMPEG_PATH = r"C:\Users\Malik Saad Rafiq\Desktop\ffmpeg-8.1.1-essentials_build\bin\ffmpeg.exe"

# Absolute path to employees folder so DeepFace always finds it regardless of where the script is launched from
EMPLOYEES_DB = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'employees')

# IMOU CAMERA CONFIGURATION
# Replace the URL below with your actual camera RTSP link
# Format: rtsp://admin:PASSWORD@IP_ADDRESS:554/cam/realmonitor?channel=1&subtype=0

# IMOU_CAMERA_URL = "rtsp://admin:L2C9A3C5@192.168.137.187:554/cam/realmonitor?channel=1&subtype=0"

# --- CAMERA SELECTION --- 
# Defaults to None. Will wait for the frontend to specify the source.
CAMERA_SOURCE = None

VIOLATIONS = {
    'NO-Hardhat', 'NO-Gloves', 'NO-Mask',
    'NO-Goggles', 'NO-Safety Vest', 'Fall-Detected'
}

# --- VIOLATION COOLDOWN ---
# Avoid spamming backend with requests for every frame
VIOLATION_COOLDOWN = {}
COOLDOWN_PERIOD = 60.0  # seconds

def report_violation_to_backend(worker_name, yolo_cls):
    try:
        mapping = {
            'NO-Hardhat': ('helmet', 'high', 500),
            'NO-Safety Vest': ('vest', 'medium', 300),
            'NO-Gloves': ('gloves', 'low', 200),
            'NO-Mask': ('no-ppe', 'low', 200),
            'NO-Goggles': ('no-ppe', 'low', 200),
            'Fall-Detected': ('reckless-behavior', 'high', 1000)
        }
        if yolo_cls not in mapping:
            return
            
        viol_type, severity, fine_amt = mapping[yolo_cls]
        viol_id = "V" + str(uuid.uuid4().int)[:6]
        
        payload = {
            "violation_id": viol_id,
            "worker_name": worker_name,
            "violation_type": viol_type,
            "severity": severity,
            "camera_id": None,
            "fine_amount": fine_amt,
            "snapshot_path": ""
        }
        
        req = urllib.request.Request(
            'http://127.0.0.1:4000/api/violations',
            data=json.dumps(payload).encode('utf-8'),
            headers={'Content-Type': 'application/json'},
            method='POST'
        )
        with urllib.request.urlopen(req, timeout=3) as response:
            if response.status == 201:
                print(f"[AI VIOLATION LOGGED] Successfully sent {viol_type} for {worker_name} to database (PKR {fine_amt}).")
            else:
                print(f"[AI VIOLATION ERROR] Node server status: {response.status}")
    except Exception as e:
        print(f"[AI VIOLATION POST FAILED] Error: {e}")

COLOR_VIOLATION = (0, 0, 255)
COLOR_SAFE = (0, 200, 80)
COLOR_INFO = (255, 255, 255)
FONT = cv2.FONT_HERSHEY_SIMPLEX

# ── INITIALIZATION ────────────────────────────────────────────────────────────
app = Flask(__name__)
CORS(app)

# Silence Flask logging for a cleaner terminal
import logging
log = logging.getLogger('werkzeug')
log.setLevel(logging.ERROR)

print(f"[INFO] Loading YOLO model: {MODEL_PATH}")
yolo_model = YOLO(MODEL_PATH)
yolo_model.overrides['verbose'] = False

global_frame = None
latest_detections = []
metrics = {
    "safe_count": 0,
    "viol_count": 0,
    "current_fps": 0.0
}

# ── PER-PERSON FACE TRACKING ──────────────────────────────────────────────────
face_recognition_active = False
recognized_faces = []  # [{"cx": int, "cy": int, "name": str, "time": float}]
FACE_CACHE_TIMEOUT = 10.0  # seconds before a cached identity expires
FACE_MATCH_RADIUS  = 500   # pixels — max distance to reuse a cached name (large for body violations)

def find_cached_name(cx, cy):
    """Find the closest cached identity near pixel position (cx, cy)."""
    now = time.time()
    best_name = "Unknown"
    best_dist = FACE_MATCH_RADIUS
    for entry in recognized_faces:
        if now - entry["time"] > FACE_CACHE_TIMEOUT:
            continue
        dist = abs(entry["cx"] - cx) + abs(entry["cy"] - cy)
        if dist < best_dist:
            best_dist = dist
            best_name = entry["name"]
    return best_name

def update_face_cache(cx, cy, name):
    """Insert or update a cached identity at pixel position (cx, cy)."""
    now = time.time()
    for entry in recognized_faces:
        dist = abs(entry["cx"] - cx) + abs(entry["cy"] - cy)
        if dist < FACE_MATCH_RADIUS:
            entry["name"] = name
            entry["cx"] = cx
            entry["cy"] = cy
            entry["time"] = now
            return
    recognized_faces.append({"cx": cx, "cy": cy, "name": name, "time": now})
    # Purge stale entries
    recognized_faces[:] = [e for e in recognized_faces if now - e["time"] < FACE_CACHE_TIMEOUT * 2]

def find_any_known_person():
    """Return the name of ANY known (non-Unknown) person currently in the scene."""
    now = time.time()
    for entry in recognized_faces:
        if now - entry["time"] > FACE_CACHE_TIMEOUT:
            continue
        if entry["name"] and entry["name"] != "Unknown":
            return entry["name"]
    return None

def run_deepface(crop, cx, cy):
    global face_recognition_active
    try:
        df_result = DeepFace.find(img_path=crop, db_path=EMPLOYEES_DB, model_name="Facenet512",
                                  distance_metric="cosine", detector_backend='opencv', enforce_detection=True, silent=True)
        if df_result and len(df_result[0]) > 0:
            match = df_result[0].iloc[0]
            dist = match["distance"]
            print(
                f"[FACE] Best match distance: {dist:.4f} → {match['identity']}")
            if dist < 0.50:  # Relaxed threshold (from 0.45) for better hat/helmet tolerance
                full_dir = os.path.dirname(match["identity"])
                name = os.path.basename(full_dir)
                update_face_cache(cx, cy, name)
                print(f"[FACE] Recognized: {name}")
            else:
                update_face_cache(cx, cy, "Unknown")
                print(
                    f"[FACE] Match found but distance {dist:.4f} > threshold 0.45 → Unknown")
        else:
            print("[FACE] No match found in employees DB")
    except Exception as e:
        # Ignore common "no face detected" errors to keep terminal clean
        if "Face could not be detected" not in str(e):
            print(f"[FACE ERROR] {e}")
    finally:
        face_recognition_active = False


def background_ai_worker():
    global global_frame, latest_detections, metrics, face_recognition_active, recognized_faces
    print("[INFO] AI Background Thread Started...")

    print(f"[INFO] Employees DB path: {EMPLOYEES_DB}")
    print(f"[INFO] DB exists: {os.path.isdir(EMPLOYEES_DB)}")

    # ── FULL DATABASE PRE-BUILD ───────────────────────────────────────────────
    # Count total images so the user knows what to expect
    total_images = 0
    for root, dirs, files in os.walk(EMPLOYEES_DB):
        for f in files:
            if f.lower().endswith(('.jpg', '.jpeg', '.png')):
                total_images += 1

    print(f"")
    print(f"╔══════════════════════════════════════════════════════╗")
    print(
        f"║  LOADING FACE DATABASE — {total_images} images to process       ║")
    print(f"║  This may take a few minutes on first run...        ║")
    print(f"║  Please wait. Camera will start after this.         ║")
    print(f"╚══════════════════════════════════════════════════════╝")
    print(f"")

    start_time = time.time()
    try:
        # This single call forces DeepFace to:
        # 1. Load the Facenet512 neural network
        # 2. Scan every image in the employees folder
        # 3. Build and save the .pkl cache file
        import numpy as np
        dummy = np.zeros((160, 160, 3), dtype=np.uint8)
        DeepFace.find(img_path=dummy, db_path=EMPLOYEES_DB,
                      model_name="Facenet512", enforce_detection=False, silent=True)

        elapsed = time.time() - start_time
        print(f"✅ SYSTEM READY: PPE Model and Face Database ({total_images} employees) loaded.")
        print(f"🚀 Monitoring is now active on http://127.0.0.1:{PORT}")
    except Exception as e:
        elapsed = time.time() - start_time
        print(f"[WARN] Database pre-build issue ({elapsed:.1f}s): {e}")
        print(f"[INFO] Will attempt to build cache on first face scan instead.")

    while True:
        if global_frame is None:
            time.sleep(0.05)
            continue

        frame_copy = global_frame.copy()

        try:
            # 1. Run YOLO (Extreme sensitivity mode)
            # device=DEVICE : Automatically uses GPU if available
            # half=True : Use FP16 for much faster GPU inference
            # imgsz=960 : Larger size helps detect small PPE on distant CCTV people
            # conf=0.15 : Balanced confidence for surveillance
            results = yolo_model(frame_copy, imgsz=960,
                                 conf=0.15, iou=0.7, agnostic_nms=True, 
                                 device=DEVICE, half=(DEVICE == 0), verbose=False)

            new_detections = []
            safe_cnt = 0
            viol_cnt = 0
            
            # For debugging: collect what we found this frame
            found_classes = []

            for result in results:
                for box in result.boxes:
                    cls_name = yolo_model.names[int(box.cls)]
                    conf_val = float(box.conf)
                    x1, y1, x2, y2 = map(int, box.xyxy[0])
                    
                    found_classes.append(cls_name)

                    is_viol = cls_name in VIOLATIONS
                    if is_viol:
                        viol_cnt += 1
                    else:
                        safe_cnt += 1

                    is_head = cls_name in [
                        "Person", "NO-Hardhat", "Hardhat", "Mask", "NO-Mask"]

                    # 2. Per-person Face Recognition
                    box_cx = (x1 + x2) // 2
                    box_cy = (y1 + y2) // 2

                    if is_head and not face_recognition_active:
                        cached = find_cached_name(box_cx, box_cy)
                        if cached == "Unknown":
                            # Only scan faces we haven't identified yet
                            # Increase padding significantly so hats don't cut off the face
                            h, w = frame_copy.shape[:2]
                            padding = 60
                            px1, py1 = max(0, x1 - padding), max(0, y1 - padding)
                            px2, py2 = min(w, x2 + padding), min(h, y2 + padding)

                            crop = frame_copy[py1:py2, px1:px2]
                            if crop.size > 0:
                                face_recognition_active = True
                                threading.Thread(target=run_deepface, args=(
                                    crop, box_cx, box_cy), daemon=True).start()

                    # Each head box or violation gets its own name from the spatial cache
                    name_to_display = find_cached_name(box_cx, box_cy) if (is_head or is_viol) else ""

                    # Automatically report violation to backend database
                    if is_viol:
                        # First try spatial match, then fall back to any known person in scene
                        person_name = name_to_display if (name_to_display and name_to_display != "Unknown") else find_any_known_person()
                        
                        if person_name:
                            now_time = time.time()
                            last_logged = VIOLATION_COOLDOWN.get((person_name, cls_name), 0.0)
                            if now_time - last_logged > COOLDOWN_PERIOD:
                                VIOLATION_COOLDOWN[(person_name, cls_name)] = now_time
                                print(f"[AI DETECT] Violation '{cls_name}' linked to person '{person_name}' — reporting to DB...")
                                threading.Thread(
                                    target=report_violation_to_backend,
                                    args=(person_name, cls_name),
                                    daemon=True
                                ).start()
                        else:
                            print(f"[AI DETECT] Violation '{cls_name}' detected but no recognized person in scene to link it to.")

                    new_detections.append(
                        (cls_name, conf_val, x1, y1, x2, y2, is_viol, name_to_display))

            # detections logged only if needed for debugging

            # Atomic update for thread safety
            latest_detections = new_detections
            metrics["safe_count"] = safe_cnt
            metrics["viol_count"] = viol_cnt

        except Exception as e:
            print("[ERROR] AI Thread Exception:", e)
            traceback.print_exc()
            time.sleep(1)  # Prevent tight crash loop

        # Give CPU a breather. Targeting ~10-15 FPS for AI processing.
        time.sleep(0.05)



ai_thread = threading.Thread(target=background_ai_worker, daemon=True)
ai_thread.start()




def camera_worker():
    global global_frame, CAMERA_SOURCE

    current_source = None
    cap = None
    pipe = None

    fail_count = 0
    frame_size = 0
    frame_width = 0
    frame_height = 0
    is_rtsp = False

    while True:
        if current_source != CAMERA_SOURCE or (pipe is None and cap is None):
            # Clear global frame instantly so generator shows connecting placeholder
            if current_source != CAMERA_SOURCE:
                global_frame = None

            # Cleanup old connection
            if cap:
                cap.release()
                cap = None
            if pipe:
                try: pipe.kill()
                except: pass
                pipe = None

            current_source = CAMERA_SOURCE
            
            if current_source == 'none' or current_source is None:
                global_frame = None
                time.sleep(1)
                continue
                
            # If we have no active stream, wait a bit and try to (re)connect
            if pipe is None and cap is None and current_source is not None:
                print(f"[INFO] No active stream. Retrying connection to {CAMERA_SOURCE} in 2s...")
                time.sleep(2)

            # Cleanup old connection
            if cap:
                cap.release()
                cap = None
            if pipe:
                try: pipe.kill()
                except: pass
                pipe = None

            current_source = CAMERA_SOURCE
            is_rtsp = isinstance(current_source, str) and current_source.startswith("rtsp")

            if is_rtsp:
                # 1280x720 Native Resolution for better detail at a distance
                frame_width, frame_height = 1280, 720
                frame_size = frame_width * frame_height * 3

                ffmpeg_cmd = [
                    FFMPEG_PATH,
                    '-loglevel', 'error',
                    '-rtsp_transport', 'tcp',
                    '-timeout', '5000000',
                    '-fflags', 'nobuffer',
                    '-flags', 'low_delay',
                    '-nostdin',
                    '-i', current_source,
                    '-vf', 'scale=1280:720',
                    '-f', 'image2pipe',
                    '-pix_fmt', 'bgr24',
                    '-vcodec', 'rawvideo',
                    '-an', '-'
                ]

                print(f"✅ CRYSTAL-SMOOTH FEED ACTIVE: FFmpeg is processing the camera stream.")
                try:
                    import queue
                    frame_queue = queue.Queue(maxsize=1)
                    pipe = subprocess.Popen(
                        ffmpeg_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, bufsize=frame_size * 2)
                    
                    # Background thread to log FFmpeg errors
                    def logger():
                        while pipe and pipe.poll() is None:
                            line = pipe.stderr.readline()
                            if line:
                                print(f"[FFMPEG] {line.decode(errors='ignore').strip()}")
                            else: break
                    threading.Thread(target=logger, daemon=True).start()

                    # Background thread to read from pipe
                    def reader():
                        while pipe and pipe.poll() is None:
                            try:
                                data = pipe.stdout.read(frame_size)
                                if not data or len(data) != frame_size: break
                                if frame_queue.full():
                                    try: frame_queue.get_nowait()
                                    except: pass
                                frame_queue.put(data)
                            except: break
                    threading.Thread(target=reader, daemon=True).start()
                    
                    # Wait longer (5s) for RTSP handshake over hotspot
                    time.sleep(5)
                    if frame_queue.empty():
                        if pipe.poll() is not None:
                            print(f"[ERROR] FFmpeg crashed on startup.")
                        else:
                            print(f"[WARN] FFmpeg is running but not receiving video. Retrying...")
                            pipe.kill()
                        pipe = None
                        
                        print(f"[INFO] Attempting OpenCV fallback...")
                        cap = cv2.VideoCapture(current_source)
                        if not cap.isOpened(): cap = None
                except Exception as e:
                    print(f"[WARN] FFmpeg Launch Error: {e}. Falling back to OpenCV...")
                    pipe = None
                    cap = cv2.VideoCapture(current_source)
                    if not cap.isOpened(): cap = None
            else:
                print(f"[INFO] Launching Local Webcam Capture (Device {current_source})...")
                cap = cv2.VideoCapture(current_source, cv2.CAP_DSHOW)
                if not cap or not cap.isOpened():
                    cap = cv2.VideoCapture(current_source)
                cap.set(cv2.CAP_PROP_FRAME_WIDTH, 1280)
                cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 720)
                if not cap.isOpened(): cap = None

        # --- DATA CONSUMPTION ---
        if pipe:
            try:
                # Get the latest frame from queue with a short timeout
                import queue
                raw_image = frame_queue.get(timeout=0.1)
                frame = np.frombuffer(raw_image, dtype='uint8').reshape((frame_height, frame_width, 3)).copy()
                global_frame = frame
            except queue.Empty:
                # If pipe is alive but queue is empty, just wait
                if pipe.poll() is not None:
                    print("[INFO] FFmpeg process ended.")
                    pipe = None
            except Exception as e:
                print(f"[ERROR] Frame processing error: {e}")
                pipe = None

        elif cap and cap.isOpened():
            ret, frame = cap.read()
            if ret and frame is not None:
                global_frame = frame
            else:
                time.sleep(0.01)
        else:
            time.sleep(0.1)

cam_thread = threading.Thread(target=camera_worker, daemon=True)
cam_thread.start()


def generate_frames():
    global global_frame, latest_detections, metrics

    fps_deque = collections.deque(maxlen=30)
    prev_time = time.time()

    while True:
        if global_frame is None:
            # Generate a black placeholder image with text
            placeholder = np.zeros((720, 1280, 3), dtype=np.uint8)
            
            if CAMERA_SOURCE is None or CAMERA_SOURCE == 'none':
                text = "NO CAMERA FOUND - ADD CAMERA"
                color = (0, 0, 255) # Red
            else:
                text = "CONNECTING TO CAMERA..."
                color = (0, 200, 255) # Yellow

            # Center the text
            (tw, th), _ = cv2.getTextSize(text, FONT, 1.5, 3)
            cv2.putText(placeholder, text, ((1280 - tw) // 2, 360), FONT, 1.5, color, 3, cv2.LINE_AA)
            
            ret, buffer = cv2.imencode('.jpg', placeholder, [int(cv2.IMWRITE_JPEG_QUALITY), 60])
            if ret:
                yield (b'--frame\r\n'
                       b'Content-Type: image/jpeg\r\n\r\n' + buffer.tobytes() + b'\r\n')
            time.sleep(0.5)
            continue

        frame = global_frame.copy()
        current_dets = list(latest_detections)

        for (cls_name, conf_val, x1, y1, x2, y2, is_viol, person_name) in current_dets:
            color = COLOR_VIOLATION if is_viol else COLOR_SAFE

            # Draw Box
            cv2.rectangle(frame, (x1, y1), (x2, y2), color, 2)

            # Draw Label
            label = f"[{person_name}] {cls_name} {conf_val:.2f}"
            (tw, th), _ = cv2.getTextSize(label, FONT, 0.55, 1)
            cv2.rectangle(frame, (x1, y1-th-8), (x1+tw+4, y1), color, -1)
            cv2.putText(frame, label, (x1+2, y1-4), FONT,
                        0.55, (0, 0, 0), 1, cv2.LINE_AA)

        # FPS Calculation
        now = time.time()
        fps_deque.append(1.0 / max(now - prev_time, 1e-6))
        fps = sum(fps_deque) / len(fps_deque)
        prev_time = now

        # Overlay Metrics
        cv2.putText(frame, f"Stream FPS: {fps:.1f}",
                    (10, 25), FONT, 0.65, COLOR_INFO, 2, cv2.LINE_AA)
        cv2.putText(frame, f"Safe:{metrics['safe_count']}  Violations:{metrics['viol_count']}",
                    (10, 55), FONT, 0.6, COLOR_INFO, 1, cv2.LINE_AA)

        # Bottom Banner
        if metrics['viol_count'] > 0:
            cv2.rectangle(
                frame, (0, frame.shape[0]-40), (frame.shape[1], frame.shape[0]), COLOR_VIOLATION, -1)
            cv2.putText(frame, f"  !! VIOLATION DETECTED: {metrics['viol_count']}",
                        (10, frame.shape[0]-12), FONT, 0.7, (255, 255, 255), 2, cv2.LINE_AA)
        else:
            cv2.rectangle(
                frame, (0, frame.shape[0]-40), (frame.shape[1], frame.shape[0]), COLOR_SAFE, -1)
            cv2.putText(frame, "  ALL PPE OK",
                        (10, frame.shape[0]-12), FONT, 0.7, (0, 0, 0), 2, cv2.LINE_AA)

        # Broadcast immediately! JPEG quality 65-70 is plenty for monitoring and saves hotspot bandwidth
        ret, buffer = cv2.imencode(
            '.jpg', frame, [int(cv2.IMWRITE_JPEG_QUALITY), 65])
        if ret:
            frame_bytes = buffer.tobytes()
            yield (b'--frame\r\n'
                   b'Content-Type: image/jpeg\r\n\r\n' + frame_bytes + b'\r\n')

        # Control the output stream frame rate slightly so we don't bombard the browser
        time.sleep(1/30.0)




@app.route('/video_feed')
def video_feed():
    return Response(generate_frames(), mimetype='multipart/x-mixed-replace; boundary=frame')

@app.route('/status', methods=['GET'])
def get_status():
    """Returns current AI detection status — polled by the browser every second."""
    current_dets = list(latest_detections)
    detected_classes = [d[0] for d in current_dets]
    fall_detected = 'Fall-Detected' in detected_classes
    return jsonify({
        "fall_detected": fall_detected,
        "safe_count": metrics["safe_count"],
        "viol_count": metrics["viol_count"],
        "detected_classes": list(set(detected_classes))
    })

@app.route('/set_camera', methods=['POST'])
def set_camera():
    global CAMERA_SOURCE
    data = request.get_json()
    source = data.get('source', 0)
    
    if isinstance(source, str):
        source = source.strip().strip('"').strip("'")
    
    if source == 'imou':
        CAMERA_SOURCE = IMOU_CAMERA_URL
    elif source == 'none' or source == '':
        CAMERA_SOURCE = None
    else:
        try:
            CAMERA_SOURCE = int(source)
        except (ValueError, TypeError):
            CAMERA_SOURCE = source
            
    print(f"[INFO] Camera source changed to: {CAMERA_SOURCE}")
    return jsonify({"status": "ok", "camera": source})

@app.route('/get_camera', methods=['GET'])
def get_camera():
    return jsonify({"camera": CAMERA_SOURCE})


if __name__ == '__main__':

    print(f"🚀 Intelligent CCTV Server starting on port {PORT}")

    app.run(host='0.0.0.0', port=PORT, threaded=True)
