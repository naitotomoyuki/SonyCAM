# read_stream_now.py
import os, time, mmap, subprocess, struct
from pathlib import Path
import numpy as np
import cv2

MAGIC = 0x47524243  # 'CBRG'
HDR_FMT = "<IIIIIQQII"
HDR_SIZE = struct.calcsize(HDR_FMT)  # 40
SHM_NAME = r"Local\Cam1Mem"

def launch_exe():
    libdir = Path(__file__).resolve().parent / "lib"
    exe = libdir / "CAM1.exe"
    if not exe.exists():
        raise FileNotFoundError(exe)
    # stderr→stdout 合流
    p = subprocess.Popen(
        [str(exe)],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        cwd=str(libdir), text=True, bufsize=1  # 行バッファ
    )
    # 共有メモリ名（ASCII LF）
    p.stdin.write(SHM_NAME + "\n"); p.stdin.flush()
    return p

def read_wh(pipe, timeout=5.0):
    """stdout から 'WH w h BPP b STRIDE s' を拾う"""
    t0 = time.time()
    while time.time() - t0 < timeout:
        line = pipe.readline()
        if not line:
            time.sleep(0.05); continue
        line = line.strip()
        # print("[cam-exe]", line)  # 必要ならデバッグ表示
        if line.startswith("WH "):
            parts = line.split()
            w = int(parts[1]); h = int(parts[2])
            bpp = int(parts[parts.index("BPP")+1])
            stride = int(parts[parts.index("STRIDE")+1])
            return w,h,bpp,stride
    raise RuntimeError("WH 行が取得できませんでした。")

def open_map(total):
    # 既存サイズより小さくても開けるので、まず 64KB で開いてヘッダ確認→閉じて再オープンも可
    return mmap.mmap(-1, total, SHM_NAME)

def main():
    proc = launch_exe()
    w,h,bpp,stride = read_wh(proc.stdout, timeout=8.0)
    bytes_per_px = max(1, bpp//8)
    img_bytes = h * stride
    total = HDR_SIZE + img_bytes

    # まず 64KB でヘッダ覗く
    m = open_map(65536)
    m.seek(0)
    hdr_raw = m.read(HDR_SIZE)
    try:
        magic, W,H,BPP,STRIDE, frame_id, ts, seq, res = struct.unpack(HDR_FMT, hdr_raw)
    except struct.error:
        magic = 0
    m.close()

    header_bytes = HDR_SIZE if magic == MAGIC else 0
    # 本番サイズで開き直し
    m = open_map(header_bytes + img_bytes)
    time.sleep(0.05)

    # 最新フレームを読む
    m.seek(header_bytes)
    raw = m.read(img_bytes)
    m.seek(header_bytes)

    row = np.frombuffer(raw, np.uint8).reshape(h, stride)
    valid = row[:, :w*bytes_per_px]
    if bpp == 32:
        img = valid.reshape(h, w, 4)[:, :, :3]  # BGRA→BGR
    elif bpp == 24:
        img = valid.reshape(h, w, 3)
    elif bpp == 8:
        gray = valid.reshape(h, w)
        img = cv2.cvtColor(gray, cv2.COLOR_GRAY2BGR)
    else:
        c = max(1, bpp//8)
        img = valid.reshape(h, w, c)[:, :, :3]

    out = Path(__file__).resolve().parent / "grab_now.png"
    cv2.imwrite(str(out), img)
    print("saved:", out)

    # 後片付け（EXEは走り続けるので必要なら終了させる）
    try:
        proc.stdin.write("finalize\n"); proc.stdin.flush()
    except Exception:
        pass
    try:
        proc.terminate()
    except Exception:
        pass
    m.close()

if __name__ == "__main__":
    main()
