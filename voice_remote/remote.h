#pragma once

#include <stdbool.h>
#include <stdint.h>
#include "cmsis_os2.h"

static inline uint32_t VoiceTicks(uint32_t milliseconds)
{
    return (milliseconds * osKernelGetTickFreq() + 999u) / 1000u;
}

void VoiceRemoteStart(void);
int VoicePostCommand(unsigned int id, const char **error);
