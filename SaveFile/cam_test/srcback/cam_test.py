# stream_read_once.py
import os, time, mmap, subprocess, struct
from pathlib import Path
import numpy as np
import cv2

MAGIC = 0x47524243           # 'CBRG'
HDR_FMT = "<IIIIIQQII"       # magic,w,h,bpp,stride,frame_id,timestamp,seq,reserved
HDR_SIZE = struct.calcsize(HDR_FMT)
SHM_NAME = r"Local\Cam1Mem"  # そのまま使ってOK

def launch_exe():
    libdir = Path(__file__).resolve().parent / "lib"
    exe = libdir / "CAM1.exe"
    p = subprocess.Popen(
        [str(exe)],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        cwd=str(libdir), text=True, bufsize=1
    )
    p.stdin.write(SHM_NAME + "\n"); p.stdin.flush()
    return p

def read_wh(pipe, timeout=8.0):
    t0 = time.time()
    while time.time()-t0 < timeout:
        line = pipe.readline()
        if not line: time.sleep(0.02); continue
        line = line.strip()
        if line.startswith("WH "):
            parts = line.split()
            w = int(parts[1]); h = int(parts[2])
            bpp = int(parts[parts.index("BPP")+1])
            stride = int(parts[parts.index("STRIDE")+1])
            return w,h,bpp,stride
    raise RuntimeError("WH 行が取得できません")

def try_open(size):
    return mmap.mmap(-1, size, SHM_NAME)

def main():
    proc = launch_exe()
    w,h,bpp,stride = read_wh(proc.stdout)
    bytes_per_px = max(1, bpp//8)
    img_bytes = h*stride

    # まず64KBでヘッダ確認
    m = try_open(65536)
    hdr_raw = m.read(HDR_SIZE); m.seek(0)
    header = None
    try:
        header = struct.unpack(HDR_FMT, hdr_raw)
    except struct.error:
        pass
    magic = header[0] if header else 0
    header_bytes = HDR_SIZE if magic == MAGIC else 0
    m.close()

    # 本番サイズで開き直し
    m = try_open(header_bytes + img_bytes)
    last_id = -1
    t0 = time.time(); frames = 0

    save_at = 100
    out = Path(__file__).resolve().parent / "stream_sample.png"

    while frames < save_at:
        if header_bytes:  # frame_id が使える
            m.seek(0)
            hdr = struct.unpack(HDR_FMT, m.read(HDR_SIZE))
            frame_id = hdr[5]
            if frame_id == last_id:
                time.sleep(0.001); continue
            last_id = frame_id

        # ピクセル取り出し
        m.seek(header_bytes)
        raw = m.read(img_bytes); m.seek(header_bytes)
        row = np.frombuffer(raw, np.uint8).reshape(h, stride)
        valid = row[:, :w*bytes_per_px]
        if bpp == 32:
            img = valid.reshape(h, w, 4)[:, :, :3]
        elif bpp == 24:
            img = valid.reshape(h, w, 3)
        elif bpp == 8:
            gray = valid.reshape(h, w)
            img = cv2.cvtColor(gray, cv2.COLOR_GRAY2BGR)
        else:
            c = max(1, bpp//8)
            img = valid.reshape(h, w, c)[:, :, :3]

        frames += 1
        if frames % 20 == 0:
            dt = time.time() - t0
            print(f"{frames} frames  {frames/dt:.1f} FPS")

    cv2.imwrite(str(out), img)
    print("saved:", out)

    # 終了（EXEは finalize で止めても/放置でもOK）
    try: proc.stdin.write("finalize\n"); proc.stdin.flush()
    except Exception: pass
    try: proc.terminate()
    except Exception: pass
    m.close()

if __name__ == "__main__":
    main()
