// CAM1_pattern_only.cpp  —— 共有メモリに動く色パターンを書き続けるだけ
// 出力名は CAM1.exe でOK（既存と差し替え）
#include "stdafx.h"   // ←PCHを使っている場合のみコメントを外す

// CAM1_bridge_min.cpp  — 連続キャプチャ（コマンド不要）/ パターン注入なし / PCHなし版
#define WIN32_LEAN_AND_MEAN
#define NOMINMAX
#include <windows.h>
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <string>
#include <memory>
#include "SonyCam.h"   // 付属ラッパー

#pragma pack(push,1)
struct ShmHeader {
    uint32_t magic;       // 'CBRG' = 0x47524243
    uint32_t width, height;
    uint32_t bpp;         // 8/24/32...
    uint32_t stride;      // 4byte align
    uint64_t frame_id;    // +1 / frame
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
    // 共有メモリ名（ASCII 1行）
    char shmA[256];
    std::fputs("Enter the shared memory name: ", stdout);
    std::fflush(stdout);
    if (!std::fgets(shmA, sizeof(shmA), stdin)) shmA[0] = '\0';
    size_t n = std::strlen(shmA);
    while (n && (shmA[n - 1] == '\r' || shmA[n - 1] == '\n')) shmA[--n] = '\0';
    if (n == 0) std::strcpy(shmA, "Local\\Cam1Mem");

    // ANSI → UTF-16（共有メモリ名）
    wchar_t shmW[256];
    int wlen = MultiByteToWideChar(CP_ACP, 0, shmA, -1, shmW, 256);
    if (wlen <= 0) std::wcscpy(shmW, L"Local\\Cam1Mem");

    // ===== カメラ初期化 =====
    std::unique_ptr<CSonyCam> cam(new CSonyCam());
    cam->SetMaxPacketSize();

    // まずは確実にフリーラン・オートOFFで明るめに
    cam->SetFeature("AcquisitionMode", "Continuous");
    cam->SetFeature("TriggerMode", "Off");
    cam->SetFeature("ExposureAuto", "Off");
    cam->SetFeature("GainAuto", "Off");
    cam->SetFeature("ExposureTime", 10000.0); // 10ms
    cam->SetFeature("Gain", 6.0);
    cam->SetFeature("PixelFormat", "RGB8Packed");
    // 必要なら PixelFormat を明示（機種依存。無視される場合あり）
    //cam->SetFeature("PixelFormat", "BGR8");  // DIB が 32bpp なら未指定でOK

    cam->StreamStart();

    // ウォームアップ（黒回避）
    {
        PBITMAPINFO bmi0 = cam->GetBMPINFO();
        std::unique_ptr<BYTE[]> warm(new BYTE[bmi0->bmiHeader.biSizeImage]);
        for (int i = 0; i < 3; ++i) (void)cam->Capture(warm.get());
    }

    PBITMAPINFO bmi = cam->GetBMPINFO();
    uint32_t W = bmi->bmiHeader.biWidth;
    uint32_t H = bmi->bmiHeader.biHeight;
    uint32_t BPP = bmi->bmiHeader.biBitCount;   // 8/24/32 ...
    uint32_t STRIDE = aligned_stride(W, BPP);
    size_t   IMG_BYTES = (size_t)STRIDE * H;
    size_t   capBytes = (size_t)bmi->bmiHeader.biSizeImage; // DIB実サイズ
    size_t   copyBytes = (capBytes < IMG_BYTES) ? capBytes : IMG_BYTES;

    // ===== 共有メモリ確保（ヘッダ + 実転送バイト数）=====
    DWORD TOTAL = (DWORD)(sizeof(ShmHeader) + copyBytes);
    HANDLE hMap = CreateFileMappingW(INVALID_HANDLE_VALUE, NULL, PAGE_READWRITE, 0, TOTAL, shmW);
    if (!hMap) { std::fprintf(stderr, "CreateFileMapping failed: %lu\n", GetLastError()); return 1; }
    void* base = MapViewOfFile(hMap, FILE_MAP_ALL_ACCESS, 0, 0, TOTAL);
    if (!base) { std::fprintf(stderr, "MapViewOfFile failed: %lu\n", GetLastError()); CloseHandle(hMap); return 1; }

    ShmHeader* hdr = (ShmHeader*)base;
    uint8_t* px = (uint8_t*)(hdr + 1);

    hdr->magic = 0x47524243;  // 'CBRG'
    hdr->width = W; hdr->height = H; hdr->bpp = BPP; hdr->stride = STRIDE;
    hdr->frame_id = 0; hdr->timestamp_us = 0; hdr->seq = 0; hdr->reserved = 0;

    // 起動情報 → stdout（Python 側が拾う）
    std::string serial = cam->GetSerialNumber();
    std::puts(serial.c_str());
    std::printf("WH %u %u BPP %u STRIDE %u\n", W, H, BPP, STRIDE);
    std::fflush(stdout);

    // フレームバッファ（必ず biSizeImage 分）
    std::unique_ptr<BYTE[]> frame(new BYTE[capBytes]);

    // ===== 連続キャプチャ＆共有メモリ書き出し =====
    uint64_t local_id = 0;
    for (;;) {
        if (poll_finalize_nonblock()) break;

        bool ok = cam->Capture(frame.get());

        // 先頭64KBの合計で“ゼロっぽさ”を観測（パターン注入はしない）
        size_t PROBE = (copyBytes < (size_t)65536) ? copyBytes : (size_t)65536;
        unsigned long long sum = 0;
        for (size_t i = 0; i < PROBE; ++i) sum += frame[i];

        // 30フレームに1回だけ SUM を出す（デバッグ用）
        static uint64_t cnt = 0;
        if ((cnt++ % 30) == 0) {
            std::printf("SUM %llu\n", (unsigned long long)sum);
            std::fflush(stdout);
        }

        // 共有メモリへコピー
        std::memcpy(px, frame.get(), copyBytes);
        hdr->frame_id = ++local_id;
        hdr->timestamp_us = GetTickCount64() * 1000ULL; // お手軽タイムスタンプ

        // 少し譲る（必要なら調整）
        // Sleep(0);
    }

    cam->StreamStop();
    UnmapViewOfFile(base);
    CloseHandle(hMap);
    return 0;
}
