"""Test: Camera via picamera2."""

import os
import time

_CAPTURE_PATH = "/tmp/camera_test.jpg"
_MIN_SIZE_BYTES = 10_240  # 10 KB sanity check


def run_test(config: dict) -> bool:
    print("\n[CAMERA] Initialising camera...")

    try:
        from picamera2 import Picamera2
    except ImportError:
        print("[CAMERA] FAIL — picamera2 not installed.")
        print("         Run: sudo apt install -y python3-picamera2")
        return False

    camera = None
    try:
        camera = Picamera2()

        w = config["camera"]["width"]
        h = config["camera"]["height"]
        still_config = camera.create_still_configuration(main={"size": (w, h)})
        camera.configure(still_config)
        camera.start()

        print(f"[CAMERA] Auto-exposure settling (2 s)...")
        time.sleep(2)

        print(f"[CAMERA] Capturing image → {_CAPTURE_PATH}")
        camera.capture_file(_CAPTURE_PATH)

        if not os.path.exists(_CAPTURE_PATH):
            print("[CAMERA] FAIL — capture file not found.")
            return False

        size = os.path.getsize(_CAPTURE_PATH)
        if size < _MIN_SIZE_BYTES:
            print(f"[CAMERA] FAIL — file too small ({size} bytes), image may be blank.")
            return False

        print(f"[CAMERA] Captured {size / 1024:.1f} KB → {_CAPTURE_PATH}")
        print("[CAMERA] Please inspect the image to verify quality.")

        answer = input("\n[CAMERA] Did the camera capture a valid image? [y/n]: ").strip().lower()
        return answer == "y"

    except RuntimeError as e:
        print(f"[CAMERA] FAIL — runtime error (camera not detected?): {e}")
        return False
    except KeyboardInterrupt:
        print("\n[CAMERA] Interrupted.")
        return False
    finally:
        if camera is not None:
            try:
                camera.stop()
                camera.close()
            except Exception:
                pass
        print("[CAMERA] Cleaned up.")
