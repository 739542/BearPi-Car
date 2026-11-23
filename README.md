# BearPi-Car

在手机上连接小车热点，左手控制油门，右手控制转向。BearPi-HM Nano 在车上提供网页、接收指令，并通过双路电机驱动板控制左右轮。电脑也可以通过 USB 调试和驾驶。

![手机驾驶台实际网页录屏，展示普通与急速两种模式](docs/media/cockpit.gif)

## 从这里开始

| 你要做的事 | 入口 |
| --- | --- |
| 组装底盘、接驱动板、核对供电 | [实验器材与接线](docs/hardware.md) |
| 了解项目分期、驾驶方式和验收情况 | [需求与设计](docs/design.md) |
| 接手离线语音控制 | [语音遥控交接](docs/voice-handoff.md) |
| 调整车轮输出、超时停车或无线控制协议 | [电机控制](firmware/motor_test.c) · [热点与 HTTP 服务](firmware/wireless.c) |
| 修改手机驾驶台 | [网页源码](web/) · [板端通信适配](web/board_transport.js) |
| 使用电脑控制台或查看早期手机中转实现 | [桌面端](desktop/) · [早期 USB 网桥](legacy/bridge.py) |
| 核对实际烧录版本和启动记录 | [v12 验证材料](verification/) |

## 电脑端导航台

在仓库根目录用 Python 3.7 或更新版本启动，随后打开 [BearPi 导航台](http://127.0.0.1:8766/)：

```powershell
python tools/voice_gateway.py
```

默认加载 `dashboard/demo` 中的小车与蓝杯语义点云，预演可离线查看。选择「蓝杯」展开路线并高亮目标，场景支持拖动、缩放和俯视。路线和车头箭头基于拍摄时的位置。

语音联调时，电脑和手持板加入小车热点。把 `$blueCupId` 设为手持端实测并启用的「去蓝杯」ID，再用下列命令启动一个轮询进程：

```powershell
python tools/voice_gateway.py --poll-car --scene dashboard/demo/scene.json --blue-cup-id $blueCupId
```

网页上的「停车」在预演时取消任务，在 `--live` 模式下向小车发停车指令。实车导航沿用现有速度标定参数。换场景时，用 `tools/plan_car_route.py --scene-name` 生成与语义 ASCII PLY 同目录的 JSON，再通过 `--scene` 指定。

### 从照片重建场景

用装有 PyCOLMAP 3.11.1 的 Python 启动导航台。在本机页面点「选择照片文件夹」，选入 2–40 张有重叠视角的 JPG 或 PNG，再点「重建场景」。默认优先使用带 CUDA 的 PyCOLMAP 或 PATH 中的 CUDA 版 COLMAP 做 SIFT 特征提取和匹配，随后由 PyCOLMAP 恢复相机位姿和稀疏点。可以用 `--reconstruction-device cuda` 要求 GPU，或用 `--reconstruction-device cpu` 指定 CPU。

完成后网页会显示原色点云并提供 PLY 下载。原始上传、文件名映射、COLMAP 稀疏模型和日志保存在 `local_reconstructions/<任务 ID>/`，可用 `--reconstruction-dir` 自定保存路径。新场景需要语义分割和尺度标定后才能生成导航路线。通过「返回导航场景」查看后端原先配置的场景。

#### 生成实景 GS

从 [Brush v0.3.0](https://github.com/ArthurBrussee/brush/releases/tag/v0.3.0) 解压 Windows 发布包，用 `--brush` 指定其中的 `brush_app.exe`：

```powershell
& $python tools/voice_gateway.py --brush $brush
```

这里 `$python` 是已装 PyCOLMAP 的 Python 路径，`$brush` 是 Brush 可执行文件的完整路径。页面出现「生成实景 GS」选项后，保持勾选并点击「重建场景」。Brush 使用这次重建的相机位姿、稀疏点和已注册照片训练，默认执行 2500 步，可用 `--gs-steps` 调整。训练完成后页面打开「实景 GS」视图，可旋转、缩放、下载 `gaussians.ply`，也可切回点云。GS 训练失败时，页面仍提供稀疏点云，任务目录的 `brush.log` 记录训练日志。

浏览器查看器使用本地分发的 [SuperSplat Viewer 1.43.3](https://github.com/playcanvas/supersplat-viewer)，由导航台同源提供。

#### 在实景 GS 中选蓝杯

使用 `tools/align_navigation_gs.py`，通过 `--scene` 指定语义场景 JSON，通过 `--reference-model` 和 `--reference-images` 指定生成该路线时的 COLMAP 模型和原图，通过 `--gs-job` 指定用同一批照片生成的 Brush 任务目录。脚本将蓝杯语义点、小车起点、车头和停车点转换到 GS 坐标，并在任务目录写入 `navigation_gs.json`。

加 `--mask-root` 指定 `segment_tabletop.py` 生成的多视角分割目录，可用至少三张已注册照片的蓝杯掩码恢复三维轮廓。网页用该轮廓为杯子上色并设置点击区域。已有清单时，用 `--output` 指定同一任务目录中的新文件名。

启动后端时加 `--navigation-gs` 并指定该 JSON。进入实景后点击蓝杯区域，画面会显示计划路线和停车点。默认使用「预演出发」。实车联调时在同一启动命令加 `--poll-car --live` 和实测的 `--forward-cm-per-sec`、`--turn-deg-per-sec`。网页确认小车摆回原起点、车头对准后可以点「出发」。如需语音触发同一条路线，再加 `--blue-cup-id`，填写手持端已启用的实测 ID。右上角可停车。

车板通过 USB 控车时，用 `--live --car-serial-port COM4` 并填写当前实际端口及同档实测速度。网页预览后勾选摆放确认，再点击「出发」即可执行一次。若目标是轻质空杯，可在规划时用 `--push-cm` 指定接触后推进的距离，并为新路线生成对应的 GS 对齐清单。

#### 搜索场景里的物体

目标搜索会查找当前场景预先索引的目标及别名。输入「蓝杯」「蓝色杯子」「小摆件」「玩偶」或对应的英文名称，网页会定位目标并显示路线。实车模式要勾选摆放确认并点击「出发」。每次切换目标都会清除上次确认。

为当前桌面生成双目标索引时，在对齐命令加 `--figurine-mask-root` 指定摆件掩码目录，加 `--figurine-views 1.jpg 2.jpg` 选用可见且检测正确的视图。完成后将新清单交给 `--navigation-gs`。小摆件需要向右转，实车出发需用 `--right-turn-deg-per-sec` 指定同档实测的右转速度。蓝杯语音入口仍使用 `--blue-cup-id` 配置的口令。

「场景目标」按钮由场景清单生成，点击即可定位。用 `--objects dashboard/demo/desktop_objects.json` 可以把当前桌面的耳机、插排和笔记本电脑也加入。带「可定位」标记的物品提供 GS 标注；确认可达停车点并生成路线后才可提交导航。

连续导航从上一段的预计停车位置和车头方向重新计算下一段路线。首次出发从拍摄时的起点开始。上一段结束后选择下一目标，核对更新后的路线和实物位置再出发。中途停止、执行出错或实车手动移动后，请把小车摆回原位并点击「重置起点」。实车的后续各段可能需要另一转向的速度标定。连续路线采用转向后直行，小车没有实时定位或自动绕障。

## 使用已烧录的小车

1. 先断开电机电池，用 USB 给主板供电。手机加入 **BearPi-Car**，向组员获取当前小车的热点密码。
2. 在浏览器打开 **http://192.168.4.1/**。横屏进入驾驶页，保持电机电池断开可以体验界面。
3. 首次带电测试先架空车轮，接通电机电池，在普通模式轻推油门，确认左右轮方向与松手停车。落地后再试弧线和表演。无线行驶时主板仍需要稳定的 USB 供电。
4. 电脑控制台使用带 Tk 的 Windows Python 3、USB 数据线与 pySerial。在仓库根目录运行以下命令，选择实际端口后连接。烧录前释放串口。

~~~powershell
python -m pip install -r requirements.txt
python desktop/control_panel.py
~~~

## 从源码编译

使用能编译 BearPi-HM Nano 的 Ubuntu OpenHarmony SDK 环境。项目复用 SDK 的 RISC-V GCC、GN 和 Ninja；SDK 工程目录作为参数传入。将完整仓库放入虚拟机，在仓库根目录执行：

~~~bash
cp firmware/wifi_local.example.h firmware/wifi_local.h
# 编辑 wifi_local.h，将示例值替换成你们自己的 8 至 63 位 ASCII 热点口令
python3 tools/embed_web.py
python3 firmware/build_car.py /实际路径/bearpi-hm_nano
~~~

每次修改 web 目录后重新执行后两步。嵌入脚本为板端替换通信实现。构建脚本在 SDK 中暂时选择小车示例，退出时恢复原来的 sample/BUILD.gn。输出位于 SDK 的 out/car_stage1，包括 allinone.bin、manifest.json 和 build.log。

用 HiBurn 选择生成的 Hi3861_wifiiot_app_allinone.bin 和开发板串口，进入 Auto burn 等待后短按 RESET。两个分区写入成功后关闭 HiBurn，打开 115200 波特率串口，再短按 RESET。上电停车后查询 AT+CAR=?，新版应返回 car-stage1-v12，随后可按热点地址访问。板上原固件含在用热点口令，仓库提供可重新构建的源码。使用不同口令构建后，固件哈希会随之改变。

## 离线回归与版本证据

在根目录运行 `python desktop/test_controls.py` 可检查键盘、失焦停车和路线状态。手机测试使用 Node.js、Playwright 与 Chromium，运行 `npm install --no-save --package-lock=false playwright`、`npx playwright install chromium`，再运行 `node verification/mobile.cjs`。使用已安装的 Edge 时，可将 BROWSER_EXECUTABLE 环境变量指向其可执行文件。测试拦截网页的网络请求，可在不接小车时运行。

[原机启动摘录](verification/boot-v12.txt)记录固件版本、停车状态和热点。构建清单中的 hardware_test 是打包当时的标记，启动摘录来自随后实际烧录和复位。早期 USB 网桥只接受 v6 版本回报；当前实车请使用热点直连，或使用桌面串口控制台。
