# 第二块 BearPi 的离线语音命令上报

烧录目标为手持端 BearPi-HM Nano（Hi3861）。SEN0539（DF2301Q）离线识别口令，手持板约每 100 ms 通过 I²C 读取命令 ID，再通过 Wi-Fi 上报给小车。电脑加入同一个 **BearPi-Car** 热点，轮询小车取出 ID，决定动作或导航任务，并通过现有轮控接口控制小车。

短按手持板的 **F1** 可以通过 I²C 唤醒模块。等 ASR 灯亮起或唤醒提示播完，就能直接说“前进”之类的口令。长按 F1 只发送一次唤醒。

## 接线

断电后将语音模块拨到 **I²C**，按引脚丝印接到第二块板。使用 3.3 V I²C 信号，供电方式和电流余量见[语音遥控交接](../docs/voice-handoff.md)。

| SEN0539 | 第二块 BearPi |
| --- | --- |
| D/T（SDA） | IO00 |
| C/R（SCL） | IO01 |
| GND | GND |
| VCC | V3.3，先核对供电余量 |

## 配置手持板和电脑

从样例创建或更新 `voice_remote/wifi_local.h`。若文件已存在，保留已配置的口令并核对下表中的字段。Git 忽略此文件。旧配置中已有 `CAR_AP_PASSWORD` 时，可以把 `VOICE_WIFI_PASSWORD` 定义为 `CAR_AP_PASSWORD`。

```bash
cp -n voice_remote/wifi_local.example.h voice_remote/wifi_local.h
```

| 配置项 | 填写值 |
| --- | --- |
| `VOICE_WIFI_SSID`、`VOICE_WIFI_PASSWORD` | `BearPi-Car` 和小车当前热点口令 |
| `VOICE_BACKEND_IP` | `192.168.4.1` |
| `VOICE_BACKEND_PORT` | `80` |
| `VOICE_LEFT_ID`、`VOICE_RIGHT_ID`、`VOICE_BLUE_CUP_ID` | 对应自学习口令的实测 ID（5–21），三个非零值须不同；`0` 表示暂未启用 |

电脑和手持板都访问小车的固定 IP。电脑在热点上分配到的地址可以变化。

## HTTP 接口和上报规则

手持板向 `http://192.168.4.1/voice/command` 发送以下请求。车端成功暂存后返回 `204 No Content`。

```http
POST /voice/command HTTP/1.1
Content-Type: application/json

{"command_id":24}
```

- 固定 ID 为前进 `22`、后退 `23`、停车 `24`。已启用的学习 ID 原样上报。未知 ID 仍可在串口观察，便于训练时登记。
- 第一次成功的 I²C 读数用作基线。同一个 ID 连续读到时只上报一次；成功读到 `0` 或其他 ID 后，可识别下一次相同口令。I²C 读取失败保留上一个成功 ID。
- 停车优先，清掉队列里等待上报的移动和导航口令。已发起的 HTTP 请求受原有 180 ms 总期限限制。车端收到 `24` 后停车并释放轮控会话。电脑取到 `24` 后取消当前路线并发出 `stop`。
- 离线读到的事件、掉线时排队的事件、等待超过约 500 ms 的事件都会丢弃。HTTP 请求只尝试一次，失败日志带命令 ID 和失败阶段或 HTTP 状态码。

电脑每约 100 ms 请求 `GET /tasks/next`，请求头为 `X-Car-Control: 1`。有任务时返回 `200` 和同格式的 JSON，并立即取走任务，空时返回 `204`。车端只暂存一条约 1 秒内的待取指令。新移动指令会替换尚未取走的移动指令。待取停车指令优先，期间新移动指令会收到 `409`。这条链路使用单个轮询进程。若取任务的 HTTP 响应丢失，需要重新说口令。

## 在 Ubuntu 中编译

使用编译车端 v12 时用的完整 BearPi-HM Nano SDK。下面假设已进入 `BearPi-Car` 仓库根目录。把 `sdk` 改成 Ubuntu 中实际含有 `build.py` 的 SDK 根目录。命令使用 SDK 默认的 `lite_component("app")`，在子 shell 内临时选择语音示例，退出时恢复原有的 `sample/BUILD.gn`。

```bash
(
  set -e
  sdk="$HOME/project/bearpi-hm_nano"
  sample="$sdk/applications/BearPi/BearPi-HM_Nano/sample"
  artifact=Hi3861_wifiiot_app_allinone.bin
  export PATH="$HOME/gcc_riscv32/bin:$HOME/gn:$HOME/ninja:$PATH"

  test -f "$sdk/build.py"
  test -f "$sample/BUILD.gn"
  test -f voice_remote/wifi_local.h
  mkdir -p "$sample/my_tasks/voice_remote"
  cp voice_remote/*.c voice_remote/*.h voice_remote/BUILD.gn "$sample/my_tasks/voice_remote/"
  backup=$(mktemp "$sample/BUILD.gn.voice.XXXXXX")
  cp -p "$sample/BUILD.gn" "$backup"
  trap 'mv -f -- "$backup" "$sample/BUILD.gn"' EXIT
  trap 'exit 130' INT
  trap 'exit 143' TERM
  cat > "$sample/BUILD.gn" <<'GN'
import("//build/lite/config/component/lite_component.gni")

lite_component("app") {
  features = [ "my_tasks/voice_remote:voice_id_reader" ]
}
GN

  cd "$sdk"
  python3 build.py BearPi-HM_Nano
  test -s "out/BearPi-HM_Nano/$artifact"
  mkdir -p out/voice_remote/voice-events
  cp "out/BearPi-HM_Nano/$artifact" "out/voice_remote/voice-events/$artifact"
  printf '\nVoice firmware: %s/out/voice_remote/voice-events/%s\n' "$sdk" "$artifact"
)
```

编译成功后，从 SDK 的 `out/voice_remote/voice-events/` 取固件烧录。同一个 SDK 的 `sample/BUILD.gn` 是共享的，请等当前构建结束再启动其他任务。

## 烧录和联调

1. 按[车端编译说明](../README.md#从源码编译)生成小车固件。两块板的 `Hi3861_wifiiot_app_allinone.bin` 同名，复制到 Windows 时保留各自的目录或分别重命名为 `car-mailbox.bin` 和 `handset.bin`。
2. 先断开电机电池，再把车端固件烧进小车板，把手持端固件烧进第二块板。每次先核对 COM 口、关闭占用该口的串口工具。在 HiBurn 选择 **Auto burn → Connect** 后短按对应板的 **RESET**，下载波特率可沿用 921600。等写入结束后断开 HiBurn，以 **115200、8N1** 查看日志并复位对应板。车端应出现 `[CAR] VOICE MAILBOX READY`。
3. 电脑加入 `BearPi-Car`。在仓库根目录用 Python 3.7 或更新版本运行 `python tools/voice_gateway.py --poll-car`。这是预演模式。手持板应打印 `[REMOTE] Connected to BearPi-Car; DHCP ready` 和 `http://192.168.4.1:80/voice/command`。轮询若一直返回 `404`，核对小车板是否烧进了带任务接口的固件。
4. 短按 F1，等 ASR 灯亮或提示播完，说已登记的前进和停车口令。手持板应打印例如 `[VOICE] ID=22` 和 `[REMOTE] ID=22 reported HTTP=204`，电脑应打印 `[car poll] ID=22: accepted (202)` 以及前进预演。停车应看到 `ID=24`。待串口出现 `ID=0` 后，可以再说同一条口令，确认电脑收到新事件。
5. 在模块上训练“去蓝杯”，说一次口令并查看 `[VOICE] ID=<数字>`。把实测值写进手持端 `VOICE_BLUE_CUP_ID`，重新编译、烧录手持板。电脑运行 `python tools/voice_gateway.py --poll-car --plan 路线文件.json --blue-cup-id 实测数字`，看到同一个 ID 和转向、前进预演。
6. 确认预演后，先架空车轮，再用 `python tools/voice_gateway.py --poll-car --live` 联调基本前进、后退和停车。蓝杯路线实跑时，另加 `--plan`、`--blue-cup-id` 和对应路线动作需要的实测 `--forward-cm-per-sec`、`--turn-deg-per-sec`。用现有[标定脚本](../tools/run_car_route.py)测量，保持标定和运行时的 `--wheel-level` 一致。

手持端扫描后使用匹配热点的 BSSID 和频率连接。`not found` 表示此次扫描未找到配置的热点。出现 `found` 后仍超时，可查看信号强度和 `disconnect reason`。原因码为 0 表示这轮没有收到非零原因码。
