# -*- coding: utf-8 -*-
import time, mmap, struct, subprocess
from pathlib import Path
import numpy as np
import cv2

SHM_NAME   = r"Local\Cam1Mem"             # ここを書き換えるときはCAM1.exe側入力と同じに
EXE_PATH   = Path(__file__).resolve().parent / "lib" / "CAM1.exe"
OUT_PATH   = "latest.png"
INTERVAL   = 2.0
OPEN_TO    = 10.0                         # ヘッダ待ちタイムアウト秒

HDR_FMT  = "<IIIIIQQII"                   # magic,w,h,bpp,stride,frame_id,timestamp,seq,reserved
HDR_SIZE = struct.calcsize(HDR_FMT)
MAGIC    = 0x47524243                     # 'CBRG'

def to_bgr(raw, w, h, bpp, stride):
    row = np.frombuffer(raw, np.uint8).reshape(h, stride)
    valid = row[:, : w * max(1, bpp // 8)]
    if bpp == 32:
        img = valid.reshape(h, w, 4)[:, :, :3]        # BGRA→BGR
    elif bpp == 24:
        img = valid.reshape(h, w, 3)                  # BGR
    elif bpp == 8:
        img = cv2.cvtColor(valid.reshape(h, w), cv2.COLOR_GRAY2BGR)
    else:
        c = max(1, bpp // 8)
        img = valid.reshape(h, w, c)[:, :, :3]
    return img                                        # 反転なし

def open_map_with_header(name, open_timeout=OPEN_TO):
    # まず64KBで開いてヘッダ確認（既存マップがあればOK）
    t0 = time.time()
    while True:
        try:
            m = mmap.mmap(-1, 65536, name)
            break
        except Exception:
            if time.time() - t0 > open_timeout:
                raise RuntimeError("共有メモリを開けませんでした（タイムアウト）")
            time.sleep(0.1)

    hdr = m.read(HDR_SIZE); m.seek(0)
    magic,w,h,bpp,stride,frame_id,ts,seq,_ = struct.unpack_from(HDR_FMT, hdr, 0)
    if magic != MAGIC or w*h == 0:
        m.close()
        raise RuntimeError("CBRGヘッダが見つかりません。名前不一致 or EXEがヘッダ未実装のビルドです。")

    total = HDR_SIZE + stride*h
    m.close()
    m = mmap.mmap(-1, total, name)
    return m, (w,h,bpp,stride)

def main():
    if not EXE_PATH.exists():
        raise FileNotFoundError(f"{EXE_PATH} が見つかりません。")

    # EXE 起動 → 共有メモリ名（ASCII+LF）を渡す
    proc = subprocess.Popen(
        [str(EXE_PATH)],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        cwd=str(EXE_PATH.parent), text=False, bufsize=0,
    )
    proc.stdin.write((SHM_NAME + "\n").encode("ascii"))
    proc.stdin.flush()

    # 起動ログを少し排水（ある/なしどちらでも良い）
    for _ in range(3):
        try:
            line = proc.stdout.readline().decode("utf-8", "ignore").strip()
            if line: print("[cam-exe]", line)
        except Exception:
            break

    # 共有メモリ（CBRGヘッダ）を開く
    m, (w,h,bpp,stride) = open_map_with_header(SHM_NAME)
    print(f"[map] {w}x{h} BPP={bpp} STRIDE={stride}")

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
        try:
            proc.stdin.write(b"finalize\n"); proc.stdin.flush()
        except Exception:
            pass
        try:
            proc.terminate(); proc.wait(timeout=2)
        except Exception:
            pass
        m.close()

if __name__ == "__main__":
    main()
