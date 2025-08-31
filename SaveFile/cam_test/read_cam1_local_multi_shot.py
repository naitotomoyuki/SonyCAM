# read_cam1_local_oneshot_fixed.py
# -*- coding: utf-8 -*-
import ctypes as C
from ctypes import wintypes as W
import struct
import numpy as np
import cv2, time

# ===== WinAPI prototypes (これが超重要) =====
kernel32 = C.windll.kernel32

OpenFileMappingW = kernel32.OpenFileMappingW
OpenFileMappingW.argtypes = [W.DWORD, W.BOOL, W.LPCWSTR]
OpenFileMappingW.restype  = W.HANDLE

MapViewOfFile = kernel32.MapViewOfFile
MapViewOfFile.argtypes = [W.HANDLE, W.DWORD, W.DWORD, W.DWORD, C.c_size_t]
MapViewOfFile.restype  = W.LPVOID

UnmapViewOfFile = kernel32.UnmapViewOfFile
UnmapViewOfFile.argtypes = [W.LPCVOID]
UnmapViewOfFile.restype  = W.BOOL

CloseHandle = kernel32.CloseHandle
CloseHandle.argtypes = [W.HANDLE]
CloseHandle.restype  = W.BOOL

GetLastError = kernel32.GetLastError
GetLastError.restype = W.DWORD

# ===== 共有メモリ名 =====
TAG = u"Local\\Cam1Mem"

# ===== C++側と一致するヘッダー定義 (44 bytes) =====
# struct ShmHeader {
#   uint32 magic;         // 'CBRG' = 0x47524243
#   uint32 width, height; // W,H
#   uint32 bpp;           // 8/24/32...
#   uint32 stride;        // bytes per row (4B align)
#   uint64 frame_id;
#   uint64 timestamp_us;
#   uint32 seq;
#   uint32 reserved;
# };
HDR_FMT  = "<IIIIIQQII"              # little-endian
HDR_SIZE = struct.calcsize(HDR_FMT)  # 44
MAGIC    = 0x47524243

FILE_MAP_READ = 0x0004

def open_map(tag=TAG):
    h_map = OpenFileMappingW(FILE_MAP_READ, False, tag)
    if not h_map:
        raise RuntimeError(f"OpenFileMapping failed (tag={tag}) err={GetLastError()}")
    base = MapViewOfFile(h_map, FILE_MAP_READ, 0, 0, 0)  # 全体ビュー
    if not base:
        err = GetLastError()
        CloseHandle(h_map)
        raise RuntimeError(f"MapViewOfFile failed err={err}")
    return h_map, base

def read_header(base_ptr) -> dict:
    buf = C.string_at(base_ptr, HDR_SIZE)
    magic, Wd, Hd, bpp, stride, fid, ts_us, seq, _ = struct.unpack(HDR_FMT, buf)
    if magic != MAGIC:
        raise ValueError(f"magic mismatch: got=0x{magic:08X}, expected=0x{MAGIC:08X}")
    return dict(W=Wd, H=Hd, bpp=bpp, stride=stride, fid=fid, ts_us=ts_us, seq=seq)

def read_image(base_ptr, hdr, assume_bgr=True):
    Wd, Hd, bpp, stride = hdr["W"], hdr["H"], hdr["bpp"], hdr["stride"]
    Cc = bpp // 8
    if Cc not in (1,3,4):
        raise ValueError(f"unsupported channels from bpp={bpp}")
    if stride == 0:
        stride = Wd * Cc

    pix_ptr = base_ptr + HDR_SIZE
    raw = C.string_at(pix_ptr, stride * Hd)
    arr = np.frombuffer(raw, np.uint8).copy()  # 一旦コピーしてからreshape

    if Cc == 1:
        img = arr.reshape(Hd, stride)[:, :Wd]
        return cv2.cvtColor(img, cv2.COLOR_GRAY2BGR)
    tight = arr.reshape(Hd, stride)[:, :Wd*Cc].reshape(Hd, Wd, Cc)
    if Cc == 4:
        return cv2.cvtColor(tight, cv2.COLOR_BGRA2BGR)  # BGRA→BGR想定
    return tight if assume_bgr else tight[..., ::-1]     # RGB→BGR反転

def main():
# ループ例（100msごとに最新を表示）

    h_map, base = open_map(TAG)
    last_fid = None
    try:
        while True:
            hdr = read_header(base)
            if hdr["fid"] != last_fid:
                img = read_image(base, hdr, assume_bgr=True)
                cv2.imshow("CAM1 interval", img)
                if cv2.waitKey(1) == 27:  # ESC
                    break
                last_fid = hdr["fid"]
            time.sleep(0.1)  # 100ms
    finally:
        UnmapViewOfFile(base)
        CloseHandle(h_map)


if __name__ == "__main__":
    main()
