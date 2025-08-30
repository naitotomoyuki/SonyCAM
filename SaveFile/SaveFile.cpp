// CAM1_stream_bayer.cpp  （出力名はこれまで通り CAM1.exe でOK）
#include "stdafx.h"   // ←PCHを使っている場合だけ残す（先頭）
#include <windows.h>
#include <atomic>
#include <thread>
#include <chrono>
#include <iostream>
#include <string>
#include <memory>
#include <cstdint>
#include <cstring>
#include "SonyCam.h"

#pragma comment(lib, "Ws2_32.lib")

// 共有メモリヘッダ
#pragma pack(push, 1)
struct ShmHeader {
    uint32_t magic;         // 'CBRG'
    uint32_t width;
    uint32_t height;
    uint32_t bpp;           // 8 for Bayer8
    uint32_t stride;        // 4-byte aligned
    uint64_t frame_id;      // 完了ごとに +1
    uint64_t timestamp_us;  // 参考
    uint32_t seq;           // 予約（将来用）
    uint32_t reserved;
};
#pragma pack(pop)

// ……前略（ヘッダや ShmHeader はそのまま）……

static std::unique_ptr<CSonyCam> gCam;
static PBITMAPINFO gBmi = nullptr;

static HANDLE gMap = nullptr;
static ShmHeader* gHdr = nullptr;
static uint8_t* gPixels = nullptr;
static size_t     gImgBytes = 0;

static std::atomic<bool> gRunning{ true };

static inline uint32_t aligned_stride(uint32_t w, uint32_t bpp) {
    const uint32_t bytes = bpp / 8;
    return ((w * bytes + 3) / 4) * 4;
}

static void die(const char* m, DWORD e = GetLastError()) {
    std::cerr << m << " (err=" << e << ")\n";
    ExitProcess(1);
}

int main() {
    std::ios::sync_with_stdio(false);
    std::cin.tie(nullptr);

    // 共有メモリ名（ASCII）
    std::string shmA;
    std::cerr << "Enter the shared memory name: ";
    std::getline(std::cin, shmA);
    if (shmA.empty()) shmA = "Local\\Cam1Mem";
    std::wstring shmW(shmA.begin(), shmA.end());

    // ---- カメラ初期化（PixelFormatは固定せず、SDKに任せる）----
    try {
        gCam = std::make_unique<CSonyCam>();
        gCam->SetMaxPacketSize();
        gCam->SetFeature("AcquisitionMode", "Continuous");
        gCam->SetFeature("TriggerMode", "Off");
        gCam->SetFeature("ExposureAuto", "Continuous");
        gCam->SetFeature("GainAuto", "Continuous");

        gCam->StreamStart();
        gBmi = gCam->GetBMPINFO();
    }
    catch (...) {
        std::cerr << "Unknown error during camera initialization.\n";
        return 1;
    }

    const uint32_t W = gBmi->bmiHeader.biWidth;
    const uint32_t H = gBmi->bmiHeader.biHeight;
    const uint32_t BPP = gBmi->bmiHeader.biBitCount;           // 8 / 24 / 32 など
    const uint32_t STRIDE = aligned_stride(W, BPP);
    gImgBytes = static_cast<size_t>(STRIDE) * H;

    // 共有メモリ確保（ヘッダ＋実バイト数）
    const DWORD total = static_cast<DWORD>(sizeof(ShmHeader) + gImgBytes);
    gMap = CreateFileMappingW(INVALID_HANDLE_VALUE, NULL, PAGE_READWRITE, 0, total, shmW.c_str());
    if (!gMap) die("CreateFileMapping failed");
    void* base = MapViewOfFile(gMap, FILE_MAP_ALL_ACCESS, 0, 0, total);
    if (!base) die("MapViewOfFile failed");
    gHdr = reinterpret_cast<ShmHeader*>(base);
    gPixels = reinterpret_cast<uint8_t*>(gHdr + 1);

    // ヘッダ初期化（**DIB 情報に合わせる**）
    gHdr->magic = 0x47524243; // 'CBRG'
    gHdr->width = W;
    gHdr->height = H;
    gHdr->bpp = BPP;
    gHdr->stride = STRIDE;
    gHdr->frame_id = 0;
    gHdr->timestamp_us = 0;
    gHdr->seq = 0;
    gHdr->reserved = 0;

    // 起動情報（stdout）
    std::cout << gCam->GetSerialNumber() << "\n";
    std::cout << "WH " << W << " " << H << " BPP " << BPP << " STRIDE " << STRIDE << "\n";

    // ローカルフレーム（**必ず biSizeImage 分**）
    const size_t capBytes = static_cast<size_t>(gBmi->bmiHeader.biSizeImage);
    std::unique_ptr<BYTE[]> frame(new BYTE[capBytes]);

    // ウォームアップ
    for (int i = 0;i < 2;i++) gCam->Capture(frame.get());

    // キャプチャスレッド：常時 frame に受ける→共有メモリへ memcpy
    std::thread capthr([&] {
        while (gRunning.load(std::memory_order_relaxed)) {
            bool ok = gCam->Capture(frame.get());
            (void)ok;
            const size_t bytes = (capBytes < gImgBytes) ? capBytes : gImgBytes;
            std::memcpy(gPixels, frame.get(), bytes);
            gHdr->frame_id++;
            gHdr->timestamp_us = GetTickCount64() * 1000ULL; // お手軽
            std::atomic_thread_fence(std::memory_order_release);
        }
        });

    // 制御スレッド：finalize/quit/exit で停止。EOF は無視（= 何も来なければ走り続ける）
    std::thread ctlthr([&] {
        std::string cmd;
        while (true) {
            if (!std::getline(std::cin, cmd)) {
                // EOF → 無視して続行（Python が何も送らない想定）
                std::this_thread::sleep_for(std::chrono::milliseconds(200));
                continue;
            }
            if (cmd == "finalize" || cmd == "quit" || cmd == "exit") {
                gRunning.store(false, std::memory_order_relaxed);
                break;
            }
        }
        });

    // ここで待機
    ctlthr.join();
    gRunning.store(false, std::memory_order_relaxed);
    capthr.join();

    gCam->StreamStop();
    if (gHdr)    UnmapViewOfFile(gHdr);
    if (gMap)    CloseHandle(gMap);
    return 0;
}

