#include <stdio.h>
#include <string.h>

#include "command.h"
#include "config.h"
#include "remote.h"

#define FORWARD_ID 22u
#define BACKWARD_ID 23u
#define STOP_ID 24u
#define QUEUE_CAPACITY 8u
#define EVENT_MAX_AGE_MS 500u

static osMutexId_t inputLock;
static bool online;
static int lastId = -1;
static unsigned int count;
static struct {
    unsigned int id;
    uint32_t observedAt;
} pending[QUEUE_CAPACITY];

bool VoiceCommandsInit(void)
{
    inputLock = osMutexNew(NULL);
    return inputLock != NULL;
}

void VoiceCommandObserve(unsigned int id)
{
    if (inputLock == NULL) {
        return;
    }
    osMutexAcquire(inputLock, osWaitForever);
    bool changed = lastId >= 0 && (unsigned int)lastId != id;
    lastId = (int)id;
    if (changed && id != 0 &&
        (id == FORWARD_ID || id == BACKWARD_ID || id == STOP_ID ||
         id == VOICE_LEFT_ID || id == VOICE_RIGHT_ID || id == VOICE_BLUE_CUP_ID)) {
        if (!online) {
            printf("[VOICE] ID=%u dropped: Wi-Fi offline\r\n", id);
        } else {
            if (id == STOP_ID) {
                /* Keep distinct stop events and discard commands queued before the stop. */
                unsigned int kept = 0;
                for (unsigned int i = 0; i < count; ++i) {
                    if (pending[i].id == STOP_ID) {
                        pending[kept++] = pending[i];
                    } else {
                        printf("[VOICE] ID=%u dropped: superseded by stop\r\n", pending[i].id);
                    }
                }
                count = kept;
            }
            if (count < QUEUE_CAPACITY) {
                pending[count].id = id;
                pending[count++].observedAt = osKernelGetTickCount();
            } else {
                printf("[VOICE] ID=%u dropped: report queue full\r\n", id);
            }
        }
    }
    osMutexRelease(inputLock);
}

void VoiceCommandsSetOnline(bool connected)
{
    osMutexAcquire(inputLock, osWaitForever);
    online = connected;
    if (!online) {
        for (unsigned int i = 0; i < count; ++i) {
            printf("[VOICE] ID=%u dropped: Wi-Fi link lost\r\n", pending[i].id);
        }
        count = 0;
    }
    osMutexRelease(inputLock);
}

unsigned int VoiceCommandTake(void)
{
    osMutexAcquire(inputLock, osWaitForever);
    unsigned int id = 0;
    while (count != 0) {
        id = pending[0].id;
        uint32_t age = osKernelGetTickCount() - pending[0].observedAt;
        --count;
        memmove(pending, pending + 1, count * sizeof(pending[0]));
        if (age < VoiceTicks(EVENT_MAX_AGE_MS)) {
            break;
        }
        printf("[VOICE] ID=%u dropped: report expired\r\n", id);
        id = 0;
    }
    osMutexRelease(inputLock);
    return id;
}
