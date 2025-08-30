# lib/camera_fixed.py
# 固定SHM/固定解像度のCamBridge.exeをPythonから操作して画像を読む最小実装
import os, time, mmap, subprocess, threading, queue
from pathlib import Path
import numpy as np

try:
    import cv2
except Exception:
    cv2 = None

class FixedCamera:
    """
    CamBridge.exe 側の定数に合わせてここも固定：
    - SHM_NAME:  L"Local\\Cam1Mem"
    - kW, kH:    2464 x 2056
    - フォーマット: RGBA（BPP=32）想定
    ※ もし CamBridge.cpp の定数を変えたら、ここも合わせてください。
    """
    SHM_NAME = r"Local\Cam1Mem"
    W = 2464
    H = 2056
    BPP = 32  # RGBA
    BYTES_PER_PIXEL = BPP // 8
    STRIDE = ((W * BYTES_PER_PIXEL + 3) // 4) * 4  # 4バイト境界
    SHM_SIZE = H * STRIDE

    def __init__(self, exe_name="CAM1e.exe", debug=True):
        self.debug = debug
        self._lib_dir = Path(__file__).resolve().parent
        self._exe = self._lib_dir / exe_name
        if not self._exe.exists():
            raise FileNotFoundError(f"{self._exe} が見つかりません。lib に配置してください。")

        # 必要ならSDKパス補助（無くても動くならそのままでOK）
        env = os.environ.copy()
        add_paths = [
            r"C:\Program Files\Sony\XCCam\GenICam_v3_0\bin\Win64_x64",
            r"C:\Program Files\Sony\XCCam\GenICam_v3_0\bin\Win64_x64\GenApi",
            r"C:\Program Files\Sony\XCCam\GenICam_v3_0\bin\Win64_x64\TLIs",
        ]
        exists = [p for p in add_paths if os.path.isdir(p)]
        if exists:
            env["PATH"] = os.pathsep.join(exists + [env.get("PATH","")])

        # CamBridge.exe 起動（stderrにログ/Done!が出る実装なので stderr=PIPE）
        self.proc = subprocess.Popen(
            [str(self._exe)],
            stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
            cwd=str(self._lib_dir), text=False, bufsize=0, env=env
        )
        if self.debug:
            print("[cam] launched:", self._exe)

        # 共有メモリを開く（固定サイズ）
        # CamBridge は自前で CreateFileMapping 済み
        time.sleep(0.1)  # ほんの少しだけ待つ
        self.shm = mmap.mmap(-1, self.SHM_SIZE, self.SHM_NAME)

        # 起動直後にシリアル等が stderr に1行出る仕様
        _ = self._readline(self.proc.stderr, 1.0)  # 読み捨てでもOK
        if self.debug and _:
            print("[cam-exe]", _.decode("utf-8", "ignore").strip())

    def _readline(self, pipe, timeout):
        q = queue.Queue()
        def reader():
            try:
                q.put(pipe.readline())
            except Exception:
                q.put(b"")
        t = threading.Thread(target=reader, daemon=True)
        t.start(); t.join(timeout)
        if t.is_alive():
            return b""
        return q.get()

    def capture(self):
        if self.proc.poll() is not None:
            raise RuntimeError("CamBridge.exe が終了しています。")

        # 要求
        self.proc.stdin.write(b"capture\n")
        self.proc.stdin.flush()

        # stderr から Done! を待つ（SUM の行も来ます）
        done = False
        end = time.time() + 5.0
        while time.time() < end and not done:
            line = self._readline(self.proc.stderr, 0.5)
            if not line:
                if self.proc.poll() is not None:
                    break
                continue
            s = line.decode("utf-8", "ignore").strip()
            if self.debug and s:
                print("[cam-exe]", s)
            if "Done!" in s:
                done = True
                break

        # 共有メモリから読み出し（上下反転なし）
        self.shm.seek(0)
        buf = self.shm.read(self.SHM_SIZE)
        self.shm.seek(0)

        row = np.frombuffer(buf, np.uint8).reshape(self.H, self.STRIDE)
        valid = row[:, : self.W * self.BYTES_PER_PIXEL]
        if self.BPP == 32:
            img = valid.reshape(self.H, self.W, 4)[:, :, :3]  # BGRA → BGR（A捨て）
        elif self.BPP == 24:
            img = valid.reshape(self.H, self.W, 3)
        else:
            c = max(1, self.BYTES_PER_PIXEL)
            img = valid.reshape(self.H, self.W, c)[:, :, :3]
        return img

    def close(self):
        try:
            if self.proc and self.proc.stdin:
                self.proc.stdin.write(b"finalize\n"); self.proc.stdin.flush()
        except Exception:
            pass
        try:
            if self.proc:
                self.proc.terminate(); self.proc.wait(timeout=2)
        except Exception:
            pass
        try:
            if self.shm:
                self.shm.close()
        except Exception:
            pass

    def __enter__(self): return self
    def __exit__(self, exc_type, exc, tb): self.close()
