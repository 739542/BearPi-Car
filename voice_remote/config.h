#pragma once

#include "wifi_local.h"

#ifndef VOICE_WIFI_SSID
#define VOICE_WIFI_SSID ""
#endif
#ifndef VOICE_WIFI_PASSWORD
#define VOICE_WIFI_PASSWORD ""
#endif
#ifndef VOICE_BACKEND_IP
#define VOICE_BACKEND_IP ""
#endif
#ifndef VOICE_BACKEND_PORT
#define VOICE_BACKEND_PORT 8766
#endif

/* Zero disables a learned word until its ID has been measured. */
#ifndef VOICE_LEFT_ID
#define VOICE_LEFT_ID 0
#endif
#ifndef VOICE_RIGHT_ID
#define VOICE_RIGHT_ID 0
#endif
#ifndef VOICE_BLUE_CUP_ID
#define VOICE_BLUE_CUP_ID 0
#endif

#if (VOICE_LEFT_ID != 0 && (VOICE_LEFT_ID < 5 || VOICE_LEFT_ID > 21)) || \
    (VOICE_RIGHT_ID != 0 && (VOICE_RIGHT_ID < 5 || VOICE_RIGHT_ID > 21)) || \
    (VOICE_BLUE_CUP_ID != 0 && (VOICE_BLUE_CUP_ID < 5 || VOICE_BLUE_CUP_ID > 21)) || \
    (VOICE_LEFT_ID != 0 && VOICE_LEFT_ID == VOICE_RIGHT_ID) || \
    (VOICE_BLUE_CUP_ID != 0 && (VOICE_BLUE_CUP_ID == VOICE_LEFT_ID || VOICE_BLUE_CUP_ID == VOICE_RIGHT_ID))
#error Voice IDs must be distinct learned command IDs (5-21), or zero.
#endif
#if VOICE_BACKEND_PORT < 1 || VOICE_BACKEND_PORT > 65535
#error Voice backend port must be between 1 and 65535.
#endif
