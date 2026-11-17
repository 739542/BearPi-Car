#include <stdio.h>
#include <unistd.h>
#include "ohos_init.h"
#include "cmsis_os2.h"
#include "wifiiot_gpio.h"
#include "wifiiot_gpio_ex.h"
#include "wifiiot_pwm.h"
#include "hi_at.h"

/* Fixed wiring: DIR1=IO10, PWM1=IO7, PWM2=IO8, DIR2=IO12.
 * AT+CAR=<0..6> on existing USB debug UART0; no competing UART reader.
 * No automatic motion. One accepted command runs at most 500 ms.
 */
static osMutexId_t g_lock;
static char g_request;
static int g_busy;
static int g_ready;
static char g_drive;
static int joy_l, joy_r;
static unsigned int g_drive_tick;

static unsigned int Stop(void)
{
    unsigned int error = 0;
    PwmStop(WIFI_IOT_PWM_PORT_PWM0);
    PwmStop(WIFI_IOT_PWM_PORT_PWM1);
    error |= GpioSetOutputVal(WIFI_IOT_GPIO_IDX_7, WIFI_IOT_GPIO_VALUE0);
    error |= GpioSetOutputVal(WIFI_IOT_GPIO_IDX_8, WIFI_IOT_GPIO_VALUE0);
    error |= IoSetFunc(WIFI_IOT_IO_NAME_GPIO_7, WIFI_IOT_IO_FUNC_GPIO_7_GPIO);
    error |= IoSetFunc(WIFI_IOT_IO_NAME_GPIO_8, WIFI_IOT_IO_FUNC_GPIO_8_GPIO);
    error |= GpioSetDir(WIFI_IOT_GPIO_IDX_7, WIFI_IOT_GPIO_DIR_OUT);
    error |= GpioSetDir(WIFI_IOT_GPIO_IDX_8, WIFI_IOT_GPIO_DIR_OUT);
    return error;
}

/* 7/8 forward arcs; 9/a rearward arcs. Left/right refer to travel side. */
static int LeftForward(char cmd)
{
    return cmd == '4' || cmd == '6' || cmd == '7' || cmd == '8';
}
static int RightForward(char cmd)
{
    return cmd == '4' || cmd == '5' || cmd == '7' || cmd == '8';
}
static int Reverses(char before, char after)
{
    return before && after && (LeftForward(before) != LeftForward(after) ||
                               RightForward(before) != RightForward(after));
}

static unsigned int StartWheels(char cmd, int l, int r)
{
    unsigned int error = 0;
    /* M1 left, M2 right. Low is forward, verified with this wiring. */
    WifiIotGpioValue left = LeftForward(cmd) ? WIFI_IOT_GPIO_VALUE0 : WIFI_IOT_GPIO_VALUE1;
    WifiIotGpioValue right = RightForward(cmd) ? WIFI_IOT_GPIO_VALUE0 : WIFI_IOT_GPIO_VALUE1;
    error |= GpioSetOutputVal(WIFI_IOT_GPIO_IDX_10, left);
    error |= GpioSetOutputVal(WIFI_IOT_GPIO_IDX_12, right);
    if (error != 0) return error;
    /* Stop() sets both enable pins to GPIO outputs low before a change.
     * Hold an enable high for full duty, without a PWM counter edge. */
    if (l == 100) {
        error = GpioSetOutputVal(WIFI_IOT_GPIO_IDX_7, WIFI_IOT_GPIO_VALUE1);
    } else if (l != 0) {
        error |= IoSetFunc(WIFI_IOT_IO_NAME_GPIO_7, WIFI_IOT_IO_FUNC_GPIO_7_PWM0_OUT);
        if (error == 0) error |= PwmStart(WIFI_IOT_PWM_PORT_PWM0, l * 400, 40000);
    }
    if (error != 0) return error;
    if (r == 100) {
        error = GpioSetOutputVal(WIFI_IOT_GPIO_IDX_8, WIFI_IOT_GPIO_VALUE1);
    } else if (r != 0) {
        error |= IoSetFunc(WIFI_IOT_IO_NAME_GPIO_8, WIFI_IOT_IO_FUNC_GPIO_8_PWM1_OUT);
        if (error == 0) error |= PwmStart(WIFI_IOT_PWM_PORT_PWM1, r * 400, 40000);
    }
    return error;
}

static unsigned int Start(char cmd)
{
    return StartWheels(cmd, cmd == '2' ? 0 : (cmd == '7' || cmd == '9' ? 20 : 30),
                       cmd == '1' ? 0 : (cmd == '8' || cmd == 'a' ? 20 : 30));
}

void CarWifiStart(void);
int CarSetWheels(int l, int r, int fast)
{
    if (l < -30 || l > 30 || r < -30 || r > 30) return 1;
    osMutexAcquire(g_lock, osWaitForever);
    if (!g_ready || g_busy || g_request) { osMutexRelease(g_lock); return 1; }
    /* Keep the lower half of the joystick unchanged. Above that, increase
     * the outer wheel up to the selected ceiling and preserve the requested turning ratio. */
    int al = l < 0 ? -l : l;
    int ar = r < 0 ? -r : r;
    int peak = al > ar ? al : ar;
    int ceiling = fast ? 100 : 60;
    int target = peak > 15 ? 15 + (ceiling - 15) * (peak - 15) / 15 : peak;
    joy_l = peak ? l * target / peak : 0;
    joy_r = peak ? r * target / peak : 0;
    g_drive = (l || r) ? 'j' : 0;
    g_drive_tick = osKernelGetTickCount();
    osMutexRelease(g_lock);
    return 0;
}

void CarWifiStop(void)
{
    osMutexAcquire(g_lock, osWaitForever);
    g_drive = 0;
    g_request = 's';
    osMutexRelease(g_lock);
}

/* Each wheel is encoded as two decimal digits: 00=full reverse, 30=idle, 60=full forward. */
static hi_u32 CarJoy(hi_s32 argc, const hi_char **argv)
{
    if (argc != 1 || !argv || !argv[0]) return 1;
    const char *s = argv[0];
    for (int i = 0; i < 4; i++) if (s[i] < '0' || s[i] > '9') return 1;
    if (s[4]) return 1;
    int l = (s[0]-'0')*10 + s[1]-'0'-30;
    int r = (s[2]-'0')*10 + s[3]-'0'-30;
    if (l > 30 || r > 30) return 1;
    if (CarSetWheels(l, r, 0)) return 1;
    hi_at_printf("[CAR] JOY ACK\r\n");
    return 0;
}

static hi_u32 CarCommand(hi_s32 argc, const hi_char **argv)
{
    if (argc != 1 || argv == NULL || argv[0] == NULL ||
        argv[0][0] < '0' || argv[0][0] > '6' || argv[0][1] != '\0') return 1;
    osMutexAcquire(g_lock, osWaitForever);
    if (!g_ready || (argv[0][0] != '0' && (g_busy || g_request != 0 || g_drive != 0))) {
        osMutexRelease(g_lock);
        return 1;
    }
    g_request = argv[0][0] == '0' ? 's' : argv[0][0];
    if (argv[0][0] == '0') g_drive = 0;
    osMutexRelease(g_lock);
    hi_at_printf("OK\r\n");
    return 0;
}

/* Host refreshes every 150ms. Expire even if the host disappears. */
static hi_u32 CarDrive(hi_s32 argc, const hi_char **argv)
{
    if (argc != 1 || argv == NULL || argv[0] == NULL || argv[0][0] == '\0' || argv[0][1] != '\0' ||
        !(argv[0][0] == '0' || (argv[0][0] >= '3' && argv[0][0] <= '9') || argv[0][0] == 'a')) return 1;
    osMutexAcquire(g_lock, osWaitForever);
    if (!g_ready || (argv[0][0] != '0' && (g_busy || g_request != 0))) {
        osMutexRelease(g_lock); return 1;
    }
    g_drive = argv[0][0] == '0' ? 0 : argv[0][0];
    g_drive_tick = osKernelGetTickCount();
    if (argv[0][0] == '0') g_request = 's';
    osMutexRelease(g_lock);
    hi_at_printf("[CAR] DRIVE ACK %c\r\n", argv[0][0]);
    return 0;
}

static hi_u32 CarInfo(hi_s32 argc, const hi_char **argv)
{
    (void)argc;
    (void)argv;
    hi_at_printf("[CAR] VERSION car-stage1-v12\r\nOK\r\n");
    return 0;
}

static const at_cmd_func g_commands[] = {
    { "+CAR", 4, CarInfo, NULL, CarCommand, NULL },
    { "+JOY", 4, NULL, NULL, CarJoy, NULL },
    { "+DRIVE", 6, NULL, NULL, CarDrive, NULL }
};

static void MotorTask(void *arg)
{
    unsigned int error;
    (void)arg;
    error = GpioInit();
    error |= Stop();
    error |= IoSetFunc(WIFI_IOT_IO_NAME_GPIO_10, WIFI_IOT_IO_FUNC_GPIO_10_GPIO);
    error |= IoSetFunc(WIFI_IOT_IO_NAME_GPIO_12, WIFI_IOT_IO_FUNC_GPIO_12_GPIO);
    error |= GpioSetDir(WIFI_IOT_GPIO_IDX_10, WIFI_IOT_GPIO_DIR_OUT);
    error |= GpioSetDir(WIFI_IOT_GPIO_IDX_12, WIFI_IOT_GPIO_DIR_OUT);
    error |= GpioSetOutputVal(WIFI_IOT_GPIO_IDX_10, WIFI_IOT_GPIO_VALUE0);
    error |= GpioSetOutputVal(WIFI_IOT_GPIO_IDX_12, WIFI_IOT_GPIO_VALUE0);
    error |= PwmInit(WIFI_IOT_PWM_PORT_PWM0);
    error |= PwmInit(WIFI_IOT_PWM_PORT_PWM1);
    if (error != 0) { Stop(); printf("[CAR] INIT ERROR %u; keep battery OFF\r\n", error); return; }
    osMutexAcquire(g_lock, osWaitForever);
    g_ready = 1;
    osMutexRelease(g_lock);
    printf("\r\n[CAR] READY - STOPPED - car-stage1-v12\r\n");
    printf("[CAR] AT+CAR=n: 1=left 2=right 3=back 4=forward 5=left turn 6=right turn 0=stop\r\n");
    printf("[CAR] Normal 60%% / Sport 100%% PWM; pulse tests 30%%; each test 500ms; wheels OFF ground; no auto-send\r\n");
    int active_l = 0, active_r = 0;
    char active = 0;
    char last_motion = 0;
    unsigned int stopped_at = 0;
    while (1) {
        char cmd;
        osMutexAcquire(g_lock, osWaitForever);
        cmd = g_request;
        g_request = 0;
        if (cmd >= '1' && cmd <= '6') g_busy = 1;
        osMutexRelease(g_lock);
        if (cmd >= '1' && cmd <= '6') {
            error = Stop();
            usleep(100000);
            osMutexAcquire(g_lock, osWaitForever);
            int cancelled = g_request == 's';
            osMutexRelease(g_lock);
            if (!cancelled && error == 0) error = Start(cmd);
            printf("[CAR] TEST %c error=%u\r\n", cmd, error);
            unsigned int began = osKernelGetTickCount();
            unsigned int limit = osKernelGetTickFreq() / 2;
            while (!cancelled && error == 0 && (unsigned int)(osKernelGetTickCount() - began) < limit) {
                usleep(10000);
                osMutexAcquire(g_lock, osWaitForever);
                cancelled = g_request == 's';
                osMutexRelease(g_lock);
            }
            error |= Stop();
            last_motion = cmd;
            stopped_at = osKernelGetTickCount();
            active = 0;
            printf("[CAR] STOP error=%u\r\n", error);
            if (error != 0) {
                osMutexAcquire(g_lock, osWaitForever);
                g_ready = 0;
                osMutexRelease(g_lock);
                printf("[CAR] FAULT - disconnect battery\r\n"); return;
            }
            osMutexAcquire(g_lock, osWaitForever);
            g_request = 0;
            g_busy = 0;
            osMutexRelease(g_lock);
        } else if (cmd == 's') {
            Stop();
            if (active) stopped_at = osKernelGetTickCount();
            active = 0;
            printf("[CAR] STOP\r\n");
        }
        osMutexAcquire(g_lock, osWaitForever);
        if (g_drive && (unsigned int)(osKernelGetTickCount() - g_drive_tick) >=
            osKernelGetTickFreq() * 7 / 10) {
            g_drive = 0;
            printf("[CAR] WATCHDOG STOP\r\n");
        }
        char desired = g_drive;
        int analog = desired == 'j';
        int l = analog ? joy_l : 0, r = analog ? joy_r : 0;
        if (analog) desired = l >= 0 ? (r >= 0 ? '4' : '6') : (r >= 0 ? '5' : '3');
        if (desired != active || l != active_l || r != active_r) {
            error = 0;
            if (active) {
                error = Stop();
                stopped_at = osKernelGetTickCount();
                active = 0;
            }
            /* Fresh start and same-direction speed changes have no fixed delay.
             * Reverse only after 100 ms stopped. Never sleep while holding lock. */
            int waiting = Reverses(last_motion, desired) &&
                (unsigned int)(osKernelGetTickCount() - stopped_at) < osKernelGetTickFreq() / 10;
            if (desired && !waiting && error == 0) {
                error = analog ? StartWheels(desired, l < 0 ? -l : l, r < 0 ? -r : r) : Start(desired);
                if (error == 0) { active = desired; last_motion = desired; }
            }
            if (error != 0) { g_ready = 0; g_drive = 0; Stop(); active = 0; }
            active_l = active ? l : 0; active_r = active ? r : 0;
            if (!waiting || error != 0)
                printf("[CAR] DRIVE STATE %c error=%u\r\n", active ? active : '0', error);
            if (error != 0) printf("[CAR] FAULT\r\n");
        }
        osMutexRelease(g_lock);
        usleep(10000);
    }
}

static void CarInit(void)
{
    g_lock = osMutexNew(NULL);
    if (g_lock == NULL) return;
    if (hi_at_register_cmd(g_commands, 3) != 0) {
        printf("[CAR] AT registration failed; keep battery OFF\r\n");
        return;
    }
    osThreadAttr_t attr = {0};
    attr.name = "CarMotorTest";
    attr.stack_size = 4096;
    attr.priority = osPriorityNormal;
    if (osThreadNew(MotorTask, NULL, &attr) == NULL) printf("[CAR] thread creation failed\r\n");
    else CarWifiStart();
}
APP_FEATURE_INIT(CarInit);
