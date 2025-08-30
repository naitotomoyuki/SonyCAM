// CAM1_pattern_only.cpp  —— 共有メモリに動く色パターンを書き続けるだけ
// 出力名は CAM1.exe でOK（既存と差し替え）
#include "stdafx.h"   // ←PCHを使っている場合のみコメントを外す

#define WIN32_LEAN_AND_MEAN
#define NOMINMAX
#include <windows.h>
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <string>
#include <iostream>

#pragma pack(push,1)
struct ShmHeader {
    uint32_t magic;       // 'CBRG' = 0x47524243
    uint32_t width, height;
    uint32_t bpp;         // 32固定（BGRA）
    uint32_t stride;      // 4byte align
    uint64_t frame_id;    // +1/frame
    uint64_t timestamp_us;
    uint32_t seq;
    uint32_t reserved;
};
#pragma pack(pop)

static inline uint32_t aligned_stride(uint32_t w, uint32_t bppBits) {
    const uint32_t bytes = bppBits / 8;
    return ((w * bytes + 3) / 4) * 4;
}

// finalize/quit/exit をパイプから受けたら停止（非ブロッキング）
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
    return false;
}

static std::wstring AnsiToWide(const std::string& s) {
    if (s.empty()) return L"";
    int len = MultiByteToWideChar(CP_ACP, 0, s.c_str(), (int)s.size(), nullptr, 0);
    std::wstring out(len, L'\0');
    MultiByteToWideChar(CP_ACP, 0, s.c_str(), (int)s.size(), &out[0], len);
    return out;
}

int main() {
    // 共有メモリ名
    std::cout << "Enter the shared memory name: ";
    std::string shmA; std::getline(std::cin, shmA);
    if (shmA.empty()) shmA = "Local\\Cam1Mem";
    std::wstring shmW = AnsiToWide(shmA);

    // 任意の解像度（今の固定値：2464x2056, 32bpp）
    const uint32_t W = 2464, H = 2056, BPP = 32;
    const uint32_t STRIDE = aligned_stride(W, BPP);
    const size_t   BYTES = (size_t)STRIDE * H;

    // 共有メモリ（ヘッダ+画素）
    const DWORD TOTAL = (DWORD)(sizeof(ShmHeader) + BYTES);
    HANDLE hMap = CreateFileMappingW(INVALID_HANDLE_VALUE, NULL, PAGE_READWRITE, 0, TOTAL, shmW.c_str());
    if (!hMap) { std::fprintf(stderr, "CreateFileMapping failed: %lu\n", GetLastError()); return 1; }
    void* base = MapViewOfFile(hMap, FILE_MAP_ALL_ACCESS, 0, 0, TOTAL);
    if (!base) { std::fprintf(stderr, "MapViewOfFile failed: %lu\n", GetLastError()); CloseHandle(hMap); return 1; }

    ShmHeader* hdr = (ShmHeader*)base;
    uint8_t* px = (uint8_t*)(hdr + 1);

    // ヘッダ
    hdr->magic = 0x47524243;
    hdr->width = W; hdr->height = H; hdr->bpp = BPP; hdr->stride = STRIDE;
    hdr->frame_id = 0; hdr->timestamp_us = 0; hdr->seq = 0; hdr->reserved = 0;

    // 起動情報（stdout）
    std::puts("(pattern-only)");  // シリアル代わり
    std::printf("WH %u %u BPP %u STRIDE %u\n", W, H, BPP, STRIDE);
    std::fflush(stdout);

    // 動くグラデーションを書き続ける（BGRAのうちBGRだけ使用）
    uint64_t id = 0; uint32_t tick = 0;
    for (;;) {
        if (poll_finalize_nonblock()) break;

        for (uint32_t y = 0; y < H; ++y) {
            uint8_t* row = px + (size_t)y * STRIDE;
            for (uint32_t x = 0; x < W; ++x) {
                row[x * 4 + 0] = (uint8_t)((x + tick) & 0xFF);     // B
                row[x * 4 + 1] = (uint8_t)((y + tick) & 0xFF);     // G
                row[x * 4 + 2] = (uint8_t)(((x + y) + tick) & 0xFF); // R
                row[x * 4 + 3] = 255;                               // A
            }
        }
        hdr->frame_id = ++id;
        hdr->timestamp_us = GetTickCount64() * 1000ULL;

        ++tick;
        Sleep(15); // およそ 60fps
    }

    UnmapViewOfFile(base);
    CloseHandle(hMap);
    return 0;
}
