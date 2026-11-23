#include <stdint.h>
#include <stdio.h>

#include "cmsis_os2.h"
#include "ohos_init.h"
#include "wifiiot_errno.h"
#include "wifiiot_gpio.h"
#include "wifiiot_gpio_ex.h"
#include "wifiiot_i2c.h"
#include "wifiiot_i2c_ex.h"
#include "command.h"
#include "remote.h"

#define VOICE_I2C_BUS WIFI_IOT_I2C_IDX_1
#define VOICE_I2C_BAUDRATE 100000u
#define DF2301Q_ADDRESS_7BIT 0x64u
#define DF2301Q_CMD_ID_REGISTER 0x02u
#define DF2301Q_PLAY_ID_REGISTER 0x03u
#define DF2301Q_WAKE_ID 1u

static unsigned int InitVoiceI2c(void)
{
    unsigned int result = GpioInit();
    if (result != WIFI_IOT_SUCCESS && result != WIFI_IOT_ERR_GPIO_REPEAT_INIT) {
        return result;
    }

    result = IoSetFunc(WIFI_IOT_IO_NAME_GPIO_0, WIFI_IOT_IO_FUNC_GPIO_0_I2C1_SDA);
    if (result != WIFI_IOT_SUCCESS) {
        return result;
    }
    result = IoSetFunc(WIFI_IOT_IO_NAME_GPIO_1, WIFI_IOT_IO_FUNC_GPIO_1_I2C1_SCL);
    if (result != WIFI_IOT_SUCCESS) {
        return result;
    }
    return I2cInit(VOICE_I2C_BUS, VOICE_I2C_BAUDRATE);
}

static bool InitWakeButton(void)
{
    unsigned int result = IoSetFunc(WIFI_IOT_IO_NAME_GPIO_11, WIFI_IOT_IO_FUNC_GPIO_11_GPIO);
    if (result == WIFI_IOT_SUCCESS) {
        result = GpioSetDir(WIFI_IOT_GPIO_IDX_11, WIFI_IOT_GPIO_DIR_IN);
    }
    if (result == WIFI_IOT_SUCCESS) {
        result = IoSetPull(WIFI_IOT_IO_NAME_GPIO_11, WIFI_IOT_IO_PULL_UP);
    }
    if (result != WIFI_IOT_SUCCESS) {
        printf("[VOICE] F1 setup error=0x%08X\r\n", result);
        return false;
    }
    printf("[VOICE] F1 wake ready\r\n");
    return true;
}

static bool WakeVoiceModule(void)
{
    unsigned char payload[] = { DF2301Q_PLAY_ID_REGISTER, DF2301Q_WAKE_ID };
    const WifiIotI2cData request = {
        .sendBuf = payload,
        .sendLen = sizeof(payload),
    };
    unsigned int result = I2cWrite(VOICE_I2C_BUS, DF2301Q_ADDRESS_7BIT << 1, &request);
    if (result != WIFI_IOT_SUCCESS) {
        printf("[VOICE] F1 wake I2C error=0x%08X\r\n", result);
        return false;
    }
    printf("[VOICE] F1 wake sent\r\n");
    return true;
}

static void VoiceTask(void *argument)
{
    (void)argument;
    unsigned int result = InitVoiceI2c();
    if (result != WIFI_IOT_SUCCESS) {
        printf("[VOICE] I2C init error=0x%08X\r\n", result);
        return;
    }

    unsigned char registerAddress = DF2301Q_CMD_ID_REGISTER;
    unsigned char commandId = 0;
    const WifiIotI2cData transfer = {
        .sendBuf = &registerAddress,
        .sendLen = sizeof(registerAddress),
        .receiveBuf = &commandId,
        .receiveLen = sizeof(commandId),
    };
    const uint32_t pollTicks = VoiceTicks(100);
    const uint32_t buttonTicks = VoiceTicks(20);
    uint32_t readAfter = pollTicks;
    uint32_t lastRead = osKernelGetTickCount() - pollTicks;
    bool wakeButtonReady = InitWakeButton();
    bool previousPressed = false;
    bool buttonHeld = false;
    int lastId = -1;
    unsigned int lastError = WIFI_IOT_SUCCESS;

    printf("[VOICE] DF2301Q I2C1 poll started (7-bit address 0x64, 100 kHz)\r\n");
    for (;;) {
        WifiIotGpioValue level;
        if (wakeButtonReady && GpioGetInputVal(WIFI_IOT_GPIO_IDX_11, &level) == WIFI_IOT_SUCCESS) {
            bool pressed = level == WIFI_IOT_GPIO_VALUE0;
            /* Require two matching samples and a release before the next wake. */
            if (pressed == previousPressed && pressed != buttonHeld) {
                buttonHeld = pressed;
                bool moduleReady = readAfter == pollTicks || osKernelGetTickCount() - lastRead >= readAfter;
                if (pressed && moduleReady && WakeVoiceModule()) {
                    lastRead = osKernelGetTickCount();
                    /* Give the module the same settling interval as the vendor driver. */
                    readAfter = VoiceTicks(1000);
                }
            }
            previousPressed = pressed;
        }

        uint32_t now = osKernelGetTickCount();
        if (now - lastRead < readAfter) {
            osDelay(buttonTicks);
            continue;
        }
        lastRead = now;
        readAfter = pollTicks;
        /* The Hi3861 SDK takes the address byte with bit 0 clear. */
        result = I2cWriteread(VOICE_I2C_BUS, DF2301Q_ADDRESS_7BIT << 1, &transfer);
        if (result != WIFI_IOT_SUCCESS) {
            if (result != lastError) {
                printf("[VOICE] I2C read error=0x%08X\r\n", result);
            }
        } else {
            VoiceCommandObserve(commandId);
            if (commandId != lastId) {
                printf("[VOICE] ID=%u\r\n", (unsigned int)commandId);
                lastId = commandId;
            }
        }
        lastError = result;
        osDelay(buttonTicks);
    }
}

static void StartVoiceReader(void)
{
    VoiceRemoteStart();
    const osThreadAttr_t attributes = {
        .name = "VoiceReader",
        .stack_size = 4096,
        .priority = osPriorityNormal,
    };
    if (osThreadNew(VoiceTask, NULL, &attributes) == NULL) {
        printf("[VOICE] Could not start reader task\r\n");
    }
}

APP_FEATURE_INIT(StartVoiceReader);
