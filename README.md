# Biko3 iOS 移植

目标：将现有 Switch NRO 和游戏资源移植到未越狱 iPhone，保留原有玩法。

**原生 iOS 宿主正在开发和云端验证中，尚未验证游戏可玩。** 已接入真实客体代码、libnx 启动、原生文件系统、音频、触控/手柄及 MoltenVK 图形与计算路径；构建成功也不代表设备验证完成。

## 云端构建与安装

公共仓库为 hyc1743/biko3-ios，Actions 的 Build Biko3 development IPA 工作流使用 macOS 26/Xcode，打包真实嵌入的客体程序，不生成替代游戏的空壳。成功后下载 Biko3-development-IPA artifact 中的 IPA 和 build-report.json。未签名的开发 IPA 需要通过自己的侧载签名工具安装。

游戏 Data 资源不在仓库或 IPA 中。安装后从“文件”App 把原始 Data 文件夹放到 Biko3 的 game 目录，完整路径为 Documents/game/Data，然后重启应用。存档位于 Documents/saves，日志位于 Documents/log.txt。当前设备启动、第一帧、完整玩法及存档兼容性仍待验证。

云端原始 NRO 输入使用 AES-GCM 容器，只有 Actions Secret BIKO3_GUEST_KEY 可以解密。密钥、NSP 和游戏 Data 不提交。构建产物会包含移植后的客体程序。

## 已完成

- Ghidra 12.1.4、Temurin JDK 21 已安装到项目 .tools/，安装包 SHA-256 校验通过。
- 三个参考仓库按 references.lock.json 固定版本取得，含源码、构建元数据和许可证；3,177 个文件经过 Git blob 与 SHA-256 校验。
- 从原始 DWARF 异常帧恢复 13,639 个函数范围，解决 NRO 缺少 eh_frame_hdr 索引的问题。
- 对固定 nxnative 源码副本作两处有记录的兼容修改，完整外部改写器和嵌入生成器均实际执行成功；原始 NRO、资源与 RELR addend 保持不变。
- Ghidra 成功导出启动链、Vulkan 入口及渲染器等 10 个目标函数的伪代码。全量分析曾内存不足，成功结果来自有范围限制的聚焦分析。
- 提取 6 个由实际创建参数确认精确长度的 SPIR-V：2 个顶点、2 个片元、2 个计算着色器。
- 原生宿主与完整接管清单已集成。首轮云端 IPA 已成功构建并逐字节验证客体映像；可移植回归测试通过，当前结果和进一步的原生执行检查见 Actions。

## 复现

Python 工具使用标准库；指令改写与着色器分析另需当前环境已有的 capstone、lz4。

```powershell
python tools/bootstrap_analysis.py --task all
python -m unittest discover -s tests -v
```

下面的输出目录必须尚不存在。重复运行时更换 run-01：

```powershell
python tools/prepare_nxnative.py switch/biko3/biko3-runtime.nro --out work/biko3-ios/run-01/prepared
python tools/rewrite_nro.py switch/biko3/biko3-runtime.nro --prepared work/biko3-ios/run-01/prepared --nxnative-root references/seedds--nxnative --out work/biko3-ios/run-01/rewritten
python tools/extract_embedded_spirv.py switch/biko3/biko3-runtime.nro --out work/biko3-ios/run-01/shaders
powershell -NoProfile -File tools/run_ghidra_focus.ps1 -Boundaries work/biko3-ios/run-01/prepared/main.function-boundaries.json -Output work/biko3-ios/run-01/ghidra
```

rewrite_nro.py 会执行已审查且哈希固定的完整外部 Python 改写器，保存修改差异、stdout/stderr、版本及产物摘要，不执行游戏。生成汇编使用本机构建路径，转到 macOS 后必须重新生成。

## 验证与剩余工作

1. **libnx 启动与平台接口。** 宿主负责单次重定位，保留原 newlib/TLS/构造函数，文件访问通过 devoptab 接入。需要用实际启动日志验证这些接口和原始工作线程。
2. **完整图形路径。** 88 个 Vulkan 转接点使用真实 MoltenVK，包含两个计算着色器。NN 窗口表面、NVK 专属限制和生命周期已加入适配，能力、画面和同步结果仍需 Apple 平台实测。
3. **真机流程。** 验收依次覆盖客体启动、第一帧、操作、完整玩法、声音、存档、后台恢复和持续运行。未满足这些条件前不宣称可玩。

公开进度见 [当前状态](docs/port-status.md)、[图形桥接](docs/graphics-bridge.md)、[libnx 启动](docs/libnx-startup.md)、[离线准备说明](docs/nxnative-preparation.md)。
