# -*- coding: utf-8 -*-
import os, time, mmap, subprocess, threading, queue, struct
from pathlib import Path
import numpy as np
import cv2

SHM_NAME_DEFAULT = r"Local\Cam1Mem"
EXE_NAME_DEFAULT = "CAM1.exe"
OUT_PATH_DEFAULT = "latest.png"
INTERVAL_SEC = 2.0
DEBUG = False

# 既知の固定値（RGB8Packed を想定：BPP=24）
FALLBACK_W, FALLBACK_H, FALLBACK_BPP = 2464, 2056, 24

# CBRGヘッダ
HDR_FMT = "<IIIIIQQII"              # magic,w,h,bpp,stride,frame_id,timestamp,seq,reserved
HDR_SIZE = struct.calcsize(HDR_FMT)
MAGIC_CBRG = 0x47524243             # 'CBRG'

def aligned_stride(w, bpp):
    b = max(1, bpp // 8)
    return ((w * b + 3) // 4) * 4

def _readline(pipe, timeout):
    q = queue.Queue()
    def reader():
        try: q.put(pipe.readline())
        except Exception: q.put(b"")
    t = threading.Thread(target=reader, daemon=True); t.start()
    t.join(timeout)
    if t.is_alive(): return ""
    try: return q.get().decode("utf-8", errors="ignore").strip()
    except Exception: return ""

def launch_cam(libdir: Path, shm_name: str, exe_name=EXE_NAME_DEFAULT):
    exe = libdir / exe_name
    if not exe.exists():
        raise FileNotFoundError(f"{exe} が見つかりません。lib に {exe_name} を置いてください。")
    env = os.environ.copy()
    for p in [
        r"C:\Program Files\Sony\XCCam\GenICam_v3_0\bin\Win64_x64",
        r"C:\Program Files\Sony\XCCam\GenICam_v3_0\bin\Win64_x64\GenApi",
        r"C:\Program Files\Sony\XCCam\GenICam_v3_0\bin\Win64_x64\TLIs",
    ]:
        if os.path.isdir(p):
            env["PATH"] = p + os.pathsep + env.get("PATH", "")

    proc = subprocess.Popen(
        [str(exe)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,  # ←まとめる
        cwd=str(libdir),
        text=False,
        bufsize=0,
        env=env,
    )
    # 共有メモリ名（ASCII + LF）
    proc.stdin.write((shm_name + "\n").encode("ascii"))
    proc.stdin.flush()
    return proc

def read_wh_line(proc_pipe, timeout_sec=8.0):
    """起動ログから WH/BPP/STRIDE を拾う。見つからなければ None を返す。"""
    deadline = time.time() + timeout_sec
    w = h = bpp = stride = None
    last_nonempty = ""
    while time.time() < deadline:
        line = _readline(proc_pipe, 0.4)
        if not line:
            continue  # タイムアウトまで粘る
        if DEBUG: print("[cam-exe]", line)
        low = line.lower()
        if "appender" in low and "win32debug" in low:  # ノイズ除去
            continue
        if "enter the shared memory name" in low:
            continue
        if line.startswith("WH "):
            parts = line.split()
            try:
                w = int(parts[1]); h = int(parts[2])
                if "BPP" in parts:    bpp    = int(parts[parts.index("BPP")+1])
                if "STRIDE" in parts: stride = int(parts[parts.index("STRIDE")+1])
            except Exception:
                pass
            break
        last_nonempty = line
    return (w, h, bpp, stride), last_nonempty

def open_map_guess(shm_name, w=None, h=None, bpp=None, stride=None):
    """CBRGヘッダがあれば総サイズ取得。なければ与えられた/既知の固定値で開く。"""
    # まず 64KB で開いてヘッダ確認
    m = mmap.mmap(-1, 65536, shm_name)
    hdr = m.read(HDR_SIZE); m.seek(0)
    if len(hdr) >= HDR_SIZE:
        magic, hw, hh, hbpp, hstride, *_ = struct.unpack_from(HDR_FMT, hdr, 0)
        if magic == MAGIC_CBRG and hw and hh and hbpp and hstride:
            total = HDR_SIZE + hstride * hh
            m.close()
            m = mmap.mmap(-1, total, shm_name)
            return m, True, (hw, hh, hbpp, hstride)

    # ヘッダ無し → 候補を埋める
    if not w or not h or not bpp:
        w = w or FALLBACK_W
        h = h or FALLBACK_H
        bpp = bpp or FALLBACK_BPP
    stride = stride or aligned_stride(w, bpp)
    total = stride * h
    m.close()
    m = mmap.mmap(-1, total, shm_name)
    return m, False, (w, h, bpp, stride)

def to_bgr(buf: bytes, w: int, h: int, bpp: int, stride: int):
    row = np.frombuffer(buf, np.uint8).reshape(h, stride)
    valid = row[:, : w * max(1, bpp // 8)]
    if bpp == 32:
        img = valid.reshape(h, w, 4)[:, :, :3]  # BGRA→BGR
    elif bpp == 24:
        img = valid.reshape(h, w, 3)            # DIBはBGR順
    elif bpp == 8:
        gray = valid.reshape(h, w)
        img = cv2.cvtColor(gray, cv2.COLOR_GRAY2BGR)
    else:
        c = max(1, bpp // 8)
        img = valid.reshape(h, w, c)[:, :, :3]
    return img  # 反転なし

def main(shm_name=SHM_NAME_DEFAULT, exe_name=EXE_NAME_DEFAULT, out_path=OUT_PATH_DEFAULT, interval=INTERVAL_SEC):
    base = Path(__file__).resolve().parent
    libdir = base / "lib"

    proc = launch_cam(libdir, shm_name, exe_name)
    print(f"[cam] launched: {libdir/exe_name}")

    # WH を待つ（出ない時は last をログ）
    (w, h, bpp, stride), last = read_wh_line(proc.stdout, timeout_sec=8.0)
    if w:
        print(f"[cam] WH: {w}x{h}  BPP={bpp} STRIDE={stride}")
    else:
        print(f"[cam] WH not found. last='{last}' → fallbackに進みます")

    m, has_hdr, (w, h, bpp, stride) = open_map_guess(shm_name, w, h, bpp, stride)
    total = (HDR_SIZE + stride*h) if has_hdr else (stride*h)
    print(f"[cam] mapped: {w}x{h} BPP={bpp} STRIDE={stride} total={total} header={has_hdr}")

    print(f"[loop] saving to '{out_path}' every {interval}s (Ctrl+C to stop)")
    try:
        while True:
            if has_hdr:
                m.seek(0); _ = m.read(HDR_SIZE)
            else:
                m.seek(0)
            raw = m.read(stride * h)
            img = to_bgr(raw, w, h, bpp, stride)
            cv2.imwrite(str(out_path), img)
            print(f"\r{time.strftime('%H:%M:%S')} saved: {out_path}", end="", flush=True)
            time.sleep(interval)
    except KeyboardInterrupt:
        print("\n[loop] stop requested.")
    finally:
        try:
            proc.stdin.write(b"finalize\n"); proc.stdin.flush()
        except Exception: pass
        try:
            proc.terminate(); proc.wait(timeout=2)
        except Exception: pass
        m.close()
        print("bye")

if __name__ == "__main__":
    main()
