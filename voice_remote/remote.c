#include <stdatomic.h>
#include <stdio.h>
#include <string.h>

#include "lwip/inet.h"
#include "lwip/netifapi.h"
#include "wifi_device.h"

#include "command.h"
#include "config.h"
#include "remote.h"

#define SCAN_DONE 1u
#define SCAN_TIMEOUT_MS 5000u
#define ASSOCIATION_TIMEOUT_MS 15000u
#define WIFI_TIMEOUT_MS 8000u
#define RETRY_MS 1500u

static osEventFlagsId_t wifiFlags;
static WifiScanInfo scanResults[WIFI_SCAN_HOTSPOT_LIMIT];
static atomic_uint disconnectReason;

static void OnWifiScan(int state, int size)
{
    (void)size;
    if (state == WIFI_STATE_AVALIABLE) {
        (void)osEventFlagsSet(wifiFlags, SCAN_DONE);
    }
}

static void OnWifiConnection(int state, WifiLinkedInfo *info)
{
    if (state == WIFI_STATE_NOT_AVALIABLE && info != NULL && info->disconnectedReason != 0) {
        atomic_store_explicit(&disconnectReason, info->disconnectedReason, memory_order_relaxed);
    }
}

static WifiEvent wifiEvents = {
    .OnWifiConnectionChanged = OnWifiConnection,
    .OnWifiScanStateChanged = OnWifiScan,
};

static bool SelectAccessPoint(WifiDeviceConfig *config)
{
    (void)osEventFlagsClear(wifiFlags, SCAN_DONE);
    WifiErrorCode result = Scan();
    if (result != WIFI_SUCCESS) {
        printf("[REMOTE] Wi-Fi scan request failed, code=%d\r\n", (int)result);
        return false;
    }
    uint32_t flags = osEventFlagsWait(wifiFlags, SCAN_DONE, osFlagsWaitAny, VoiceTicks(SCAN_TIMEOUT_MS));
    if ((flags & osFlagsError) != 0) {
        printf("[REMOTE] Wi-Fi scan timed out\r\n");
        return false;
    }

    unsigned int count = WIFI_SCAN_HOTSPOT_LIMIT;
    result = GetScanInfoList(scanResults, &count);
    if (result != WIFI_SUCCESS) {
        printf("[REMOTE] Could not read scan results, code=%d\r\n", (int)result);
        return false;
    }
    const WifiScanInfo *selected = NULL;
    bool seen = false;
    for (unsigned int i = 0; i < count && i < WIFI_SCAN_HOTSPOT_LIMIT; ++i) {
        const WifiScanInfo *entry = &scanResults[i];
        if (strcmp(entry->ssid, VOICE_WIFI_SSID) != 0) {
            continue;
        }
        seen = true;
        if (entry->securityType == WIFI_SEC_TYPE_PSK &&
            (selected == NULL || entry->rssi > selected->rssi)) {
            selected = entry;
        }
    }
    if (selected == NULL) {
        printf("[REMOTE] Scan: " VOICE_WIFI_SSID " %s\r\n", seen ? "seen without PSK" : "not found");
        return false;
    }
    memcpy(config->bssid, selected->bssid, sizeof(config->bssid));
    config->freq = (unsigned int)selected->frequency;
    printf("[REMOTE] Scan: " VOICE_WIFI_SSID " found, frequency=%d MHz RSSI(raw)=%d\r\n",
           selected->frequency, selected->rssi);
    return true;
}

static bool WifiLinked(void)
{
    WifiLinkedInfo info = {0};
    return GetLinkedInfo(&info) == WIFI_SUCCESS && info.connState == WIFI_CONNECTED &&
           strcmp(info.ssid, VOICE_WIFI_SSID) == 0;
}

static struct netif *WaitForAddress(void)
{
    uint32_t started = osKernelGetTickCount();
    while (!WifiLinked()) {
        if (osKernelGetTickCount() - started >= VoiceTicks(ASSOCIATION_TIMEOUT_MS)) {
            printf("[REMOTE] Wi-Fi association timed out, disconnect reason=%u\r\n",
                   atomic_load_explicit(&disconnectReason, memory_order_relaxed));
            return NULL;
        }
        osDelay(VoiceTicks(100));
    }

    struct netif *interface = netifapi_netif_find("wlan0");
    if (interface == NULL || netifapi_dhcp_start(interface) != ERR_OK) {
        printf("[REMOTE] DHCP start failed\r\n");
        return NULL;
    }
    started = osKernelGetTickCount();
    while (WifiLinked() && osKernelGetTickCount() - started < VoiceTicks(WIFI_TIMEOUT_MS)) {
        if (netifapi_dhcp_is_bound(interface) == ERR_OK) {
            printf("[REMOTE] Connected to " VOICE_WIFI_SSID "; DHCP ready\r\n");
            return interface;
        }
        osDelay(VoiceTicks(100));
    }
    (void)netifapi_dhcp_stop(interface);
    printf("[REMOTE] DHCP timed out or link lost\r\n");
    return NULL;
}

static void ReportCommands(struct netif *interface)
{
    printf("[REMOTE] Reporting to http://%s:%u/voice/command\r\n",
           VOICE_BACKEND_IP, (unsigned int)VOICE_BACKEND_PORT);
    VoiceCommandsSetOnline(true);
    uint32_t linkChecked = osKernelGetTickCount();
    for (;;) {
        unsigned int id = VoiceCommandTake();
        if (id != 0 || osKernelGetTickCount() - linkChecked >= VoiceTicks(250)) {
            if (!WifiLinked() || netifapi_dhcp_is_bound(interface) != ERR_OK) {
                if (id != 0) {
                    printf("[VOICE] ID=%u dropped: Wi-Fi link lost\r\n", id);
                }
                break;
            }
            linkChecked = osKernelGetTickCount();
        }
        if (id != 0) {
            const char *error;
            int status = VoicePostCommand(id, &error);
            if (status >= 200 && status < 300) {
                printf("[REMOTE] ID=%u reported HTTP=%d\r\n", id, status);
            } else if (status < 0) {
                printf("[REMOTE] ID=%u report failed: %s\r\n", id, error);
            } else {
                printf("[REMOTE] ID=%u report failed HTTP=%d\r\n", id, status);
            }
        }
        osDelay(VoiceTicks(20));
    }
    VoiceCommandsSetOnline(false);
    printf("[REMOTE] Wi-Fi link lost; reconnecting\r\n");
}

static void RemoteTask(void *argument)
{
    (void)argument;
    if (sizeof(VOICE_WIFI_SSID) < 2 || sizeof(VOICE_WIFI_SSID) > WIFI_MAX_SSID_LEN ||
        sizeof(VOICE_WIFI_PASSWORD) < 9 || sizeof(VOICE_WIFI_PASSWORD) > 64) {
        printf("[REMOTE] Set VOICE_WIFI_SSID and VOICE_WIFI_PASSWORD in voice_remote/wifi_local.h\r\n");
        return;
    }
    uint32_t backendAddress = inet_addr(VOICE_BACKEND_IP);
    if (backendAddress == INADDR_NONE || backendAddress == INADDR_ANY) {
        printf("[REMOTE] Set VOICE_BACKEND_IP to the computer's LAN IPv4 in voice_remote/wifi_local.h\r\n");
        return;
    }
    WifiDeviceConfig config = {
        .securityType = WIFI_SEC_TYPE_PSK,
    };
    memcpy(config.ssid, VOICE_WIFI_SSID, sizeof(VOICE_WIFI_SSID));
    memcpy(config.preSharedKey, VOICE_WIFI_PASSWORD, sizeof(VOICE_WIFI_PASSWORD));
    osDelay(VoiceTicks(2000));
    wifiFlags = osEventFlagsNew(NULL);
    if (wifiFlags == NULL || RegisterWifiEvent(&wifiEvents) != WIFI_SUCCESS) {
        printf("[REMOTE] Could not register Wi-Fi scan and connection events\r\n");
        return;
    }
    for (;;) {
        if (IsWifiActive() != WIFI_STA_ACTIVE && EnableWifi() != WIFI_SUCCESS) {
            printf("[REMOTE] STA start failed\r\n");
            osDelay(VoiceTicks(RETRY_MS));
            continue;
        }
        if (!SelectAccessPoint(&config)) {
            osDelay(VoiceTicks(RETRY_MS));
            continue;
        }
        int network = -1;
        WifiErrorCode added = AddDeviceConfig(&config, &network);
        if (added == WIFI_SUCCESS) {
            printf("[REMOTE] Joining " VOICE_WIFI_SSID "\r\n");
            atomic_store_explicit(&disconnectReason, 0, memory_order_relaxed);
            WifiErrorCode connecting = ConnectTo(network);
            if (connecting == WIFI_SUCCESS) {
                struct netif *interface = WaitForAddress();
                if (interface != NULL) {
                    ReportCommands(interface);
                    (void)netifapi_dhcp_stop(interface);
                }
            } else {
                printf("[REMOTE] Wi-Fi connect request failed, code=%d\r\n", (int)connecting);
            }
            (void)Disconnect();
            (void)RemoveDevice(network);
        } else {
            printf("[REMOTE] Wi-Fi configuration failed, code=%d\r\n", (int)added);
        }
        osDelay(VoiceTicks(RETRY_MS));
    }
}

void VoiceRemoteStart(void)
{
    const osThreadAttr_t attributes = {
        .name = "VoiceRemote",
        .stack_size = 6144,
        .priority = osPriorityNormal,
    };
    if (!VoiceCommandsInit() || osThreadNew(RemoteTask, NULL, &attributes) == NULL) {
        printf("[REMOTE] Could not start Wi-Fi task\r\n");
    }
}
