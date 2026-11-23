#include <errno.h>
#include <stdio.h>
#include <string.h>

#include "lwip/inet.h"
#include "lwip/sockets.h"
#include "config.h"
#include "remote.h"

/* One deadline covers connect, send and receipt of the HTTP status line. */
#define REQUEST_MS 180u

static bool WaitSocket(int fd, bool writing, uint32_t started)
{
    uint32_t elapsed = osKernelGetTickCount() - started;
    uint32_t limit = VoiceTicks(REQUEST_MS);
    if (elapsed >= limit) {
        return false;
    }
    unsigned long micros =
        (unsigned long)(limit - elapsed) * 1000000ul / osKernelGetTickFreq();
    struct timeval timeout = { (long)(micros / 1000000ul), (long)(micros % 1000000ul) };
    fd_set ready;
    FD_ZERO(&ready);
    FD_SET(fd, &ready);
    return select(fd + 1, writing ? NULL : &ready, writing ? &ready : NULL, NULL, &timeout) > 0;
}

static bool OpenConnection(int fd, uint32_t started)
{
    unsigned long nonblocking = 1;
    if (lwip_ioctl(fd, FIONBIO, &nonblocking) != 0) {
        return false;
    }
    struct sockaddr_in address = {0};
    address.sin_family = AF_INET;
    address.sin_port = htons(VOICE_BACKEND_PORT);
    address.sin_addr.s_addr = inet_addr(VOICE_BACKEND_IP);
    if (connect(fd, (const struct sockaddr *)&address, sizeof(address)) == 0) {
        return true;
    }
    if (errno != EINPROGRESS || !WaitSocket(fd, true, started)) {
        return false;
    }
    int error = 0;
    socklen_t length = sizeof(error);
    return getsockopt(fd, SOL_SOCKET, SO_ERROR, &error, &length) == 0 && error == 0;
}

static bool SendRequest(int fd, unsigned int id, uint32_t started)
{
    char body[32];
    int bodyLength = snprintf(body, sizeof(body), "{\"command_id\":%u}", id);
    if (bodyLength <= 0 || (size_t)bodyLength >= sizeof(body)) {
        return false;
    }
    char request[224];
    int length = snprintf(request, sizeof(request),
        "POST /voice/command HTTP/1.1\r\nHost: %s:%u\r\n"
        "Content-Type: application/json\r\nContent-Length: %d\r\nConnection: close\r\n\r\n%s",
        VOICE_BACKEND_IP, (unsigned int)VOICE_BACKEND_PORT, bodyLength, body);
    if (length <= 0 || (size_t)length >= sizeof(request)) {
        return false;
    }
    int sent = 0;
    while (sent < length && WaitSocket(fd, true, started)) {
        int count = send(fd, request + sent, (size_t)(length - sent), 0);
        if (count <= 0) {
            return false;
        }
        sent += count;
    }
    return sent == length;
}

static int ReceiveStatus(int fd, uint32_t started)
{
    char reply[128];
    size_t received = 0;
    while (received < sizeof(reply) - 1 && WaitSocket(fd, false, started)) {
        int count = recv(fd, reply + received, sizeof(reply) - 1 - received, 0);
        if (count <= 0) {
            return -1;
        }
        received += (size_t)count;
        reply[received] = '\0';
        const char *end = strstr(reply, "\r\n");
        if (end == NULL) {
            continue;
        }
        if (end - reply < 12 || strncmp(reply, "HTTP/1.", 7) != 0 ||
            (reply[7] != '0' && reply[7] != '1') || reply[8] != ' ' ||
            reply[9] < '1' || reply[9] > '5' ||
            reply[10] < '0' || reply[10] > '9' ||
            reply[11] < '0' || reply[11] > '9' || (end != reply + 12 && reply[12] != ' ')) {
            return -1;
        }
        return (reply[9] - '0') * 100 + (reply[10] - '0') * 10 + (reply[11] - '0');
    }
    return -1;
}

int VoicePostCommand(unsigned int id, const char **error)
{
    uint32_t started = osKernelGetTickCount();
    int fd = socket(AF_INET, SOCK_STREAM, 0);
    if (fd < 0) {
        *error = "socket";
        return -1;
    }
    int status = -1;
    if (!OpenConnection(fd, started)) {
        *error = "connect";
    } else if (!SendRequest(fd, id, started)) {
        *error = "send";
    } else {
        status = ReceiveStatus(fd, started);
        *error = status < 0 ? "response missing, invalid or timed out" : NULL;
    }
    closesocket(fd);
    return status;
}
