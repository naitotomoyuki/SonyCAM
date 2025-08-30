# lib/camera_fixed.py
import os, time, mmap, subprocess, threading, queue
from pathlib import Path
import numpy as np
try:
    import cv2
except Exception:
    cv2 = None

class FixedCamera:
    SHM_NAME = r"Local\Cam1Mem"
    W, H, BPP = 2464, 2056, 32
    BYTES_PER_PIXEL = BPP // 8
    STRIDE = ((W * BYTES_PER_PIXEL + 3) // 4) * 4
    SHM_SIZE = H * STRIDE

    def __init__(self, exe_name="CAM1.exe", debug=True):
        self.debug = debug
        self._lib_dir = Path(__file__).resolve().parent
        exe_path = self._lib_dir / exe_name
        if not exe_path.exists():
            raise FileNotFoundError(f"{exe_path} が見つかりません。lib に配置してください。")

        env = os.environ.copy()
        add_paths = [
            r"C:\Program Files\Sony\XCCam\GenICam_v3_0\bin\Win64_x64",
            r"C:\Program Files\Sony\XCCam\GenICam_v3_0\bin\Win64_x64\GenApi",
            r"C:\Program Files\Sony\XCCam\GenICam_v3_0\bin\Win64_x64\TLIs",
        ]
        path_parts = [p for p in add_paths if os.path.isdir(p)]
        if path_parts:
            env["PATH"] = os.pathsep.join(path_parts + [env.get("PATH","")])

        # stdout/stderrを一本化、stdinもパイプに
        self.proc = subprocess.Popen(
            [str(exe_path)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            cwd=str(self._lib_dir),
            text=False,
            bufsize=0,
            env=env,
        )
        self._pipe = self.proc.stdout
        if self.debug:
            print("[cam] launched:", exe_path)

        # 共有メモリ名は ASCII で（C++ を cin に統一した前提）
        self.proc.stdin.write((self.SHM_NAME + "\n").encode("ascii"))
        self.proc.stdin.flush()

        time.sleep(0.1)
        self.shm = mmap.mmap(-1, self.SHM_SIZE, self.SHM_NAME)

        # 起動直後のノイズを軽く排水
        _ = self._readline(self._pipe, 1.0)
        if self.debug and _:
            print("[cam-exe]", _.decode("utf-8", "ignore").strip())

    def _readline(self, pipe, timeout):
        q = queue.Queue()
        def reader():
            try: q.put(pipe.readline())
            except Exception: q.put(b"")
        t = threading.Thread(target=reader, daemon=True); t.start()
        t.join(timeout)
        if t.is_alive(): return b""
        return q.get()

    def _spam_commands(self, duration_sec=0.8):
        """cin/wcin どちらでも拾えるよう、ASCII/UTF-16LE を短時間に交互送信"""
        ascii_crlf = b"capture\r\n"
        ascii_lf   = b"capture\n"
        wide_crlf  = "capture".encode("utf-16le") + b"\x0d\x00\x0a\x00"
        t_end = time.time() + duration_sec
        i = 0
        while time.time() < t_end:
            try:
                if i % 3 == 0:   self.proc.stdin.write(ascii_crlf)
                elif i % 3 == 1: self.proc.stdin.write(ascii_lf)
                else:            self.proc.stdin.write(wide_crlf)
                self.proc.stdin.flush()
            except Exception:
                break
            _ = self._readline(self._pipe, 0.01)  # 排水
            i += 1
            time.sleep(0.05)

    def capture(self):
        if self.proc.poll() is not None:
            raise RuntimeError("CAM1.exe が終了しています。")

        # 通常のキャプチャ
        self.proc.stdin.write(b"capture\n")
        self.proc.stdin.flush()

        # Done! を待つ（最大2秒）
        end = time.time() + 2.0
        done = False
        while time.time() < end:
            line = self._readline(self._pipe, 0.2)
            if not line: 
                continue
            if b"Done!" in line:
                done = True
                break

        # 保険（Done!が来ない環境向け）
        if not done:
            self._spam_commands(0.8)

        # 共有メモリから読み出し
        buf = self.shm.read(self.SHM_SIZE)
        self.shm.seek(0)

        row = np.frombuffer(buf, np.uint8).reshape(self.H, self.STRIDE)
        valid = row[:, : self.W * self.BYTES_PER_PIXEL]
        if self.BPP == 32:
            img = valid.reshape(self.H, self.W, 4)[:, :, :3]  # BGRA→BGR
        elif self.BPP == 24:
            img = valid.reshape(self.H, self.W, 3)
        else:
            c = max(1, self.BYTES_PER_PIXEL)
            img = valid.reshape(self.H, self.W, c)[:, :, :3]
        return img

    def close(self):
        try:
            if self.proc and self.proc.stdin:
                # finalize は両方式で
                try:
                    self.proc.stdin.write(b"finalize\r\n"); self.proc.stdin.flush()
                except Exception: pass
                try:
                    fin_wide = "finalize".encode("utf-16le") + b"\x0d\x00\x0a\x00"
                    self.proc.stdin.write(fin_wide); self.proc.stdin.flush()
                except Exception: pass
        except Exception: pass
        try:
            if self.proc:
                self.proc.terminate(); self.proc.wait(timeout=2)
        except Exception: pass
        try:
            if self.shm:
                self.shm.close()
        except Exception: pass

    def __enter__(self): return self
    def __exit__(self, exc_type, exc, tb): self.close()
