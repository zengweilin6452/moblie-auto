"""取消语义、发送阶段检查点、FAULTED 与跨事件循环组合(plan 7.1/7.3)。"""

import asyncio
import threading

import pytest
from fakes import FakeBackend, FakeElement, make_adapter, make_session

from mobile_auto import (
    DeviceBusy,
    Locator,
    MobileAdapter,
    MobileConfig,
    SessionFaulted,
    Target,
)
from mobile_auto.core.lease import DeviceLease
from mobile_auto.core.session import SessionState

SMALL = MobileConfig(device_serial="SERIAL1", wait_timeout=2, poll_interval=0.02)


# ── 排队未开始:可取消 ──────────────────────────────────────────


def test_queued_task_cancelled_without_running():
    session, _ = make_session()
    session.start()
    ran = []
    release = threading.Event()

    def blocking(backend):
        ran.append("first")
        assert release.wait(2)

    session.submit(blocking)
    future2 = session.submit(lambda backend: ran.append("second"))
    assert future2.cancel() is True  # 排队未开始 → 取消成功
    release.set()
    session.close()
    assert ran == ["first"]


def test_mark_cancelled_unknown_after_entering_send():
    session, _ = make_session()
    session.start()
    started = threading.Event()
    release = threading.Event()

    def operation(backend):
        session.send_checkpoint()  # 进入发送阶段
        started.set()
        assert release.wait(2)
        return "device-done"

    future = session.submit(operation)
    assert started.wait(2)
    assert session.mark_cancelled(future) == "unknown"
    release.set()
    assert future.result(2) == "device-done"  # 设备侧完成了,但等待方已被取消
    assert session.operation_outcome(future) == "unknown"


def test_operation_outcome_cancelled_before_send():
    session, _ = make_session()
    session.start()
    release = threading.Event()

    def operation(backend):
        assert release.wait(2)  # 匹配阶段阻塞,尚未进入发送
        session.send_checkpoint()  # 这里应发现取消,不发送
        return "sent"

    future = session.submit(operation)
    session.mark_cancelled(future)
    release.set()
    assert future.exception(2) is not None  # 未发送,以内部取消错误收尾
    assert session.operation_outcome(future) == "cancelled-before-send"


# ── 必测窗口:匹配完成后、点击发送前取消 → 不发出点击 ─────────


async def test_cancel_between_match_and_send_does_not_click():
    element = FakeElement((0, 0, 10, 10))
    found = threading.Event()
    release = threading.Event()

    def slow_match(by, value, el):
        found.set()
        assert release.wait(2)  # 定位已完成、点击尚未发送的窗口
        return True

    backend = FakeBackend(
        element_filter=slow_match, elements={("text", "x"): [element]}
    )
    adapter, _ = make_adapter(backend=backend, config=SMALL)
    task = asyncio.ensure_future(adapter.click(Target(Locator.TEXT, "x")))
    assert await asyncio.to_thread(found.wait, 2)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task  # 取消已在会话登记(mark_cancelled 完成)
    release.set()
    await asyncio.sleep(0.1)  # 等线程走到检查点
    assert element.click_calls == 0  # 取消在发送前生效,未发出点击


async def test_handle_click_cancelled_during_revalidation_does_not_click():
    """句柄操作在重定位期间被取消:取消标记传播 + 发送前检查点,
    不得再发出点击。"""
    element = FakeElement((0, 0, 10, 10), info={"className": "android.widget.TextView"})
    revalidating = threading.Event()
    release = threading.Event()

    def slow_revalidate(by, value, el):
        revalidating.set()
        assert release.wait(2)  # 重定位进行中、动作尚未发送的窗口
        return True

    backend = FakeBackend(
        element_filter=slow_revalidate,
        elements={("text", "x"): [element]},
        find_script=[[element]],  # 首次查询立即返回,用于建立句柄
    )
    adapter, _ = make_adapter(backend=backend, config=SMALL)
    handles = await adapter.find_elements(Target(Locator.TEXT, "x"))
    assert len(handles) == 1

    task = asyncio.ensure_future(handles[0].click())
    assert await asyncio.to_thread(revalidating.wait, 2)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task  # 等待方取消已在会话登记
    release.set()
    await asyncio.sleep(0.1)  # 等线程走到检查点
    assert element.click_calls == 0  # 取消在发送前生效,未发出点击


# ── FAULTED:close 排空超时保持故障与租约 ───────────────────────


def test_close_timeout_faults_and_holds_lease():
    leases = []
    session, backend = make_session(lease_factory=lambda s: _track(leases, s))
    session.start()
    release = threading.Event()

    def hung(backend):
        assert release.wait(2)

    session.submit(hung)
    threading.Event().wait(0.05)  # 让操作进入运行
    with pytest.raises(SessionFaulted, match="保持 FAULTED"):
        session.close(timeout=0.1)
    assert session.state is SessionState.FAULTED
    assert leases[0].released == 0  # 租约保持
    with pytest.raises(SessionFaulted, match="故障"):
        session.submit(lambda backend: 1)  # 拒绝新操作
    release.set()
    session.close()  # 在途结束后重试排空
    assert session.state is SessionState.CLOSED
    assert leases[0].released == 1


def _track(leases, serial):
    from fakes import NullLease

    lease = NullLease(serial)
    leases.append(lease)
    return lease


# ── 跨进程租约集成:第二个会话立即 busy ────────────────────────


def test_second_session_gets_busy_not_queued(tmp_path):
    lease_dir = tmp_path / "leases"

    def factory(serial):
        return DeviceLease(serial, lease_dir=lease_dir)

    first, first_backend = make_session(lease_factory=factory)
    first.start()
    second, second_backend = make_session(lease_factory=factory)
    with pytest.raises(DeviceBusy):
        second.start()
    assert second_backend.connect_calls == []  # 未获得租约不连接设备
    first.close()
    second.start()  # 释放后可获得
    second.close()


def test_lease_lifecycle_in_session_and_manual_switch():
    leases = []
    session, _ = make_session(lease_factory=lambda s: _track(leases, s))
    session.start()
    assert session.state is SessionState.LEASED
    assert leases[0].acquired == 1
    session.release_lease()
    assert session.state is SessionState.READY
    session.acquire_lease()
    assert session.state is SessionState.LEASED
    session.close()
    assert session.state is SessionState.CLOSED
    # release_lease 一次 + close 时仍持有(重新获取过)一次
    assert leases[0].released == 2


def test_acquire_lease_busy_keeps_ready_state():
    class StubLease:
        busy = False

        def acquire(self):
            if self.busy:
                raise DeviceBusy("busy")

        def release(self):
            pass

    stub = StubLease()
    session, _ = make_session(lease_factory=lambda s: stub)
    session.start()
    session.release_lease()
    stub.busy = True
    with pytest.raises(DeviceBusy):
        session.acquire_lease()
    assert session.state is SessionState.READY  # 忙时状态不变


def test_release_reacquire_waits_for_actual_unlock(tmp_path):
    """释放的窗口期重新获取不得复用旧锁:状态迁移与 OS 锁释放必须原子。

    旧实现先改状态后放锁——窗口里重新获取因同进程已持锁而空转成功,
    随后被前一次释放放掉,会话自认持锁而互斥已失(第三方可取同一锁)。
    """
    lease_dir = tmp_path / "leases"
    inner = DeviceLease("SERIAL1", lease_dir=lease_dir)
    gate = threading.Event()
    release_started = threading.Event()

    class GatedLease:
        def acquire(self):
            inner.acquire()

        def release(self):
            release_started.set()
            assert gate.wait(5)
            inner.release()

    session, _ = make_session(lease_factory=lambda s: GatedLease())
    session.start()  # LEASED:inner 持真实文件锁

    releaser = threading.Thread(target=session.release_lease, daemon=True)
    releaser.start()
    assert release_started.wait(2)  # 释放已开始并被闸住(状态尚未对外可见)

    acquirer_done = []
    acquirer = threading.Thread(
        target=lambda: (session.acquire_lease(), acquirer_done.append(True)),
        daemon=True,
    )
    acquirer.start()
    acquirer.join(0.2)
    assert not acquirer_done  # 窗口期获取必须等待,不得复用未释放的旧锁

    gate.set()  # 真实解锁
    releaser.join(2)
    acquirer.join(2)
    assert acquirer_done
    assert inner.held  # 重新获取持有的是真实文件锁,而非空转复用
    third = DeviceLease("SERIAL1", lease_dir=lease_dir)
    with pytest.raises(DeviceBusy):  # 互斥未失:第三方此时取不到同一把锁
        third.acquire()
    session.close()


# ── session 级 fixture 与 function 级事件循环组合(plan 7.1)───


@pytest.fixture(scope="module")
def shared_session():
    session, backend = make_session()
    session.start()
    yield session
    session.close()


async def test_first_loop_shares_sync_session(shared_session):
    adapter = MobileAdapter(shared_session)
    assert await adapter.screenshot() == b"PNG-1"


async def test_second_loop_shares_sync_session(shared_session):
    adapter = MobileAdapter(shared_session)
    assert await adapter.screenshot() == b"PNG-2"  # 新事件循环复用同一同步会话
