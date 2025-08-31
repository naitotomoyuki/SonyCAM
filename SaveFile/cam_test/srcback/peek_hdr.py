# peek_hdr.py
import mmap, struct
SHM = r"Local\Cam1Mem_HDR1"  # ← CAM1.exe に入力した名前と同じにする
HDR_FMT = "<IIIIIQQII"
HDR_SIZE = struct.calcsize(HDR_FMT)

m = mmap.mmap(-1, 65536, SHM)
hdr = m.read(HDR_SIZE)
magic,w,h,bpp,stride,fid,ts,seq,res = struct.unpack_from(HDR_FMT, hdr, 0)
print(f"magic={hex(magic)} w={w} h={h} bpp={bpp} stride={stride} frame_id={fid}")
m.close()
