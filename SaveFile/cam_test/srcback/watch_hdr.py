# watch_hdr.py
import time, mmap, struct
SHM = r"Local\Cam1Mem_HDR1"
HDR_FMT = "<IIIIIQQII"
HDR_SIZE = struct.calcsize(HDR_FMT)

m = mmap.mmap(-1, 65536, SHM)
prev = None
try:
    while True:
        m.seek(0)
        magic,w,h,bpp,stride,fid,ts,seq,res = struct.unpack_from(HDR_FMT, m.read(HDR_SIZE), 0)
        print(f"\rmagic={hex(magic)} size={w}x{h} bpp={bpp} stride={stride} frame_id={fid}", end="")
        time.sleep(0.3)
finally:
    m.close()
