// CAM1_bridge_min.cpp  — PCH無し・スレッド無しの連続キャプチャ版
#define WIN32_LEAN_AND_MEAN
#define NOMINMAX
#include <windows.h>
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <string>
#include <memory>
#include "SonyCam.h"

#pragma pack(push,1)
struct ShmHeader {
    uint32_t magic;       // 'CBRG' = 0x47524243
    uint32_t width, height;
    uint32_t bpp;         // 8/24/32...
    uint32_t stride;      // 4byte align
    uint64_t frame_id;    // +1/Frame
    uint64_t timestamp_us;
    uint32_t seq;
    uint32_t reserved;
};
#pragma pack(pop)

static inline uint32_t aligned_stride(uint32_t w, uint32_t bppBits) {
    const uint32_t bytes = bppBits / 8;
    return ((w * bytes + 3) / 4) * 4;
}

// パイプから finalize/quit/exit が来たら止める（非ブロッキング）
static bool poll_finalize_nonblock() {
    HANDLE hIn = GetStdHandle(STD_INPUT_HANDLE);
    if (hIn == INVALID_HANDLE_VALUE) return false;
    if (GetFileType(hIn) == FILE_TYPE_PIPE) {
        DWORD avail = 0;
        if (PeekNamedPipe(hIn, nullptr, 0, nullptr, &avail, nullptr) && avail > 0) {
            std::string line;
            if (std::getline(std::cin, line)) {
                if (line == "finalize" || line == "quit" || line == "exit") return true;
            }
        }
    }
    return false; // コンソール直結時は Ctrl+C で終了想定
}

int main() {
    // 共有メモリ名を ASCII で受け取る（Python は "Local\\Cam1Mem\n" を送る）
    char shmA[256];
    std::fputs("Enter the shared memory name: ", stdout);
    std::fflush(stdout);
    if (!std::fgets(shmA, sizeof(shmA), stdin)) shmA[0] = '\0';
    size_t n = std::strlen(shmA);
    while (n && (shmA[n - 1] == '\r' || shmA[n - 1] == '\n')) shmA[--n] = '\0';
    if (n == 0) std::strcpy(shmA, "Local\\Cam1Mem");

    // ANSI → UTF-16
    wchar_t shmW[256];
    int wlen = MultiByteToWideChar(CP_ACP, 0, shmA, -1, shmW, 256);
    if (wlen <= 0) std::wcscpy(shmW, L"Local\\Cam1Mem");

    // カメラ初期化
    std::unique_ptr<CSonyCam> cam(new CSonyCam());
    cam->SetMaxPacketSize();
    cam->SetFeature("AcquisitionMode", "Continuous");
    cam->SetFeature("TriggerMode", "Off");
    cam->SetFeature("ExposureAuto", "Continuous");
    cam->SetFeature("GainAuto", "Continuous");
    cam->StreamStart();

    // ウォームアップ（黒回避）
    PBITMAPINFO bmi0 = cam->GetBMPINFO();
    std::unique_ptr<BYTE[]> warm(new BYTE[bmi0->bmiHeader.biSizeImage]);
    for (int i = 0;i < 3;++i) (void)cam->Capture(warm.get());

    PBITMAPINFO bmi = cam->GetBMPINFO();
    uint32_t W = bmi->bmiHeader.biWidth;
    uint32_t H = bmi->bmiHeader.biHeight;
    uint32_t BPP = bmi->bmiHeader.biBitCount;
    uint32_t STRIDE = aligned_stride(W, BPP);
    size_t   IMG_BYTES = (size_t)STRIDE * H;
    size_t   capBytes = (size_t)bmi->bmiHeader.biSizeImage;
    size_t   copyBytes = (capBytes < IMG_BYTES) ? capBytes : IMG_BYTES;

    // 共有メモリ（ヘッダ + 実際にコピーするバイト数）
    DWORD TOTAL = (DWORD)(sizeof(ShmHeader) + copyBytes);
    HANDLE hMap = CreateFileMappingW(INVALID_HANDLE_VALUE, NULL, PAGE_READWRITE, 0, TOTAL, shmW);
    if (!hMap) { std::fprintf(stderr, "CreateFileMapping failed: %lu\n", GetLastError()); return 1; }
    void* base = MapViewOfFile(hMap, FILE_MAP_ALL_ACCESS, 0, 0, TOTAL);
    if (!base) { std::fprintf(stderr, "MapViewOfFile failed: %lu\n", GetLastError()); CloseHandle(hMap); return 1; }

    ShmHeader* hdr = (ShmHeader*)base;
    uint8_t* px = (uint8_t*)(hdr + 1);
    hdr->magic = 0x47524243; hdr->width = W; hdr->height = H; hdr->bpp = BPP; hdr->stride = STRIDE;
    hdr->frame_id = 0; hdr->timestamp_us = 0; hdr->seq = 0; hdr->reserved = 0;

    // 起動情報を stdout へ（Pythonが拾う）
    std::string serial = cam->GetSerialNumber();
    std::puts(serial.c_str());
    std::printf("WH %u %u BPP %u STRIDE %u\n", W, H, BPP, STRIDE);
    std::fflush(stdout);

    // フレームバッファ（必ず biSizeImage 分）
    std::unique_ptr<BYTE[]> frame(new BYTE[capBytes]);

    // 連続キャプチャ
    uint64_t local_id = 0;
    for (;;) {
        if (poll_finalize_nonblock()) break;

        bool ok = cam->Capture(frame.get());

        // 先頭64KBでゼロ検知 → テストパターン注入
        size_t PROBE = (copyBytes < (size_t)65536) ? copyBytes : (size_t)65536;
        unsigned long long sum = 0;
        for (size_t i = 0;i < PROBE;++i) sum += frame[i];
        if (!ok || sum == 0ULL) {
            int bppB = (int)(BPP / 8);
            for (uint32_t y = 0;y < H;++y) {
                BYTE* row = frame.get() + (size_t)y * STRIDE;
                for (uint32_t x = 0;x < W;++x) {
                    BYTE B = (BYTE)(x & 0xFF), G = (BYTE)(y & 0xFF), R = (BYTE)((x + y) & 0xFF);
                    if (bppB >= 3) {
                        row[x * bppB + 0] = B; row[x * bppB + 1] = G; row[x * bppB + 2] = R;
                        if (bppB == 4) row[x * 4 + 3] = 255;
                    }
                    else {
                        row[x] = B; // Mono8
                    }
                }
            }
        }

        std::memcpy(px, frame.get(), copyBytes);
        hdr->frame_id = ++local_id;
        hdr->timestamp_us = GetTickCount64() * 1000ULL;
        // Sleep(0); // 必要に応じて負荷調整
    }

    cam->StreamStop();
    UnmapViewOfFile(base);
    CloseHandle(hMap);
    return 0;
}
