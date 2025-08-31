# cam_fixed_test.py
from pathlib import Path
import cv2
from lib.camera_fixed import FixedCamera

def main():
    out = Path(__file__).resolve().parent / "capture_fixed.png"
    with FixedCamera(exe_name="CAM1.exe", debug=True) as cam:
        img = cam.capture()
        print("shape:", img.shape, "min/max/mean:", img.min(), img.max(), img.mean())
        cv2.imwrite(str(out), img)
    print("saved:", out)

if __name__ == "__main__":
    main()
