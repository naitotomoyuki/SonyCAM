# stream_reader_min.py — ヘッダ無しでも動く強制マップ版（WH行が取れなくてもOK）
import os, time, mmap, subprocess, threading, queue
from pathlib import Path
import numpy as np
import cv2

SHM_NAME = r"Local\Cam1Mem"

# ここに「この環境で実際に出ているサイズ候補」を並べる
# 先頭から順に試し、マップできたものを使う
FALLBACK_GUESSES = [
    # (W, H, BPP, STRIDE)
    (2464, 2056, 32, 9856),   # あなたの実機（32bpp, stride=9856）
    (1024,  768, 32, 4096),   # 以前のテスト解像度
]

def _spawn_cam_exe(exe_path: Path, cwd: Path):
    env = os.environ.copy()
    # SONY SDKのPATH（存在するものだけ）
    add = [
        r"C:\Program Files\Sony\XCCam\GenICam_v3_0\bin\Win64_x64",
        r"C:\Program Files\Sony\XCCam\GenICam_v3_0\bin\Win64_x64\GenApi",
        r"C:\Program Files\Sony\XCCam\GenICam_v3_0\bin\Win64_x64\TLIs",
    ]
    env["PATH"] = os.pathsep.join([p for p in add if os.path.isdir(p)] + [env.get("PATH","")])

    proc = subprocess.Popen(
        [str(exe_path)],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        cwd=str(cwd), text=False, bufsize=0, env=env
    )
    # 共有メモリ名（ASCII LF で）
    proc.stdin.write((SHM_NAME + "\n").encode("ascii"))
    proc.stdin.flush()
    return proc

def _spam_capture(proc, stop_evt):
    patt = [b"capture\r\n", b"capture\n"]
    i = 0
    while not stop_evt.is_set():
        try:
            proc.stdin.write(patt[i % 2]); proc.stdin.flush()
        except Exception:
            break
        i += 1
        time.sleep(0.05)  # 20Hz

def _try_map_fixed(name, w, h, bpp, stride, timeout=5.0):
    total = stride * h
    t0 = time.time()
    while time.time() - t0 < timeout:
        try:
            m = mmap.mmap(-1, total, name, access=mmap.ACCESS_READ)
            return m, total
        except Exception:
            time.sleep(0.05)
    return None, total

def main():
    here = Path(__file__).resolve().parent
    lib  = here / "lib"
    exe  = lib / "CAM1.exe"
    print("[info] exe:", exe)
    if not exe.exists():
        raise FileNotFoundError(exe)

    # 起動
    proc = _spawn_cam_exe(exe, lib)

    # 旧式EXEを想定：captureを自動連打してフレーム更新を促す
    stop_evt = threading.Event()
    spam_th  = threading.Thread(target=_spam_capture, args=(proc, stop_evt), daemon=True)
    spam_th.start()

    # 固定サイズ群で順に mmap を試す
    mapped = None
    for (W, H, BPP, STRIDE) in FALLBACK_GUESSES:
        m, total = _try_map_fixed(SHM_NAME, W, H, BPP, STRIDE, timeout=5.0)
        if m is not None:
            mapped = (m, W, H, BPP, STRIDE, total)
            print(f"[info] mapped with fixed params: {W}x{H} BPP={BPP} STRIDE={STRIDE} total={total}")
            break
        else:
            print(f"[warn] map failed for guess: {W}x{H} BPP={BPP} STRIDE={STRIDE} total={total}")

    if mapped is None:
        stop_evt.set()
        try: proc.terminate(); proc.wait(timeout=2)
        except Exception: pass
        raise RuntimeError("failed to mmap with all fallback guesses")

    m, W, H, BPP, STRIDE, TOTAL = mapped
    bytes_per_pixel = max(1, BPP // 8)

    # 1枚保存（更新が乗るよう 50ms 待ってから読む）
    time.sleep(0.05)
    m.seek(0)
    raw = m.read(TOTAL); m.seek(0)
    row = np.frombuffer(raw, np.uint8).reshape(H, STRIDE)
    valid = row[:, : W*bytes_per_pixel]
    if BPP == 32:
        img = valid.reshape(H, W, 4)[:, :, :3]  # BGRA→BGR
    elif BPP == 24:
        img = valid.reshape(H, W, 3)
    elif BPP == 8:
        gray = valid.reshape(H, W)
        img  = cv2.cvtColor(gray, cv2.COLOR_GRAY2BGR)
    else:
        img = valid.reshape(H, W, bytes_per_pixel)[:, :, :3]
    out = here / "grab_once.png"
    cv2.imwrite(str(out), img)
    print("saved:", out)

    # 以降は更新検出（先頭64KBの変化）でFPS表示
    frames = 0; t0 = time.time()
    PROBE = min(65536, TOTAL)
    m.seek(0); last = m.read(PROBE); m.seek(0)

    try:
        while True:
            time.sleep(0.002)
            m.seek(0); cur = m.read(PROBE); m.seek(0)
            if cur != last:
                last = cur
                frames += 1
                if frames % 30 == 0:
                    dt = time.time() - t0
                    fps = frames/dt if dt>0 else 0
                    print(f"\r{frames} frames  {fps:.1f} FPS", end="", flush=True)
    except KeyboardInterrupt:
        pass
    finally:
        stop_evt.set()
        try:
            proc.stdin.write(b"finalize\n"); proc.stdin.flush()
        except Exception: pass
        if spam_th: spam_th.join(timeout=0.5)
        try:
            proc.terminate(); proc.wait(timeout=2)
        except Exception: pass
        m.close()
        print("\nbye")

if __name__ == "__main__":
    main()
