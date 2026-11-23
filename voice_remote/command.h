#pragma once

#include <stdbool.h>
#include <stdint.h>

bool VoiceCommandsInit(void);
void VoiceCommandObserve(unsigned int id);
void VoiceCommandsSetOnline(bool online);
unsigned int VoiceCommandTake(void);
