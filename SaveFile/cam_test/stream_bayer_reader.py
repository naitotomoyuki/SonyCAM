# stream_reader_safe.py  （cam_test 直下に保存）
import time, mmap, subprocess, struct
from pathlib import Path
import numpy as np
import cv2

SHM_NAME = r"Local\Cam1Mem"
MAGIC = 0x47524243  # 'CBRG'
HDR_FMT = "<IIIIIQQII"
HEADER_SIZE = struct.calcsize(HDR_FMT)

# レガシー（ヘッダ無し）時のデフォルト想定
LEGACY_W, LEGACY_H, LEGACY_BPP = 2464, 2056, 32

def parse_header(b): return struct.unpack_from(HDR_FMT, b, 0)

def open_view(name, length, retries=120, sleep=0.05):
    last = None
    for _ in range(retries):
        try:
            return mmap.mmap(-1, length, name)
        except OSError as e:
            last = e; time.sleep(sleep)
    raise last

def wait_header_ready(name, timeout=8.0):
    m = open_view(name, HEADER_SIZE)
    try:
        t_end = time.time() + timeout
        while time.time() < t_end:
            m.seek(0)
            hdr = m.read(HEADER_SIZE)
            if len(hdr) == HEADER_SIZE:
                magic, w, h, bpp, stride, fid, ts, seq, _ = parse_header(hdr)
                if magic == MAGIC and w > 0 and h > 0 and bpp in (8,24,32) and stride >= w:
                    return (w, h, bpp, stride)
            time.sleep(0.02)
        return None
    finally:
        m.close()

def main():
    libdir = Path(__file__).resolve().parent / "lib"
    exe = libdir / "CAM1.exe"
    print("[info] exe path:", exe)
    if not exe.exists():
        raise FileNotFoundError(exe)

    # EXE 起動（stdoutへ合流）
    proc = subprocess.Popen(
        [str(exe)],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        cwd=str(libdir), text=False, bufsize=0
    )
    # 共有メモリ名（ASCII）
    proc.stdin.write((SHM_NAME + "\n").encode("ascii")); proc.stdin.flush()

    # 1) ヘッダ等待ち
    params = wait_header_ready(SHM_NAME, timeout=8.0)

    legacy = False
    if params is None:
        # 2) レガシー（ヘッダ無し）にフォールバック
        legacy = True
        w, h, bpp = LEGACY_W, LEGACY_H, LEGACY_BPP
        stride = ((w * (bpp//8) + 3)//4)*4
        total = stride * h   # ヘッダなし
        print(f"[warn] header not found. fallback to LEGACY mode {w}x{h} {bpp}bpp stride={stride}")
        m = open_view(SHM_NAME, total)
    else:
        w, h, bpp, stride = params
        total = HEADER_SIZE + stride * h
        print(f"[hdr] w={w} h={h} bpp={bpp} stride={stride} total={total}")
        m = open_view(SHM_NAME, total)

    # 起動ログを軽く排水
    for _ in range(3):
        try:
            line = proc.stdout.readline().decode("utf-8", "ignore").strip()
            if line: print("[cam-exe]", line)
        except Exception:
            break

    last_id = -1
    n, t0 = 0, time.time()
    try:
        while True:
            if legacy:
                # ヘッダ無し：そのままピクセルを読む
                m.seek(0)
            else:
                # ヘッダ有り：新フレーム待ち
                m.seek(0)
                hdr = m.read(HEADER_SIZE)
                if len(hdr) < HEADER_SIZE:
                    time.sleep(0.001); continue
                magic, w, h, bpp, stride, fid, ts, seq, _ = parse_header(hdr)
                if magic != MAGIC or fid == last_id:
                    time.sleep(0.002); continue
                last_id = fid

            buf = m.read(stride * h)
            if len(buf) < stride * h:
                time.sleep(0.001); continue

            row = np.frombuffer(buf, np.uint8).reshape(h, stride)

            if bpp == 32:
                bgr = row[:, :w*4].reshape(h, w, 4)[:, :, :3].copy()
            elif bpp == 24:
                bgr = row[:, :w*3].reshape(h, w, 3).copy()
            elif bpp == 8:
                gray = row[:, :w].copy()
                # 必要に応じて COLOR_BayerBG2BGR / GR / GB を試す
                bgr = cv2.cvtColor(gray, cv2.COLOR_BayerRG2BGR)
            else:
                continue

            cv2.imshow("live", bgr)
            n += 1
            if n % 30 == 0:
                dt = time.time() - t0
                fps = n/dt if dt > 0 else 0
                print(f"\r{n} frames  {fps:.1f} FPS", end="", flush=True)

            k = cv2.waitKey(1) & 0xFF
            if k == ord('s'):
                cv2.imwrite("frame.png", bgr); print("\nSaved: frame.png")
            if k == 27:  # ESC
                break
    finally:
        try:
            proc.stdin.write(b"finalize\n"); proc.stdin.flush()
        except Exception: pass
        try:
            proc.terminate(); proc.wait(timeout=2)
        except Exception: pass
        m.close(); cv2.destroyAllWindows()

if __name__ == "__main__":
    main()
