# 尾行 3 · Biko3 iOS

在 iPhone 上游玩《尾行 3》，支持横屏、屏幕虚拟手柄和实体手柄。

## 准备

- 安装 iOS 26 或更新版本的 iPhone。
- 从 [Releases](https://github.com/hyc1743/biko3-ios/releases) 下载新版 `Biko3-development.ipa`，使用自己的签名与侧载工具安装。IPA 未签名，项目不提供签名证书。
- 从 [dev.3 下载页](https://github.com/hyc1743/biko3-ios/releases/tag/v0.1.0-dev.3) 下载下面列出的六个游戏资源分卷。资源单独提供，IPA 本身不包含 Data。
- 手机需要容纳约 3 GB 的分卷和约 3 GB 的展开数据，首次导入时请预留足够空间。

## 安装游戏资源

首次安装下载六个资源分卷即可，无需下载源码、安装 Python 或自行打包。已导入过资源的玩家更新 IPA 后可以继续使用原有数据，无需重复下载。

### 1. 下载全部六个分卷

在 [dev.3 的 Assets 列表](https://github.com/hyc1743/biko3-ios/releases/tag/v0.1.0-dev.3) 中下载以下文件，也可以点击表内链接：

| 文件 | 大小 |
| --- | ---: |
| [Biko3Data.part001.b3data](https://github.com/hyc1743/biko3-ios/releases/download/v0.1.0-dev.3/Biko3Data.part001.b3data) | 512 MiB |
| [Biko3Data.part002.b3data](https://github.com/hyc1743/biko3-ios/releases/download/v0.1.0-dev.3/Biko3Data.part002.b3data) | 512 MiB |
| [Biko3Data.part003.b3data](https://github.com/hyc1743/biko3-ios/releases/download/v0.1.0-dev.3/Biko3Data.part003.b3data) | 512 MiB |
| [Biko3Data.part004.b3data](https://github.com/hyc1743/biko3-ios/releases/download/v0.1.0-dev.3/Biko3Data.part004.b3data) | 512 MiB |
| [Biko3Data.part005.b3data](https://github.com/hyc1743/biko3-ios/releases/download/v0.1.0-dev.3/Biko3Data.part005.b3data) | 512 MiB |
| [Biko3Data.part006.b3data](https://github.com/hyc1743/biko3-ios/releases/download/v0.1.0-dev.3/Biko3Data.part006.b3data) | 约 284 MiB |

六卷合计约 2.98 GB，**必须全部下载，保持原文件名**。应用内置匹配的校验清单，复制完成后会自动检查完整性。不要把 GitHub 自动生成的 `Source code` 压缩包当作游戏资源。

### 2. 复制到 iPhone

首次打开 Biko3，让应用创建文件目录。然后通过“文件”App，或电脑上的 Apple Devices / iTunes 文件共享，将 **六个 `.b3data` 文件**复制到：

```text
文件 → 我的 iPhone → Biko3
```

直接放在 Biko3 根目录即可，也可以放到其中的 `ResourcePacks` 文件夹。保持原文件名，无需复制 JSON 清单，也不要手动解压分卷。

### 3. 自动导入

返回 Biko3，应用会自动校验并展开资源，界面显示当前进度。请保持应用在前台。

- 提示缺卷或校验失败：补齐或重新复制提示中的文件，再点“重新检查 / 继续解包”。
- 中断后重开：已完成且校验正确的文件会保留，未完成文件重新展开。
- 导入完成：自动进入游戏。确认可以游玩后，可删除手机里的六个分卷，释放约 3 GB 空间；保留 `game/Data`。

也可直接复制自己已有的完整 Data，目录结构必须是 `Biko3/game/Data`，避免多嵌套一层 `Data/Data`。dev.2 起支持分卷导入；新图标与下述手柄布局从 dev.3 起提供。自行打包和 SHA-256 校验方法见[资源分卷指南](docs/resource-packs.md)。

## 操作

虚拟手柄与实体手柄按相同的位置映射：

```text
       Y
   X       B
       A
```

| 位置 | 游戏按键 |
| --- | --- |
| 上 | Y |
| 下 | A |
| 左 | X |
| 右 | B |

实体手柄按四个面键的物理位置映射；不同品牌的印刷字样可能不同。肩键、扳机、方向键和摇杆保持各自功能。

连接系统可识别的实体手柄后，虚拟手柄自动隐藏，断开后恢复。虚拟手柄顶部的 `2D / 3D` 按钮用于切换布局；需要方向键和右摇杆时切换至 `3D`。右摇杆通过右侧空白区域滑动操作。

## 存档、更新和日志

以下路径均以“文件 → 我的 iPhone → Biko3”为根目录：

| 路径 | 内容 |
| --- | --- |
| `game/Data` | 展开后的游戏资源，游玩时需保留 |
| `ResourcePacks` | 可选的资源分卷存放目录 |
| `saves` | 存档，建议定期备份 |
| `log.txt` | 本次启动与运行日志 |
| `log.prev.txt` | 上一次运行日志 |

更新时用相同应用标识覆盖安装，先备份 `saves`，避免通过卸载应用来更新而丢失本地数据。

遇到闪退、黑屏、无声或操作异常，可在 [Issues](https://github.com/hyc1743/biko3-ios/issues) 提供 iPhone 型号、iOS 版本、应用版本、复现步骤，以及 `log.txt` 和 `log.prev.txt`。画面问题可附截图或录屏。只保留上一轮日志，出问题后请尽快导出，避免连续重启覆盖。日志保存在本机，不会自动上传。

## 参考项目与致谢

感谢以下项目及其贡献者：

- [seedds/nxnative](https://github.com/seedds/nxnative)：本项目使用的原生宿主、指令改写工具、平台服务及触控／手柄前端的基础。
- [jhaizhou-ops/d2r-mobile](https://github.com/jhaizhou-ops/d2r-mobile)：参考其将原有游戏适配到 iOS 的实现与使用体验设计。
- [LNPANDY/nxnative-romfs-archive](https://github.com/LNPANDY/nxnative-romfs-archive)：参考其解决大量资源文件传输与安装问题的分卷思路。本项目使用自己的 `.b3data` 格式并在首次启动时展开资源。
- [KhronosGroup/MoltenVK](https://github.com/KhronosGroup/MoltenVK)：将 Vulkan 图形与计算调用接到 Metal。
- [switchbrew/libnx](https://github.com/switchbrew/libnx) 及相关工具链贡献者：平台接口资料与运行时兼容参考。

各项目保留其原有许可证，详见 [THIRD_PARTY.md](THIRD_PARTY.md)。本仓库原创适配代码与工具使用 [MIT 许可证](LICENSE)；游戏程序、图像及其他资源的权利归原权利人所有。
