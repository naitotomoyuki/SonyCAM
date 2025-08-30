// CAM1_stream_bayer.cpp  �i�o�͖��͂���܂Œʂ� CAM1.exe ��OK�j
#include "stdafx.h"   // ��PCH���g���Ă���ꍇ�����c���i�擪�j
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

// ���L�������w�b�_
#pragma pack(push, 1)
struct ShmHeader {
    uint32_t magic;         // 'CBRG'
    uint32_t width;
    uint32_t height;
    uint32_t bpp;           // 8 for Bayer8
    uint32_t stride;        // 4-byte aligned
    uint64_t frame_id;      // �������Ƃ� +1
    uint64_t timestamp_us;  // �Q�l
    uint32_t seq;           // �\��i�����p�j
    uint32_t reserved;
};
#pragma pack(pop)

// �c�c�O���i�w�b�_�� ShmHeader �͂��̂܂܁j�c�c

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

    // ���L���������iASCII�j
    std::string shmA;
    std::cerr << "Enter the shared memory name: ";
    std::getline(std::cin, shmA);
    if (shmA.empty()) shmA = "Local\\Cam1Mem";
    std::wstring shmW(shmA.begin(), shmA.end());

    // ---- �J�����������iPixelFormat�͌Œ肹���ASDK�ɔC����j----
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
    const uint32_t BPP = gBmi->bmiHeader.biBitCount;           // 8 / 24 / 32 �Ȃ�
    const uint32_t STRIDE = aligned_stride(W, BPP);
    gImgBytes = static_cast<size_t>(STRIDE) * H;

    // ���L�������m�ہi�w�b�_�{���o�C�g���j
    const DWORD total = static_cast<DWORD>(sizeof(ShmHeader) + gImgBytes);
    gMap = CreateFileMappingW(INVALID_HANDLE_VALUE, NULL, PAGE_READWRITE, 0, total, shmW.c_str());
    if (!gMap) die("CreateFileMapping failed");
    void* base = MapViewOfFile(gMap, FILE_MAP_ALL_ACCESS, 0, 0, total);
    if (!base) die("MapViewOfFile failed");
    gHdr = reinterpret_cast<ShmHeader*>(base);
    gPixels = reinterpret_cast<uint8_t*>(gHdr + 1);

    // �w�b�_�������i**DIB ���ɍ��킹��**�j
    gHdr->magic = 0x47524243; // 'CBRG'
    gHdr->width = W;
    gHdr->height = H;
    gHdr->bpp = BPP;
    gHdr->stride = STRIDE;
    gHdr->frame_id = 0;
    gHdr->timestamp_us = 0;
    gHdr->seq = 0;
    gHdr->reserved = 0;

    // �N�����istdout�j
    std::cout << gCam->GetSerialNumber() << "\n";
    std::cout << "WH " << W << " " << H << " BPP " << BPP << " STRIDE " << STRIDE << "\n";

    // ���[�J���t���[���i**�K�� biSizeImage ��**�j
    const size_t capBytes = static_cast<size_t>(gBmi->bmiHeader.biSizeImage);
    std::unique_ptr<BYTE[]> frame(new BYTE[capBytes]);

    // �E�H�[���A�b�v
    for (int i = 0;i < 2;i++) gCam->Capture(frame.get());

    // �L���v�`���X���b�h�F�펞 frame �Ɏ󂯂遨���L�������� memcpy
    std::thread capthr([&] {
        while (gRunning.load(std::memory_order_relaxed)) {
            bool ok = gCam->Capture(frame.get());
            (void)ok;
            const size_t bytes = (capBytes < gImgBytes) ? capBytes : gImgBytes;
            std::memcpy(gPixels, frame.get(), bytes);
            gHdr->frame_id++;
            gHdr->timestamp_us = GetTickCount64() * 1000ULL; // ����y
            std::atomic_thread_fence(std::memory_order_release);
        }
        });

    // ����X���b�h�Ffinalize/quit/exit �Œ�~�BEOF �͖����i= �������Ȃ���Α��葱����j
    std::thread ctlthr([&] {
        std::string cmd;
        while (true) {
            if (!std::getline(std::cin, cmd)) {
                // EOF �� �������đ��s�iPython ����������Ȃ��z��j
                std::this_thread::sleep_for(std::chrono::milliseconds(200));
                continue;
            }
            if (cmd == "finalize" || cmd == "quit" || cmd == "exit") {
                gRunning.store(false, std::memory_order_relaxed);
                break;
            }
        }
        });

    // �����őҋ@
    ctlthr.join();
    gRunning.store(false, std::memory_order_relaxed);
    capthr.join();

    gCam->StreamStop();
    if (gHdr)    UnmapViewOfFile(gHdr);
    if (gMap)    CloseHandle(gMap);
    return 0;
}

