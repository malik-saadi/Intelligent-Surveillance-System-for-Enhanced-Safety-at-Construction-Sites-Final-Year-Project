import os
import time
import base64
from flask import Flask, request, jsonify
from flask_cors import CORS
from deepface import DeepFace
import torch

# ── DEVICE DETECTION (GPU/CPU) ────────────────────────────────────────────────
print("\n🔍 Checking Hardware...")
gpu_available = torch.cuda.is_available()

if gpu_available:
    DEVICE = 0
    print(f"✅ GPU DETECTED: {torch.cuda.get_device_name(0)}")
    print("🚀 Face Recognition will use GPU acceleration.")
    
    # Force TensorFlow to use GPU
    import tensorflow as tf
    gpus = tf.config.list_physical_devices('GPU')
    if gpus:
        try:
            for gpu in gpus:
                tf.config.experimental.set_memory_growth(gpu, True)
            print(f"✅ TENSORFLOW GPU ENABLED: {len(gpus)} device(s) found.")
        except RuntimeError as e:
            print(f"ℹ️ TensorFlow GPU initialization error: {e}")
    else:
        print("ℹ️ TENSORFLOW GPU NOT FOUND. Using CPU for recognition.")
else:
    DEVICE = 'cpu'
    print("ℹ️ NO GPU DETECTED. Face Recognition will use CPU.")
print("────────────────────────────────────────────────\n")

app = Flask(__name__)
CORS(app)

# Silence Flask logging for a cleaner terminal
import logging
log = logging.getLogger('werkzeug')
log.setLevel(logging.ERROR)

# Absolute path so it works no matter where the script is launched from
DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'employees')
MATCH_THRESHOLD = 0.35

def get_confidence(distance):
    return max(0, round((1 - distance) * 100, 1))

@app.route('/recognize', methods=['POST'])
def recognize_face():
    try:
        data = request.json
        if not data or 'image' not in data:
            return jsonify({"error": "No image data provided"}), 400

        # Decode base64 image
        image_data = data['image']
        # Remove data URI prefix if present
        if ',' in image_data:
            image_data = image_data.split(',')[1]
            
        img_bytes = base64.b64decode(image_data)
        temp_img_path = "temp_auth_image.jpg"
        
        with open(temp_img_path, "wb") as f:
            f.write(img_bytes)

        # Run DeepFace recognition (no explicit detector_backend so cache matches CCTV)
        result = DeepFace.find(
            img_path=temp_img_path,
            db_path=DB_PATH,
            model_name="Facenet512",
            enforce_detection=False,
            silent=True
        )

        # Cleanup temp file
        if os.path.exists(temp_img_path):
            os.remove(temp_img_path)

        if result and len(result[0]) > 0:
            match = result[0].iloc[0]
            distance = match["distance"]
            
          
            full_dir = os.path.dirname(match["identity"])
            worker_id = os.path.basename(full_dir)
            
            confidence = get_confidence(distance)

            # Check threshold
            if distance < MATCH_THRESHOLD:
                return jsonify({
                    "status": "match",
                    "worker_id": worker_id,
                    "confidence": confidence,
                    "distance": distance
                })
            else:
                return jsonify({
                    "status": "unknown",
                    "message": "Face detected, but unverified."
                })
        else:
            return jsonify({"status": "no_face", "message": "No face detected in image"})

    except Exception as e:
        if os.path.exists("temp_auth_image.jpg"):
            os.remove("temp_auth_image.jpg")
        return jsonify({"error": str(e)}), 500


if __name__ == '__main__':
    # ── FULL DATABASE PRE-BUILD ───────────────────────────────────────────────
    total_images = 0
    for root, dirs, files in os.walk(DB_PATH):
        for f in files:
            if f.lower().endswith(('.jpg', '.jpeg', '.png')):
                total_images += 1

    print(f"🔄 Initializing Employee Database...")

    start_time = time.time()
    try:
        import numpy as np
        dummy = np.zeros((160, 160, 3), dtype=np.uint8)
        DeepFace.find(img_path=dummy, db_path=DB_PATH, model_name="Facenet512", enforce_detection=False, silent=True)

        elapsed = time.time() - start_time
        print(f"✅ ATTENDANCE READY: Face Database ({total_images} employees) loaded.")
        print(f"🚀 Auth API is now active on http://127.0.0.1:5000")
    except Exception as e:
        print(f"[WARN] Pre-build issue: {e}")

    app.run(host='0.0.0.0', port=5000, debug=False)
