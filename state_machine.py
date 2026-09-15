# -*- coding: utf-8 -*-
"""状态机模块（对应设计文档第6章）。

管理应用的整体加载状态，约束合法的状态转换：

- IDLE -> DEVICE_LOADING        （选中设备，开始加载设备）
- DEVICE_LOADING -> FOLDER_LOADING （设备就绪，开始加载文件夹）
- FOLDER_LOADING -> READY       （文件夹加载完成，进入就绪）
- READY -> IDLE                 （用户切换设备时复位）

状态常量取自契约文件 constants.py，状态变化通过事件总线发布
EVENT_STATE_CHANGED（old_state / new_state / context）事件，
事件常量取自契约文件 events.py。仅使用标准库。
"""
import threading
import time

from constants import (
    STATE_IDLE,
    STATE_DEVICE_LOADING,
    STATE_FOLDER_LOADING,
    STATE_READY,
)
from events import EVENT_STATE_CHANGED
from event_bus import EventBus


class AppStateMachine:
    """应用状态机。

    属性:
        current_state: str            当前状态（STATE_* 常量之一）
        _previous_state: str          上一个状态
        _lock: threading.Lock         保护状态读写的锁
        _pending_transitions: list    最近的状态转换记录 [(new_state, context), ...]
        _event_bus: EventBus          事件总线实例
        _condition: threading.Condition 供 wait_for_state 使用的条件变量
    """

    # 合法状态转换规则表：当前状态 -> 允许的目标状态集合
    _TRANSITIONS = {
        STATE_IDLE: {STATE_DEVICE_LOADING},
        STATE_DEVICE_LOADING: {STATE_FOLDER_LOADING},
        STATE_FOLDER_LOADING: {STATE_READY},
        STATE_READY: {STATE_IDLE},
    }

    # _pending_transitions 保留的最大记录数
    _MAX_PENDING_RECORDS = 50

    def __init__(self, event_bus: EventBus):
        """初始化状态机，起始状态为 IDLE。

        参数:
            event_bus: 事件总线实例，用于发布状态变化事件
        """
        self._event_bus = event_bus
        self.current_state = STATE_IDLE
        self._previous_state = STATE_IDLE
        self._lock = threading.Lock()
        self._pending_transitions = []
        self._condition = threading.Condition()

    def get_state(self) -> str:
        """获取当前状态。

        返回:
            当前状态字符串（STATE_* 常量之一）
        """
        with self._lock:
            return self.current_state

    def can_transition(self, new_state: str) -> bool:
        """判断当前状态能否转换到目标状态。

        参数:
            new_state: 目标状态

        返回:
            转换合法返回 True，非法（含未知状态、原地转换）返回 False
        """
        with self._lock:
            allowed = self._TRANSITIONS.get(self.current_state, set())
            return new_state in allowed

    def transition(self, new_state: str, **context) -> bool:
        """尝试转换到目标状态。

        转换成功时：
        1. 更新 _previous_state 与 current_state；
        2. 记录转换上下文到 _pending_transitions；
        3. 调用对应的 _on_enter_* 钩子方法；
        4. 通过事件总线发布 EVENT_STATE_CHANGED 事件
           （old_state / new_state / context）。

        转换非法时不做任何修改，返回 False。

        参数:
            new_state: 目标状态
            **context: 随转换传递的上下文信息（如设备ID、文件夹路径等）

        返回:
            转换成功返回 True，非法转换返回 False
        """
        with self._lock:
            allowed = self._TRANSITIONS.get(self.current_state, set())
            if new_state not in allowed:
                return False
            old_state = self.current_state
            self._previous_state = old_state
            self.current_state = new_state
            self._record_transition(new_state, context)

        # 调用进入钩子时不在持锁状态执行，避免钩子内再查询状态造成死锁
        hook = self._get_enter_hook(new_state)
        if hook is not None:
            hook(context)

        # 通过事件总线发布状态变化（异步派发，供订阅方按需处理）
        try:
            self._event_bus.publish(
                EVENT_STATE_CHANGED,
                old_state=old_state,
                new_state=new_state,
                context=context,
            )
        except Exception as exc:
            # 事件总线故障不应导致状态转换失败，仅记录
            print(f"[AppStateMachine] 发布状态变化事件失败: {exc}")

        # 唤醒等待状态变化的线程（wait_for_state）
        with self._condition:
            self._condition.notify_all()
        return True

    def wait_for_state(self, target_state: str, timeout: float = 30.0) -> bool:
        """带超时地等待进入目标状态（基于条件变量）。

        可在工作线程中调用，当状态机转换到 target_state 时被唤醒。

        参数:
            target_state: 等待的目标状态
            timeout:      最长等待秒数，默认 30 秒

        返回:
            在超时前等到目标状态返回 True，超时返回 False
        """
        deadline = time.monotonic() + timeout
        with self._condition:
            while self.current_state != target_state:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return False
                self._condition.wait(timeout=remaining)
            return True

    # ------------------------------------------------------------------
    # 内部辅助方法
    # ------------------------------------------------------------------

    def _get_enter_hook(self, state: str):
        """获取指定状态对应的进入钩子方法。

        参数:
            state: 目标状态

        返回:
            钩子方法 callable，未定义时返回 None
        """
        hooks = {
            STATE_DEVICE_LOADING: self._on_enter_device_loading,
            STATE_FOLDER_LOADING: self._on_enter_folder_loading,
            STATE_READY: self._on_enter_ready,
            STATE_IDLE: self._on_enter_idle,
        }
        return hooks.get(state)

    def _record_transition(self, new_state: str, context: dict) -> None:
        """记录一次状态转换（调用方需持有 _lock）。

        参数:
            new_state: 目标状态
            context:   转换上下文
        """
        self._pending_transitions.append((new_state, dict(context)))
        if len(self._pending_transitions) > self._MAX_PENDING_RECORDS:
            # 只保留最近若干条记录，防止无限增长
            del self._pending_transitions[
                :len(self._pending_transitions) - self._MAX_PENDING_RECORDS
            ]

    # ------------------------------------------------------------------
    # 状态进入钩子（默认行为完整实现，子类可覆写以扩展）
    # ------------------------------------------------------------------

    def _on_enter_device_loading(self, context: dict) -> None:
        """进入 DEVICE_LOADING 状态的默认处理。

        默认行为：清空历史转换记录并记录当前加载上下文，
        供设备加载流程作为新一轮加载的起点。

        参数:
            context: 转换上下文（通常包含 device_id 等）
        """
        self._pending_transitions = []

    def _on_enter_folder_loading(self, context: dict) -> None:
        """进入 FOLDER_LOADING 状态的默认处理。

        默认行为：保留当前上下文，文件夹加载流程据此继续执行。

        参数:
            context: 转换上下文（通常包含 folder_path 等）
        """
        # 规范化上下文，保证下游加载流程能安全读取这两个键
        context.setdefault("folder_path", "")
        context.setdefault("recursive", False)

    def _on_enter_ready(self, context: dict) -> None:
        """进入 READY 状态的默认处理。

        默认行为：无需额外处理，订阅方通过 EVENT_STATE_CHANGED 事件感知就绪。

        参数:
            context: 转换上下文
        """
        # 记录就绪时间戳，供启动恢复/日志等场景参考
        context.setdefault("ready_time", time.time())

    def _on_enter_idle(self, context: dict) -> None:
        """进入 IDLE 状态的默认处理。

        默认行为：清空历史转换记录，复位到初始状态。

        参数:
            context: 转换上下文
        """
        self._pending_transitions = []
