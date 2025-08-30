// CamBridge.cpp - Console bridge for Python (no PCH)
#include "stdafx.h"
#include <windows.h>
#include <iostream>
#include <string>
#include <memory>
#include "SonyCam.h"

static const wchar_t* kShmName = L"Local\\Cam1Mem";
static const int      kW = 2464;   // ←必要なら変更
static const int      kH = 2056;   // ←必要なら変更
static const size_t   kShmSize = static_cast<size_t>(kW) * kH * 4; // RGBA

std::unique_ptr<CSonyCam> gCam;
PBITMAPINFO gBmi = nullptr;
std::unique_ptr<BYTE[]> gFrame;

HANDLE gMap = nullptr;
LPVOID gBuf = nullptr;

static void die(const char* m, DWORD e = GetLastError()) {
    std::cerr << m << " (err=" << e << ")" << std::endl;
    ExitProcess(1);
}

static bool InitSharedMemory() {
    gMap = CreateFileMappingW(INVALID_HANDLE_VALUE, NULL, PAGE_READWRITE, 0, (DWORD)kShmSize, kShmName);
    if (!gMap) return false;
    gBuf = MapViewOfFile(gMap, FILE_MAP_ALL_ACCESS, 0, 0, kShmSize);
    if (!gBuf) { CloseHandle(gMap); gMap = nullptr; return false; }
    return true;
}


static void InitCamera() {
    gCam = std::make_unique<CSonyCam>();
    gCam->SetMaxPacketSize();
    gCam->SetFeature("AcquisitionMode", "Continuous");
    gCam->StreamStart();

    gBmi = gCam->GetBMPINFO();
    gFrame = std::make_unique<BYTE[]>(gBmi->bmiHeader.biSizeImage);

    std::string serial = gCam->GetSerialNumber();
    //std::cerr << serial << std::endl;  // ← Python 側が最初に読む1行
    std::cout << serial << std::endl;
}

static void FiniCamera() {
    if (gCam) { gCam->StreamStop(); gCam.reset(); }
}


int main() {
    if (!InitSharedMemory()) { std::cerr << "SHM init failed\n"; return 1; }
    try { InitCamera(); }
    catch (...) {
        std::cerr << "Unknown error during camera initialization." << std::endl;
        return 1;
    }
    std::string cmd;
    while (std::getline(std::cin, cmd)) {
        if (cmd == "capture") {
            if (!gCam->Capture(gFrame.get())) {
                std::cerr << "Failed to capture image." << std::endl;
                continue;
            }
            size_t bytes = gBmi->bmiHeader.biSizeImage;
            if (bytes > kShmSize) bytes = kShmSize;
            memcpy(gBuf, gFrame.get(), bytes);
            std::cerr << "Done!" << std::endl;
        }
        else if (cmd == "finalize") {
            break;
        }
    }
    FiniCamera();
    if (gBuf) UnmapViewOfFile(gBuf);
    if (gMap) CloseHandle(gMap);
    return 0;
}
