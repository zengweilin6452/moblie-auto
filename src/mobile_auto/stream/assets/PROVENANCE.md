# scrcpy 资产来源登记(P1 工单 01;P2 上游迁移补 scrcpy-server-upstream.jar)

| 文件 | 字节 | SHA256 | 来源 |
| :-- | --: | :-- | :-- |
| `scrcpy-server` | 206042 | `6a805e76ea8683223b9c63a683350cd7ef7434a188423ab3dcc7eec2a134dcb2` | `wetest-scrcpy==0.3.8` wheel 内 `wetest/scrcpy/resource/android/scrcpy-server` |
| `libjpeg-turbo.so` | 2458736 | `b26ab7d352be585714c76c6c74a5a1e3098f9d32b8d81219b35cf4be6f8fe952` | 同上(`libjpeg-turbo/arm64/`) |
| `scrcpy-server-upstream.jar` | 733930 | `764eb6f79811d5211fe9df341120882ba9994c7a61b897d7bf3fb662e53bc536` | `https://github.com/Genymobile/scrcpy/releases/download/v5.0.1/scrcpy-server-v5.0.1`(Apache-2.0) |

- `scrcpy-server-upstream.jar`(2026-10-25,feat/scrcpy-upstream 分支):上游官方
  server v5.0.1,含 Android 16 QPR2+/17 的 `IDisplayWindowListener` 崩溃修复
  (issue #6362)。与 wetest server 协议不兼容,仅供 UpstreamChannel 使用;
  上游通道不需要 libjpeg-turbo.so(无 CAPTURE 消息)。

- 提取脚本:`scripts/fetch_scrcpy_server.py`(幂等,摘要校验)。
- 选取原因:`wetest-scrcpy==0.4.0` wheel 未打包 server(上游打包回归);0.3.8 的 server
  与 0.4.0 客户端协议一致(`1.2.4` + `udt_socket_name`),真机 spike 验证兼容性。
- `scrcpy-server` 为 Genymobile scrcpy(Apache-2.0)的 WeTest 修改版
  (增加 udt_socket_name 多 socket、CAPTURE/GET_SCREENINFO 等控制消息)。
- `libjpeg-turbo.so`:server 端 CAPTURE 截图的 turbojpeg 编码依赖。0.3.8/0.4.0
  客户端源码均不推送它(wheel 打包但无人使用);真机 2026-10-24 实测:缺失时
  CAPTURE 返回空帧(server 日志:`UnsatisfiedLinkError: dlopen failed:
  library "/data/local/tmp/udt//libjpeg-turbo.so" not found`)且进入约 60s
  捕获停摆。加载路径硬编码为 `/data/local/tmp/udt/`(自定义目录参数
  `lib_jpeg_turbo_dir_env` 实测无效),通道因此推送到
  `/data/local/tmp/udt/libjpeg-turbo.so`(与 wetest 工具同字节,幂等)。
- server 版本:启动日志 `Start wait multi connection, version: 3.3.2.11`
  (WeTest 修改版,协议号 1.2.4)。
- 许可证状态:wetest-scrcpy PyPI 元数据未填 License 字段、无公开源码仓库;
  内网接入前需按团队合规要求复核(见 docs/offline-install.md 待核查项)。
