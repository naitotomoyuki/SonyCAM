# probe_mem.py
import mmap, time, numpy as np
SHM=r"Local\Cam1Mem"; W,H,BPP,STRIDE=2464,2056,32,9856; TOTAL=H*STRIDE
m=mmap.mmap(-1,TOTAL,SHM); time.sleep(0.05)
m.seek(0); buf=m.read(min(65536,TOTAL)); m.seek(0)
s=int(np.frombuffer(buf,dtype=np.uint8).sum()); print("PROBE-SUM:", s)
m.close()
