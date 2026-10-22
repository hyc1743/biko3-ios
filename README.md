# 尾行 3 · Biko3 iOS

在 iPhone 上游玩《尾行 3》，支持横屏、屏幕虚拟手柄和实体手柄。

## 准备

- 安装 iOS 26 或更新版本的 iPhone。
- 从 [Releases](https://github.com/hyc1743/biko3-ios/releases) 下载新版 `Biko3-development.ipa`，使用自己的签名与侧载工具安装。IPA 未签名，项目不提供签名证书。
- 准备你自己的完整游戏 `Data` 文件夹。IPA 和本仓库均不附带游戏数据或资源分卷。
- 手机需要容纳约 3 GB 的分卷和约 3 GB 的展开数据，首次导入时请预留足够空间。

## 安装游戏资源

推荐先把 Data 打成六个大文件，再传入手机，避免逐个复制小文件。已导入过资源的玩家更新 IPA 后可以继续使用原有数据。

### 1. 在电脑上打包

下载本仓库源码，安装 Python 3.12 或更新版本，在项目目录执行：

```powershell
python tools/pack_resources.py pack --data "D:/你的游戏/Data" --out "out/resource-packs"
python tools/pack_resources.py verify --parts "out/resource-packs" --data "D:/你的游戏/Data"
```

将示例路径替换成自己的 Data 路径。输出目录必须尚不存在；重复打包时请换一个目录。工具只使用 Python 标准库，无需另装 Python 依赖。

打包后得到 `Biko3Data.part001.b3data` 至 `Biko3Data.part006.b3data`。应用内置匹配的文件清单，只有路径、大小和内容与该清单一致的原始 Data 才能导入；修改版或其他版本的资源可能无法通过校验。

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

也可直接复制完整 Data，目录结构必须是 `Biko3/game/Data`，避免多嵌套一层 `Data/Data`。dev.2 起支持分卷导入；新图标与下述手柄布局从 dev.3 起提供。更多说明见[资源分卷指南](docs/resource-packs.md)。

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
