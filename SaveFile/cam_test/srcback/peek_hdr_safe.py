# peek_hdr_safe.py  — 既存マップのみ open（未存在なら失敗する）
import ctypes, ctypes.wintypes as wt, struct, sys

SHM = r"Local\Cam1Mem_HDR3"   # ← CAM1.exe に入力した名前と一致させる
HDR_FMT = "<IIIIIQQII"
HDR_SIZE = struct.calcsize(HDR_FMT)
FILE_MAP_READ = 0x0004

k32 = ctypes.WinDLL("kernel32", use_last_error=True)
OpenFileMappingW = k32.OpenFileMappingW
OpenFileMappingW.argtypes = [wt.DWORD, wt.BOOL, wt.LPCWSTR]
OpenFileMappingW.restype  = wt.HANDLE
MapViewOfFile = k32.MapViewOfFile
MapViewOfFile.argtypes = [wt.HANDLE, wt.DWORD, wt.DWORD, wt.DWORD, ctypes.c_size_t]
MapViewOfFile.restype  = wt.LPVOID
UnmapViewOfFile = k32.UnmapViewOfFile
UnmapViewOfFile.argtypes = [wt.LPCVOID]
CloseHandle = k32.CloseHandle
CloseHandle.argtypes = [wt.HANDLE]

hMap = OpenFileMappingW(FILE_MAP_READ, False, SHM)
if not hMap:
    err = ctypes.get_last_error()
    print(f"[err] OpenFileMappingW failed: {err}")
    sys.exit(1)

# 64KB だけマップ（サイズ0でも良いが、ここでは十分な先頭だけ）
pv = MapViewOfFile(hMap, FILE_MAP_READ, 0, 0, 65536)
if not pv:
    err = ctypes.get_last_error()
    CloseHandle(hMap)
    print(f"[err] MapViewOfFile failed: {err}")
    sys.exit(1)

buf = (ctypes.c_ubyte * HDR_SIZE).from_address(pv)
hdr = bytes(buf)
magic,w,h,bpp,stride,fid,ts,seq,res = struct.unpack_from(HDR_FMT, hdr, 0)
print(f"magic={hex(magic)} w={w} h={h} bpp={bpp} stride={stride} frame_id={fid}")

UnmapViewOfFile(pv)
CloseHandle(hMap)
