"""
camera_stream.py
────────────────
A lightweight MJPEG streaming server for the CCTV camera.
NO AI, NO PPE detection, NO YOLO, NO DeepFace.
Just clean raw frames from the RTSP camera, streamed to the browser.

Port: 5002  |  Run: python camera_stream.py
"""

import cv2
import time
import queue
import threading
import subprocess
import numpy as np
from flask import Flask, Response, jsonify
from flask_cors import CORS
import logging

# ── CONFIG ────────────────────────────────────────────────────────────────────
RTSP_URL    = "rtsp://admin:L2C9A3C5@192.168.137.155:554/cam/realmonitor?channel=1&subtype=0"
FFMPEG_PATH = r"C:\Users\Malik Saad Rafiq\Desktop\ffmpeg-8.1.1-essentials_build\bin\ffmpeg.exe"
PORT        = 5002

FRAME_WIDTH  = 1280
FRAME_HEIGHT = 720
FRAME_SIZE   = FRAME_WIDTH * FRAME_HEIGHT * 3  # raw BGR bytes per frame

# ── FLASK ─────────────────────────────────────────────────────────────────────
app = Flask(__name__)
CORS(app)
logging.getLogger('werkzeug').setLevel(logging.ERROR)

# ── FRAME QUEUE (maxsize=1 → always the freshest frame) ──────────────────────
frame_queue = queue.Queue(maxsize=1)

def push_frame(frame):
    """Drop the old frame, push the new one — never accumulate a backlog."""
    try:
        frame_queue.get_nowait()
    except queue.Empty:
        pass
    try:
        frame_queue.put_nowait(frame)
    except queue.Full:
        pass


# ── CAMERA WORKER ─────────────────────────────────────────────────────────────
def camera_worker():
    while True:
        pipe = None
        cap  = None

        # ── FFmpeg — buffered pipe + dedicated reader thread ──────────────────
        # bufsize=FRAME_SIZE*2 ensures read() always gets full frames on Windows.
        # A separate reader thread drains the pipe continuously so it never fills up.
        ffmpeg_cmd = [
            FFMPEG_PATH,
            '-loglevel',        'error',
            '-rtsp_transport',  'tcp',
            '-timeout',         '5000000',
            '-fflags',          'nobuffer+discardcorrupt',
            '-flags',           'low_delay',
            '-nostdin',
            '-i',               RTSP_URL,
            '-vf',              f'scale={FRAME_WIDTH}:{FRAME_HEIGHT}',
            '-f',               'image2pipe',
            '-pix_fmt',         'bgr24',
            '-vcodec',          'rawvideo',
            '-an', '-'
        ]

        try:
            print("[INFO] Connecting to CCTV camera via FFmpeg…")
            pipe = subprocess.Popen(
                ffmpeg_cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                bufsize=FRAME_SIZE * 2   # ← must be buffered on Windows
            )

            # Drain stderr silently in background
            def _drain_stderr(p):
                while p and p.poll() is None:
                    line = p.stderr.readline()
                    if line:
                        txt = line.decode(errors='ignore').strip()
                        if txt:
                            print(f"[FFMPEG] {txt}")
                    else:
                        break
            threading.Thread(target=_drain_stderr, args=(pipe,), daemon=True).start()

            # ── Dedicated pipe reader thread ───────────────────────────────────
            # Runs at full camera FPS, drains stdout, pushes to queue.
            # This is the key fix: the read loop is separate from the generator,
            # so the pipe never backs up and frames are always fresh.
            pipe_alive = threading.Event()
            pipe_alive.set()

            def _pipe_reader(p):
                while p.poll() is None:
                    try:
                        raw = p.stdout.read(FRAME_SIZE)
                    except Exception:
                        break
                    if not raw or len(raw) != FRAME_SIZE:
                        break
                    frame = np.frombuffer(raw, dtype='uint8').reshape(
                        (FRAME_HEIGHT, FRAME_WIDTH, 3)).copy()
                    push_frame(frame)
                pipe_alive.clear()      # signal that this pipe is dead

            reader_thread = threading.Thread(target=_pipe_reader, args=(pipe,), daemon=True)

            # Wait for RTSP handshake before starting reader
            time.sleep(2)

            if pipe.poll() is not None:
                print("[WARN] FFmpeg exited during handshake. Trying OpenCV…")
                pipe = None
            else:
                reader_thread.start()
                print(f"✅ Camera stream LIVE — http://127.0.0.1:{PORT}/video_feed")

        except Exception as e:
            print(f"[WARN] FFmpeg failed: {e}. Trying OpenCV…")
            pipe = None

        # ── OpenCV fallback ────────────────────────────────────────────────────
        if pipe is None:
            try:
                cap = cv2.VideoCapture(RTSP_URL)
                if not cap.isOpened():
                    raise RuntimeError("Cannot open RTSP stream")
                print(f"✅ Camera stream LIVE via OpenCV — port {PORT}")
            except Exception as e:
                print(f"[ERROR] Camera unreachable: {e}. Retrying in 5s…")
                time.sleep(5)
                continue

        # ── Wait loop — monitor for pipe/cap dying ─────────────────────────────
        if pipe:
            # Just wait for the reader thread to finish (pipe died)
            while pipe_alive.is_set():
                time.sleep(0.5)
            print("[INFO] FFmpeg pipe closed. Reconnecting…")
            try: pipe.kill()
            except: pass

        elif cap:
            while True:
                ret, frame = cap.read()
                if not ret or frame is None:
                    print("[WARN] OpenCV read failed. Reconnecting…")
                    break
                push_frame(frame)
            cap.release()

        print("[INFO] Reconnecting in 3s…")
        time.sleep(3)


# ── MJPEG GENERATOR ───────────────────────────────────────────────────────────
def generate_frames():
    """Blocks on queue.get() — wakes the instant a fresh frame is ready."""
    while True:
        try:
            frame = frame_queue.get(timeout=2.0)
        except queue.Empty:
            continue

        ret, buf = cv2.imencode('.jpg', frame, [cv2.IMWRITE_JPEG_QUALITY, 72])
        if not ret:
            continue

        yield (
            b'--frame\r\n'
            b'Content-Type: image/jpeg\r\n\r\n'
            + buf.tobytes()
            + b'\r\n'
        )


# ── ROUTES ────────────────────────────────────────────────────────────────────
@app.route('/video_feed')
def video_feed():
    return Response(generate_frames(),
                    mimetype='multipart/x-mixed-replace; boundary=frame')

@app.route('/status')
def status():
    return jsonify({"ok": True, "streaming": not frame_queue.empty(),
                    "resolution": f"{FRAME_WIDTH}x{FRAME_HEIGHT}"})


# ── ENTRY POINT ───────────────────────────────────────────────────────────────
if __name__ == '__main__':
    print("=" * 55)
    print(f"  📷  CCTV Camera Stream  (port {PORT})")
    print(f"  🌐  http://127.0.0.1:{PORT}/video_feed")
    print(f"  ✅  No AI — clean raw feed, minimum lag")
    print("=" * 55)

    threading.Thread(target=camera_worker, daemon=True).start()
    app.run(host='0.0.0.0', port=PORT, threaded=True)
