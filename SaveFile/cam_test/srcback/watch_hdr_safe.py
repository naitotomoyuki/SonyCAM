# watch_hdr_safe.py — 既存マップのみを監視
import ctypes, ctypes.wintypes as wt, struct, time, sys

SHM = r"Local\Cam1Mem_HDR3"
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
    print("[err] mapping not found. Start CAM1.exe first.")
    sys.exit(1)
pv = MapViewOfFile(hMap, FILE_MAP_READ, 0, 0, 65536)
if not pv:
    err = ctypes.get_last_error(); CloseHandle(hMap)
    print(f"[err] MapViewOfFile failed: {err}")
    sys.exit(1)

try:
    while True:
        buf = (ctypes.c_ubyte * HDR_SIZE).from_address(pv)
        hdr = bytes(buf)
        magic,w,h,bpp,stride,fid,ts,seq,res = struct.unpack_from(HDR_FMT, hdr, 0)
        print(f"\rmagic={hex(magic)} size={w}x{h} bpp={bpp} stride={stride} frame_id={fid}", end="")
        time.sleep(0.3)
except KeyboardInterrupt:
    print()
finally:
    UnmapViewOfFile(pv); CloseHandle(hMap)
