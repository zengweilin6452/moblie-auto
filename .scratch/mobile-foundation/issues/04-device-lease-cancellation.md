# 04: 跨进程设备租约与取消语义

**What to build:** 测试与调试不再争用设备、用例超时不再引发重复业务操作:跨进程文件租约按设备序列号加锁;每个操作带发送阶段取消检查点;会话故障进入 FAULTED 后拒绝后续设备操作且取证降级(spec 用户故事 24、25、26)。

**Blocked by:** 02 设备会话与第一条真机用例

**Status:** resolved

- [x] 第二进程请求忙设备立即得到 busy,不排队抢占;锁文件统一目录、按设备序列号摘要命名
- [x] 模拟设备下"匹配完成后、点击发送前取消"不发出点击(确定性测试)
- [x] 已进入发送阶段的操作被取消:结果记为 unknown,不自动重放相同动作
- [x] 排队未开始的任务可取消;运行中的在途调用结束前不释放租约
- [x] FAULTED 会话拒绝新设备操作;失败取证不再调用挂起的设备线程
- [x] close 排空有界,超时报会话故障;重复关闭安全;锁随进程退出释放,不靠删文件或过期时间偷锁
- [x] 会话状态机转换有测试覆盖;session 级 fixture 与 function 级事件循环组合验证通过

## Answer

交付(本工单提交):

- `lease.py`(新文件):DeviceLease——msvcrt 文件区域锁,非阻塞获取(忙→立即 DeviceBusy 不排队);锁文件在 `~/.mobile-auto/leases/`,以序列号 sha256 前 24 位命名不泄露序列号;释放仅解锁句柄不删文件;进程退出由 OS 释放;非 Windows 报不支持。
- `session.py`:状态机完整化 NEW→READY→LEASED→DRAINING→CLOSED + FAULTED;start() 获取租约(可注入 lease_factory,测试用 NullLease/tmp 目录);acquire_lease/release_lease 支持网页短事务在 READY/LEASED 间切换;取消标记(_OpState)+ send_checkpoint 发送阶段检查点(通过检查才记 entered_send);mark_cancelled/operation_outcome(结局固化:cancelled-queued/cancelled-before-send/unknown/completed/failed/running);close(timeout=10s) 有界排空——取消排队任务、等在途结束,超时进 FAULTED 并保留租约,重复 close 安全。
- `adapter.py`:_call 在等待方取消时向会话登记;click/input_text 在"匹配完成后、发送前"调用 send_checkpoint(必测窗口)。

验证:ruff 0 问题;单元 126 项通过(连跑 3 轮无 flake);真机 4 项通过(会话持真实租约,锁文件落盘验证)。

决定与说明:

- 租约从 lease.py 独立模块实现(plan §10 归在 session.py,拆分以控制单文件规模,职责不变)。
- send_checkpoint 通过后才置 entered_send:在检查点处被取消的操作结局是 cancelled-before-send 而非 unknown——语义为"未发送"。
- 已发送后取消的结局固化为 unknown:即使设备侧动作实际完成,等待方未感知即记 unknown(plan 7.1),不自动重放。
- close 排空不用 executor.shutdown(cancel_futures):改为遍历取消 + 条件变量等待,保证 FAULTED 重试 close 可复用同一线程池;FAULTED 下线程可能延迟解释器退出,已在 close docstring 与错误信息中注明。
- 手动 release_lease 后 close 不会二次释放(_lease_held 跟踪);acquire_lease 忙时会话保持 READY。
- 取证降级(工单 05)依赖本票机制:FAULTED 后 submit 立即抛 SessionFaulted,取证路径不会把调用挂进 hung 线程。
