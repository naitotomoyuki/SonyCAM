# quick_probe.py
import time, mmap, struct
SHM=r"Local\Cam1Mem"; HDR_FMT="<IIIIIQQII"; HDR=struct.calcsize(HDR_FMT)
m=mmap.mmap(-1, HDR, SHM); 
def hdr():
    m.seek(0); magic,w,h,bpp,stride,fid,ts,seq,_=struct.unpack(HDR_FMT,m.read(HDR)); 
    return magic,w,h,bpp,stride,fid
a=hdr(); time.sleep(0.2); b=hdr()
print("from->to:", a, "=>", b, "changed:", b[-1]!=a[-1])
m.close()
