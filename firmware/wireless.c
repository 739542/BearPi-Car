#include "wifi_local.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <strings.h>
#include "cmsis_os2.h"
#include "wifi_hotspot.h"
#include "lwip/netifapi.h"
#include "lwip/sockets.h"
#include "web_assets.h"

extern int CarSetWheels(int left, int right, int fast);
extern void CarWifiStop(void);
static int fastMode;
static unsigned owner, serialNo, lastSeq, refreshed;

static int write_all(int fd, const void *data, unsigned len)
{
    const char *p = data;
    while (len) {
        int n = send(fd,p,len,0);
        if (n <= 0) return -1;
        p += n; len -= (unsigned)n;
    }
    return 0;
}

static void reply(int fd,int ok,const char *type,const void *body,unsigned size)
{
    char head[224];
    int n=snprintf(head,sizeof(head),"HTTP/1.1 %s\r\nContent-Type: %s\r\nContent-Length: %u\r\nCache-Control: no-store\r\nConnection: close\r\n\r\n",
        ok?"200 OK":"409 Conflict",type,size);
    if(n>0 && (unsigned)n<sizeof(head) && !write_all(fd,head,(unsigned)n)) write_all(fd,body,size);
}

static void control(int fd,const char *cmd)
{
    unsigned now=osKernelGetTickCount(), token, seq;
    int l,r,used=0,ok=1;
    char result[96]="{}";
    if(owner && (unsigned)(now-refreshed)>=osKernelGetTickFreq()*6/10) {
        owner=0; CarWifiStop();
    }
    if(!strcmp(cmd,"status")) {
        snprintf(result,sizeof(result),"{\"version\":\"v11\",\"busy\":%s}",owner?"true":"false");
    } else if(!strcmp(cmd,"stop")) {
        owner=0; CarWifiStop();
    } else if((!strcmp(cmd,"arm") || !strcmp(cmd,"arm-fast")) && !owner) {
        if(!++serialNo) ++serialNo;
        fastMode=!strcmp(cmd,"arm-fast"); owner=serialNo; lastSeq=0; refreshed=now;
        snprintf(result,sizeof(result),"{\"session\":\"%u\"}",owner);
    } else if(sscanf(cmd,"drive %9u %9u %3d %3d%n",&token,&seq,&l,&r,&used)==4 &&
              used && !cmd[used] && owner && token==owner && seq>lastSeq &&
              l>=-30 && l<=30 && r>=-30 && r<=30 && !CarSetWheels(l,r,fastMode)) {
        lastSeq=seq; refreshed=now;
    } else {
        ok=0; strcpy(result,"{\"error\":\"Release controls and retry\"}");
    }
    reply(fd,ok,"application/json",result,strlen(result));
}

static void handle(int fd)
{
    char buf[2304]; int n=0;
    char *end=NULL;
    while(n<(int)sizeof(buf)-1) {
        int k=recv(fd,buf+n,sizeof(buf)-1-n,0);
        if(k<=0)return;
        n+=k; buf[n]=0;
        if((end=strstr(buf,"\r\n\r\n"))!=NULL)break;
    }
    if(!end)return;
    if(!strncmp(buf,"GET / HTTP/",11))reply(fd,1,"text/html; charset=utf-8",page,sizeof(page));
    else if(!strncmp(buf,"GET /style.css HTTP/",20))reply(fd,1,"text/css",style,sizeof(style));
    else if(!strncmp(buf,"GET /app.js HTTP/",17))reply(fd,1,"application/javascript",script,sizeof(script));
    else if(!strncmp(buf,"POST /control HTTP/",19)) {
        int length=-1,allowed=0;
        char *body=end+4;
        for(char *line=strstr(buf,"\r\n")+2;line<end;) {
            char *next=strstr(line,"\r\n"); if(!next)return;
            *next=0;
            if(!strncasecmp(line,"Content-Length:",15))length=atoi(line+15);
            if(!strcasecmp(line,"X-Car-Control: 1"))allowed=1;
            line=next+2;
        }
        if(!allowed || length<1 || length>95 || body-buf+length>=(int)sizeof(buf))return;
        while(n<body-buf+length){
            int k=recv(fd,buf+n,body-buf+length-n,0);if(k<=0)return;n+=k;
        }
        body[length]=0;control(fd,body);
    } else {
        const char missing[]="HTTP/1.1 404 Not Found\r\nContent-Length: 0\r\nConnection: close\r\n\r\n";
        write_all(fd,missing,sizeof(missing)-1);
    }
}

static void WirelessTask(void *arg)
{
    (void)arg;
    osDelay(2*osKernelGetTickFreq());
    HotspotConfig cfg={.ssid="BearPi-Car",.preSharedKey=CAR_AP_PASSWORD,.securityType=WIFI_SEC_TYPE_PSK,
        .band=HOTSPOT_BAND_TYPE_2G,.channelNum=6};
    if(SetHotspotConfig(&cfg)!=WIFI_SUCCESS || EnableHotspot()!=WIFI_SUCCESS)goto fail;
    struct netif *net=NULL;
    for(int i=0;i<30 && !net;i++){net=netifapi_netif_find("ap0");if(!net)osDelay(osKernelGetTickFreq()/10);}
    ip4_addr_t ip,mask; IP4_ADDR(&ip,192,168,4,1);IP4_ADDR(&mask,255,255,255,0);
    if(!net || netifapi_netif_set_addr(net,&ip,&mask,&ip)!=ERR_OK || netifapi_dhcps_start(net,NULL,0)!=ERR_OK)goto fail;
    int server=socket(AF_INET,SOCK_STREAM,0);
    struct sockaddr_in addr={0};addr.sin_family=AF_INET;addr.sin_port=htons(80);addr.sin_addr.s_addr=ip.addr;
    if(server<0)goto fail;
    if(bind(server,(struct sockaddr*)&addr,sizeof(addr)) || listen(server,5)){closesocket(server);goto fail;}
    printf("[CAR] AP READY BearPi-Car http://192.168.4.1/\r\n");
    for(;;){
        int fd=accept(server,NULL,NULL);
        if(fd<0){osDelay(1);continue;}
        struct timeval timeout={0,350000};
        setsockopt(fd,SOL_SOCKET,SO_RCVTIMEO,&timeout,sizeof(timeout));
        setsockopt(fd,SOL_SOCKET,SO_SNDTIMEO,&timeout,sizeof(timeout));
        handle(fd);closesocket(fd);
    }
fail:
    printf("[CAR] AP setup failed\r\n");
}

void CarWifiStart(void)
{
    osThreadAttr_t attr={0};attr.name="CarWifi";attr.stack_size=10240;attr.priority=osPriorityNormal;
    if(!osThreadNew(WirelessTask,NULL,&attr))printf("[CAR] AP thread failed\r\n");
}
