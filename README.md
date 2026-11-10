# RemoteMic 9451

把 **18D1:9451 的 Google TV 外形蓝牙语音遥控器**用作 Windows 语音输入设备和快捷键遥控器。

Windows voice input and device-scoped key mapping for a tested Google TV-style **18D1:9451** Bluetooth remote. [English guide](docs/README.en.md).

这是从真实设备适配整理出来的 **GPL-3.0-only 源码首版**，不是所有安卓电视遥控器的通用驱动。外形、蓝牙名称相同不代表协议一致；目前真机验证仅覆盖 ZTKA-IR57 / VID `18D1` / PID `9451` / REV `0105` 的一个样本。**没有完成 macOS 移植，也没有小米 RC003 真机适配。**

## 已解决的具体问题

- 遥控器麦克风音频经蓝牙接收、IMA ADPCM 解码、VB-CABLE 送入豆包输入法。
- 按住语音键说话，松开结束；可连续输入多句。
- 针对该仿款约15秒强制结束的问题，每10秒接续流，内部切段不释放右 Alt；真实松键才结束。已实测一次持续约31秒，保护上限120秒尚未完整实测。
- 损坏/不完整蓝牙分片恢复，避免切段后的半帧持续污染解码。
- 仅接受所选遥控器的方向/确定键，转换为光标键和 Enter。
- 可选额外按键映射：前台切换、再次按下最小化、窗口总览等。
- 登录自启和有重试限额的后台看护；已实测按键模块退出后约6秒恢复。

这些是一个样本上的实测结果，不等于所有9451设备或Windows版本均兼容。新增的公开版首次配置流程已做隔离环境测试；尚未获得第二台电脑的完整实物安装验收。

## 工作方式

```text
遥控器麦克风 → ATVV BLE → 134字节帧/ADPCM解码 → 48kHz PCM
                                                    ↓
当前文本框 ← 豆包输入法 ← CABLE Output ← VB-CABLE ← CABLE Input
                 ↑
       语音键控制右 Alt 按下/松开
```

**语音键既负责开关遥控器收音，也触发输入法快捷键。它不是只把一个按钮映射成 Alt。** 电脑桌面麦克风无需参与这条链路。

## 环境与版本限制

| 项目 | 当前范围 |
|---|---|
| 操作系统 | Windows x64；实测Windows 11，其他版本未完整验收 |
| Python | 64位Python 3.13已验证；依赖锁在 `requirements.txt` |
| 遥控器 | 上述9451样本，已在Windows中配对 |
| 输入法 | 豆包输入法1.0.0.23已验证；适配器按文件哈希和内存签名校验，不支持的构建会拒绝使用旧偏移 |
| 音频 | 单独安装官方VB-CABLE，WASAPI输出到CABLE Input |
| 额外键 | 实验性、默认关闭；只接受下述固定HID驱动构建 |

豆包语音快捷键适配使用 Frida 附加输入法服务，可能出现 Frida Helper/UAC 提示。额外键功能还会附加目标遥控器的Windows HID宿主。程序不关闭UAC，不修改系统驱动文件，不安装内核过滤驱动；不支持时应停用该功能，不能删除哈希检查强行运行。

## 安装和首次配置

1. 从本仓库下载源码或克隆，放在日后不会移动的目录。安装[Python](https://www.python.org/downloads/windows/)、[豆包输入法](https://shurufa.doubao.com/pc)和[VB-CABLE](https://vb-audio.com/Cable/)。这些第三方程序不包含在本仓库中，按各自官方许可使用。
2. 在Windows蓝牙设置中配对遥控器。不要为了运行本项目重复删除配对或恢复出厂设置。
3. 在仓库目录的PowerShell运行：

   ```powershell
   .\setup.ps1
   ```

   它建立 `.venv`、安装固定依赖，并从**已经配对**的9451 HID服务中选择设备。多个候选时运行：

   ```powershell
   .\.venv\Scripts\python.exe configure.py --list
   .\.venv\Scripts\python.exe configure.py --device 1
   ```

   设备地址和容器标识只保存在被Git忽略的 `config.local.json`；不要上传该文件。`config.example.json`展示可用设置。若脚本执行受本机策略限制，可以手动按 `setup.ps1` 中的Python命令执行，无需修改系统策略。
4. 豆包输入法麦克风选 **CABLE Output (VB-Audio Virtual Cable)**，长按语音快捷键设为**右Alt**。本程序向对应的 **CABLE Input** 输出。Windows/游戏默认录音设备可以继续使用实体麦克风；本程序不会自动把它改成虚拟设备。
5. 若目标应用也把Alt绑定到自己的录音/听写，先取消那一层重复绑定。实测WorkBuddy的Alt录音与豆包同时触发，会使带下划线的临时文字在结束时消失或变短。
6. 先执行只读体检，再双击 `start.cmd`：

   ```powershell
   .\.venv\Scripts\python.exe doctor.py
   ```

   自己点击一个文本框，按普通键唤醒遥控器，再按住语音键说话，松开后等待输入法提交文字。不要同时运行本项目的多个不同副本或其它占用同一遥控器语音通道的软件。

## 可选：额外按键

先核对 `doctor.py` 的驱动构建检查。启用：

```powershell
.\.venv\Scripts\python.exe configure.py --enable-extra-keys
```

停止并重新启动产品后生效。若不带 `--enable-extra-keys` 重新配置，则关闭额外键功能。

| 按钮 | 动作 |
|---|---|
| 上下左右、确定 | 光标方向、Enter |
| 返回 | Esc |
| 主页 | Win+D显示/恢复桌面 |
| 静音 | 保留系统原生输出静音，不再叠加一次切换 |
| YouTube | 打开/置前WorkBuddy；已经在前台则最小化 |
| Netflix | 打开/置前Codex桌面App；已经在前台则最小化 |
| 信号源 | Win+Tab窗口总览；方向/确定选择 |
| 电源 | 按键模块工作时拦截，不执行动作 |
| 音量± | 该样本实按未收到报告，不虚构映射 |

应用安装路径可在 `config.local.json` 的 `applications` 中配置。默认Codex检测的是桌面App，不是 `codex.exe` 命令行。

额外键通过**目标设备源头报告处理**避免双触发。仅RawInput追加动作无法消除原生行为：例如这只设备的“主页”本来是浏览器主页，“信号源”本来发出启动电子表格的Consumer usage。

当前支持的系统文件：`Microsoft.Bluetooth.Profiles.HidOverGatt.dll`，SHA256：

```text
41d2998c7f38e104b0a469404cfb05ddbfe7c0a912b9020f446f005f3f4156d4
```

Windows更新改变此文件后，额外键会拒绝使用旧偏移。只确认版本号相同仍不够。驱动未知时保持该功能关闭；不修改系统文件。两秒续约失效或模块停止后恢复原生行为，所以“电源不执行动作”并不是永久的全局电源设置。

## 自启、停止与移除

```powershell
.\login-startup.ps1          # 当前用户登录后启动
.\login-startup.ps1 -Remove  # 取消登录自启
```

`stop.cmd` 会一起停止接收器、按键模块和看护，主动停止后不会马上被看护重启。仅停止实验按键模块：

```powershell
.\.venv\Scripts\pythonw.exe -B key_mapping.pyw --stop
```

这会记录主动禁用标记，看护会尊重它。去掉 `--stop` 手动启动，或重新启动产品，可以恢复已配置启用的按键模块。看护只恢复已稳定运行后退出的组件；启动就失败时不循环附加，每个组件每小时最多自动恢复两次。看护自身也被终止、权限被拒绝或设备不兼容时，仍需要人工排错。

移除时先取消自启、运行停止入口，再删除这个源码目录和 `%LOCALAPPDATA%\RemoteMic9451`。这不会自动卸载豆包或VB-CABLE，也不要删除其它项目共用的Python。

## 测试、协议和排错

```powershell
$env:PYTHONPATH=(Join-Path $PWD 'src')
.\.venv\Scripts\python.exe -B -m unittest discover -s tests -v
node tests/key-policy.cjs src/rm9451/key_mapping.js
```

普通测试不会主动录音、附加Frida或发送按键。两项硬件/桌面集成检查默认跳过；设置 `RM9451_HARDWARE_TESTS=1` 才会执行。CI通过不能代替真机测试。

- [协议与架构](docs/architecture.md)：134字节帧、握手、15秒接续、报告格式。
- [排错与已知限制](docs/troubleshooting.md)。
- [贡献和隐私](CONTRIBUTING.md)。
- 日志位于 `%LOCALAPPDATA%\RemoteMic9451\evidence`。不保存录音或识别正文；诊断可能包含本机路径/设备信息，分享前检查并脱敏。

## 来源与许可

`rm9451` 是本项目的9451适配和公开配置封装。豆包精确构建适配、Windows设备归属等能力复用了 [ZSTDJan/windows-remote-mic-app](https://github.com/ZSTDJan/windows-remote-mic-app) 的 `ovb_rc003`，固定提交 `07fae8034f0210cca90f8b913491044555bd9f6d`；它的Windows实现又基于 [nijez/open-voice-bridge](https://github.com/nijez/open-voice-bridge)。详见 [NOTICE](vendor/NOTICE.md) 和附带的上游归属文件。

本项目按 **GPL-3.0-only** 提供源码，见 [LICENSE](LICENSE)。保留上游版权，不包含豆包、Windows驱动、VB-CABLE、Frida或Python的第三方二进制，也不授予其商标或图标权利。本仓库未使用上游应用图标。软件不提供担保。
