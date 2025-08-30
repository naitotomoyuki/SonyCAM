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

        # stdout と stderr を1本化（どちらに出しても拾えるように）
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

        # 共有メモリ名を UTF-16LE + CRLF で送る（wcin対応）
        shm_wide = self.SHM_NAME.encode("utf-16le") + b"\x0d\x00\x0a\x00"
        self.proc.stdin.write(shm_wide)
        self.proc.stdin.flush()

        # 少し待ってから SHM オープン
        time.sleep(0.15)
        self.shm = mmap.mmap(-1, self.SHM_SIZE, self.SHM_NAME)

        # 起動直後の1行を排水（シリアル等）
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

    def _spam_commands(self, duration_sec=1.2):
        """
        wcin/cin どっちでも反応するように
        - ASCII: 'capture\\r\\n' と 'capture\\n'
        - UTF-16LE: 'capture\\r\\n'
        を 50ms ごとに交互送信
        """
        ascii_crlf = b"capture\r\n"
        ascii_lf   = b"capture\n"
        wide_crlf  = "capture".encode("utf-16le") + b"\x0d\x00\x0a\x00"

        t_end = time.time() + duration_sec
        i = 0
        while time.time() < t_end:
            try:
                if i % 3 == 0:
                    self.proc.stdin.write(ascii_crlf)
                elif i % 3 == 1:
                    self.proc.stdin.write(ascii_lf)
                else:
                    self.proc.stdin.write(wide_crlf)
                self.proc.stdin.flush()
            except Exception:
                break
            # ログを軽く排水
            _ = self._readline(self._pipe, 0.01)
            i += 1
            time.sleep(0.05)

    def capture(self):
        if self.proc.poll() is not None:
            raise RuntimeError("CAM1.exe が終了しています。")

        # 差分検知のためのスナップショット
        probe_len = min(65536, self.SHM_SIZE)
        self.shm.seek(0); before = self.shm.read(probe_len); self.shm.seek(0)

        # まずは通常の1発送信（CRLF）
        try:
            self.proc.stdin.write(b"capture\r\n")
            self.proc.stdin.flush()
        except Exception:
            pass

        # 0.7秒だけ Done!/SUM を待つ
        end = time.time() + 0.7
        done = False
        while time.time() < end:
            line = self._readline(self._pipe, 0.15)
            if not line:
                continue
            s = line.decode("utf-8", "ignore").strip()
            if self.debug and s:
                print("[cam-exe]", s)
            low = s.lower()
            if "done" in low or low.startswith("sum "):
                done = True
                break

        # 反応薄ければスパム送信＋差分待ち
        if not done:
            self._spam_commands(duration_sec=1.2)

        # 共有メモリの中身が変わるまで最大 1.5 秒監視
        end2 = time.time() + 1.5
        changed = False
        while time.time() < end2:
            self.shm.seek(0); cur = self.shm.read(probe_len); self.shm.seek(0)
            if cur != before:
                changed = True
                break
            time.sleep(0.01)

        # 読み出し（上下反転なし）
        buf = self.shm.read(self.SHM_SIZE); self.shm.seek(0)
        row = np.frombuffer(buf, np.uint8).reshape(self.H, self.STRIDE)
        valid = row[:, : self.W * self.BYTES_PER_PIXEL]
        if self.BPP == 32:
            img = valid.reshape(self.H, self.W, 4)[:, :, :3]  # BGRA→BGR
        elif self.BPP == 24:
            img = valid.reshape(self.H, self.W, 3)
        else:
            c = max(1, self.BYTES_PER_PIXEL)
            img = valid.reshape(self.H, self.W, c)[:, :, :3]

        if self.debug:
            print(f"[cam] changed={changed}")
        return img

    def close(self):
        try:
            if self.proc and self.proc.stdin:
                # finalize も ASCII と UTF-16LE 両方
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
