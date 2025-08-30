# capture_once_legacy.py  —— CAM1.exe が「ヘッダ無し・Done!未出」の場合でも読む
import os, time, mmap, subprocess, threading, queue
from pathlib import Path
import numpy as np
import cv2

LIBDIR = Path(__file__).resolve().parent / "lib"
EXE    = LIBDIR / "CAM1.exe"
SHM    = r"Local\Cam1Mem_test"     # ← CAM1.exe に打った名前と一致させる
W,H,BPP,STRIDE = 2464, 2056, 32, 9856
BYTES_PER_PIXEL = BPP // 8
TOTAL = H * STRIDE

def _reader(pipe, q):
    try:
        while True:
            b = pipe.readline()
            if not b: break
            s = b.decode("utf-8", "ignore").strip()
            if s: q.put(s)
    except Exception:
        pass

def main():
    if not EXE.exists():
        raise FileNotFoundError(EXE)

    env = os.environ.copy()
    add_paths = [
        r"C:\Program Files\Sony\XCCam\GenICam_v3_0\bin\Win64_x64",
        r"C:\Program Files\Sony\XCCam\GenICam_v3_0\bin\Win64_x64\GenApi",
        r"C:\Program Files\Sony\XCCam\GenICam_v3_0\bin\Win64_x64\TLIs",
    ]
    path_parts = [p for p in add_paths if os.path.isdir(p)]
    if path_parts:
        env["PATH"] = os.pathsep.join(path_parts + [env.get("PATH","")])

    # stderr→stdout に合流
    proc = subprocess.Popen(
        [str(EXE)],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        cwd=str(LIBDIR), text=False, bufsize=0, env=env
    )
    # ログを非ブロッキングで読む準備
    q = queue.Queue()
    th = threading.Thread(target=_reader, args=(proc.stdout, q), daemon=True)
    th.start()

    # 共有メモリ名（ASCII LF）を送る
    proc.stdin.write((SHM + "\n").encode("ascii")); proc.stdin.flush()

    # 起動ログを少し捨てつつ表示
    t0 = time.time()
    while time.time() - t0 < 1.0:
        try:
            line = q.get_nowait()
            print("[cam-exe]", line)
        except queue.Empty:
            break

    # mmap を開く（レガシー＝ピクセルだけ）
    time.sleep(0.2)
    m = mmap.mmap(-1, TOTAL, SHM)

    # —— “Done!” 待ち + 共有メモリ変化のフォールバック —— #
    # 直前スナップショット
    m.seek(0); before = m.read(min(4096, TOTAL)); m.seek(0)

    # capture を複数方式で送る（ASCII/CRLF/UTF-16LE 全部）
    try:
        proc.stdin.write(b"capture\n"); proc.stdin.flush()
        proc.stdin.write(b"capture\r\n"); proc.stdin.flush()
        proc.stdin.write("capture".encode("utf-16le") + b"\x0d\x00\x0a\x00"); proc.stdin.flush()
    except Exception:
        pass

    done = False
    deadline = time.time() + 2.0  # “Done!” の待機（最大2秒）
    while time.time() < deadline:
        try:
            line = q.get(timeout=0.2)
            print("[cam-exe]", line)
            if "Done!" in line:
                done = True
                break
        except queue.Empty:
            # 共有メモリの変化を見て抜ける
            m.seek(0); cur = m.read(len(before)); m.seek(0)
            if cur != before:
                done = True
                break

    # 読み出し
    buf = m.read(TOTAL); m.seek(0)
    row = np.frombuffer(buf, np.uint8).reshape(H, STRIDE)
    valid = row[:, : W * BYTES_PER_PIXEL]
    img = valid.reshape(H, W, 4)[:, :, :3]    # BGRA→BGR

    print("shape:", img.shape, "min/max/mean:", img.min(), img.max(), img.mean())
    out = Path(__file__).resolve().parent / "grab_once.png"
    cv2.imwrite(str(out), img)
    print("saved:", out)

    # 終了処理
    try:
        proc.stdin.write(b"finalize\n"); proc.stdin.flush()
    except Exception: pass
    try:
        proc.terminate(); proc.wait(timeout=2)
    except Exception: pass
    m.close()

if __name__ == "__main__":
    main()
