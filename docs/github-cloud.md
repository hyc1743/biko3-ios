# GitHub 云端构建接入

2026-10-22 已通过 GitHub API 验证当前用户为 `hyc1743`。本项目目录直接获取 Git 凭据失败，但已有 `../super-mario-odyssey-ios` 仓库的局部 Git 配置可以非交互获取认证。无需复制凭据文件或安装 GitHub CLI。

工具只从 `GH_TOKEN`、`GITHUB_TOKEN` 或 Git Credential Manager 获取认证，不将令牌写入命令行、仓库、临时文件或日志。Git push 通过子进程环境传递认证。Actions artifact 跨主机下载跳转时会移除 Authorization。

```powershell
python -m pip install -r requirements.txt
python tools/github_cloud.py --credential-dir ../super-mario-odyssey-ios identity
```

公开前先准备 JSON 数组文件清单，其中每项必须是项目内的一个确切相对文件路径；不能使用目录或通配符。`review` 显示文件大小和 SHA-256，供核对实际待发布内容。工具拒绝明文游戏、密钥、生成目录、任意二进制、链接和路径穿越。唯一加密输入例外是 `ci-inputs/guest.enc`：必须符合版本化 AES-256-GCM 容器格式，且明文摘要必须匹配固定的原始 Biko3 NRO。

```powershell
python tools/github_cloud.py review --manifest public-files.json
```

确认具体源码和构建文件后，可按顺序执行以下有外部副作用的操作；工具不会自动创建仓库或上传文件。`publish` 在临时仓库中创建只含清单文件的提交，使用普通 push，拒绝强制覆盖，并核对实际 Git 暂存文件集合。

```powershell
python tools/github_cloud.py --credential-dir ../super-mario-odyssey-ios create-public biko3-ios
python tools/github_cloud.py --credential-dir ../super-mario-odyssey-ios set-secret hyc1743/biko3-ios BIKO3_GUEST_KEY --value-file .tools/private/biko3-guest.key
python tools/github_cloud.py --credential-dir ../super-mario-odyssey-ios publish hyc1743/biko3-ios --manifest public-files.json
python tools/github_cloud.py --credential-dir ../super-mario-odyssey-ios dispatch hyc1743/biko3-ios build-ios.yml
python tools/github_cloud.py --credential-dir ../super-mario-odyssey-ios runs hyc1743/biko3-ios build-ios.yml
python tools/github_cloud.py --credential-dir ../super-mario-odyssey-ios run hyc1743/biko3-ios 12345
python tools/github_cloud.py --credential-dir ../super-mario-odyssey-ios artifacts hyc1743/biko3-ios 12345
python tools/github_cloud.py --credential-dir ../super-mario-odyssey-ios download hyc1743/biko3-ios 67890 work/cloud/artifact.zip
```

`12345` 和 `67890` 分别替换为实际 run id 和 artifact id。`dispatch` API 不返回 run id；通过 `runs` 的创建时间和 `head_sha` 确认对应运行，再按 id 查询。单次查询不会因为 observation 超时而重启构建。

首次 push 到 main 会自动触发构建，因此应先配置 Secret。已有 push 构建正在运行时，查询该运行即可；`dispatch` 用于需要主动重跑的场景。

本项目按用户要求在公共仓库运行 macOS Actions，生成包含 guest executable 的 IPA，游戏 Data 由手机 Documents 导入；keys、NSP 和 Data 不公开。原始 NRO 使用 AES-256-GCM 加密为 `ci-inputs/guest.enc`，密钥只存本地 `.tools/private/biko3-guest.key` 并通过 GitHub Actions Secret 传入构建。CI 解密时同时验证认证标签、固定原始 NRO 摘要及 NRO 标志；被篡改的输入或错误密钥不能继续构建。公开 IPA 是开发产物，其游戏可玩性必须单独验证。

```powershell
python tools/ci_guest.py seal switch/biko3/biko3-runtime.nro ci-inputs/guest.enc --manifest ci-inputs/guest.json
python tools/github_cloud.py --credential-dir ../super-mario-odyssey-ios set-secret hyc1743/biko3-ios BIKO3_GUEST_KEY --value-file .tools/private/biko3-guest.key
```

`seal` 只接受固定 NRO，不读取 Switch keys、NSP 或 Data。首次执行随机生成 32 字节密钥；已有密钥会复用，已有输出会拒绝覆盖。`set-secret` 使用仓库的 GitHub 公钥进行 sealed box 加密后上传，不输出密钥。CI 使用 `BIKO3_GUEST_KEY` 环境变量调用 `python tools/ci_guest.py open ci-inputs/guest.enc "$RUNNER_TEMP/guest.nro"`；只输出字节数和摘要。

当前工具的完成不代表游戏移植完成；实际 IPA 必须由 iOS 工程编译、检查包内容、签名安装并验证启动与游戏功能。
