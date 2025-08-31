# -*- coding: utf-8 -*-
import time, mmap, struct
from pathlib import Path
import numpy as np
import cv2

SHM_NAME = r"Local\Cam1Mem"          # CAM1.exe と合わせる
OUT_PATH = "latest.png"
INTERVAL = 2.0

HDR_FMT  = "<IIIIIQQII"               # magic,w,h,bpp,stride,frame_id,timestamp,seq,reserved
HDR_SIZE = struct.calcsize(HDR_FMT)
MAGIC    = 0x47524243                 # 'CBRG'

def to_bgr(raw, w, h, bpp, stride):
    row = np.frombuffer(raw, np.uint8).reshape(h, stride)
    valid = row[:, :w * max(1, bpp // 8)]
    if bpp == 32:
        img = valid.reshape(h, w, 4)[:, :, :3]     # BGRA → BGR
    elif bpp == 24:
        img = valid.reshape(h, w, 3)               # DIBのBGR
    elif bpp == 8:
        img = cv2.cvtColor(valid.reshape(h, w), cv2.COLOR_GRAY2BGR)
    else:
        c = max(1, bpp // 8)
        img = valid.reshape(h, w, c)[:, :, :3]
    return img                                    # 反転なし

def main():
    # まず64KBで開いてヘッダを読む
    m = mmap.mmap(-1, 65536, SHM_NAME)
    hdr = m.read(HDR_SIZE); m.seek(0)
    magic,w,h,bpp,stride,frame_id,ts,seq,_ = struct.unpack_from(HDR_FMT, hdr, 0)
    if magic != MAGIC or w*h == 0:
        raise RuntimeError("CBRGヘッダが見つかりません。CAM1.exe が起動済みか、名前が一致しているか確認してください。")

    total = HDR_SIZE + stride*h
    m.close()
    m = mmap.mmap(-1, total, SHM_NAME)
    print(f"[map] {w}x{h} BPP={bpp} STRIDE={stride} total={total}")

    try:
        while True:
            m.seek(0)
            hdr = m.read(HDR_SIZE)
            _,w,h,bpp,stride,frame_id,ts,seq,_ = struct.unpack_from(HDR_FMT, hdr, 0)
            raw = m.read(stride*h)
            img = to_bgr(raw, w, h, bpp, stride)
            cv2.imwrite(OUT_PATH, img)
            print(f"\r{time.strftime('%H:%M:%S')} saved {OUT_PATH} (id={frame_id})", end="", flush=True)
            time.sleep(INTERVAL)
    except KeyboardInterrupt:
        print("\nbye")
    finally:
        m.close()

if __name__ == "__main__":
    main()
