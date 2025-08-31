# capture_once_legacy.py  —— CAM1.exe がヘッダを書かないレガシー用
import os, time, mmap, subprocess
from pathlib import Path
import numpy as np
import cv2

# ★ここを環境に合わせて
LIBDIR = Path(__file__).resolve().parent / "lib"
EXE    = LIBDIR / "CAM1.exe"
SHM    = r"Local\Cam1Mem_test2"     # ← CAM1.exe に入力するSHM名と同じにする
W,H,BPP,STRIDE = 2464, 2056, 32, 9856   # ← CAM1.exe の WH 行と同じ
BYTES = H * STRIDE

def main():
    if not EXE.exists():
        raise FileNotFoundError(EXE)
    env = os.environ.copy()
    # SDK DLL パス（存在するものだけ追加）
    add_paths = [
        r"C:\Program Files\Sony\XCCam\GenICam_v3_0\bin\Win64_x64",
        r"C:\Program Files\Sony\XCCam\GenICam_v3_0\bin\Win64_x64\GenApi",
        r"C:\Program Files\Sony\XCCam\GenICam_v3_0\bin\Win64_x64\TLIs",
    ]
    path_parts = [p for p in add_paths if os.path.isdir(p)]
    if path_parts:
        env["PATH"] = os.pathsep.join(path_parts + [env.get("PATH","")])

    # EXE 起動（stdout/stderr 1本化）
    proc = subprocess.Popen(
        [str(EXE)],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        cwd=str(LIBDIR), text=False, bufsize=0, env=env
    )
    # 共有メモリ名（ASCIIでOK）を送る
    proc.stdin.write((SHM + "\n").encode("ascii")); proc.stdin.flush()

    # 起動ログを少し捨てる
    for _ in range(3):
        try:
            line = proc.stdout.readline().decode("utf-8","ignore").strip()
            if line: print("[cam-exe]", line)
        except Exception:
            break

    # 共有メモリをオープン（レガシー＝ピクセルだけ、サイズは H*STRIDE）
    time.sleep(0.1)
    m = mmap.mmap(-1, BYTES, SHM)

    # 1フレーム要求 → “Done!” を待機（5秒タイムアウト）
    proc.stdin.write(b"capture\n"); proc.stdin.flush()
    end = time.time() + 5.0
    done = False
    while time.time() < end and not done:
        try:
            line = proc.stdout.readline().decode("utf-8","ignore").strip()
        except Exception:
            line = ""
        if line:
            print("[cam-exe]", line)
            if "Done!" in line:
                done = True; break
    if not done:
        print("warn: Done! が来なかったのでそのまま読み出します…")

    # 共有メモリから読み出し → 画像化（BGRX想定の先頭3ch）
    buf = m.read(BYTES); m.seek(0)
    row = np.frombuffer(buf, np.uint8).reshape(H, STRIDE)
    valid = row[:, : W * (BPP//8)]
    img = valid.reshape(H, W, 4)[:, :, :3]    # BGRA→BGR（上反転なし）

    print("shape:", img.shape, "min/max/mean:", img.min(), img.max(), img.mean())
    out = Path(__file__).resolve().parent / "grab_once.png"
    cv2.imwrite(str(out), img)
    print("saved:", out)

    # 後始末
    try:
        proc.stdin.write(b"finalize\n"); proc.stdin.flush()
    except Exception: pass
    try:
        proc.terminate(); proc.wait(timeout=2)
    except Exception: pass
    m.close()

if __name__ == "__main__":
    main()
