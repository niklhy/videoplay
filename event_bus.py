# -*- coding: utf-8 -*-
"""事件总线模块（对应设计文档第5章）。

提供线程安全的事件订阅/发布机制：
- subscribe / unsubscribe：维护事件类型到回调列表的映射，内部加锁保护；
- publish：异步入队（queue.Queue），由 process_events 在 tkinter 主线程中消费，
  避免工作线程直接触碰 UI；
- publish_sync：立即在当前线程同步派发，供启动阶段等必须即时生效的场景使用；
- process_events：排空事件队列并逐个调用回调，供 tkinter ``after`` 轮询调用。

事件类型常量统一从契约文件 events.py 导入，本模块不重复定义。
仅使用标准库。
"""
import queue
import sys
import threading
import traceback


class EventBus:
    """线程安全的事件总线。

    属性:
        _subscribers: dict[str, list[callable]]  事件类型 -> 回调列表
        _queue: queue.Queue                      异步事件队列，元素为 (event_type, kwargs)
        _lock: threading.Lock                    保护 _subscribers 的锁
    """

    def __init__(self):
        """初始化事件总线。"""
        self._subscribers = {}
        self._queue = queue.Queue()
        self._lock = threading.Lock()

    def subscribe(self, event_type: str, callback: callable) -> None:
        """订阅指定事件类型。

        参数:
            event_type: 事件类型（使用 events.py 中的常量）
            callback:   回调函数，签名 callback(**kwargs)
        """
        if not callable(callback):
            return
        with self._lock:
            callbacks = self._subscribers.setdefault(event_type, [])
            if callback not in callbacks:
                callbacks.append(callback)

    def unsubscribe(self, event_type: str, callback: callable) -> None:
        """取消订阅指定事件类型。

        若该回调不存在则静默忽略。

        参数:
            event_type: 事件类型
            callback:   之前注册的回调函数
        """
        with self._lock:
            callbacks = self._subscribers.get(event_type)
            if not callbacks:
                return
            if callback in callbacks:
                callbacks.remove(callback)
            if not callbacks:
                # 回调列表为空时移除键，保持订阅表整洁
                del self._subscribers[event_type]

    def publish(self, event_type: str, **kwargs) -> None:
        """异步发布事件：将事件放入队列，由 process_events 消费派发。

        可在任意线程（含工作线程）安全调用，不会阻塞发布方。

        参数:
            event_type: 事件类型
            **kwargs:   随事件传递的任意关键字参数
        """
        self._queue.put((event_type, kwargs))

    def publish_sync(self, event_type: str, **kwargs) -> None:
        """同步发布事件：立即在当前线程调用所有订阅者回调。

        单个订阅者抛出的异常会被捕获并打印到 stderr，不影响其余订阅者。

        参数:
            event_type: 事件类型
            **kwargs:   随事件传递的任意关键字参数
        """
        with self._lock:
            callbacks = list(self._subscribers.get(event_type, []))
        for callback in callbacks:
            try:
                callback(**kwargs)
            except Exception:
                # 回调异常不影响其他订阅者，仅记录到 stderr
                print(f"[EventBus] 订阅者回调异常 (event={event_type}):",
                      file=sys.stderr)
                traceback.print_exc()

    def process_events(self) -> None:
        """消费事件队列，逐个同步派发已入队的事件。

        设计为在 tkinter 主线程中通过 ``root.after`` 周期轮询调用，
        从而把后台线程产生的事件安全地切换到 UI 线程处理。
        本方法排空当前队列中的所有事件；单个回调异常不影响其余事件。
        """
        while True:
            try:
                event_type, kwargs = self._queue.get_nowait()
            except queue.Empty:
                break
            try:
                self.publish_sync(event_type, **kwargs)
            except Exception:
                # publish_sync 内部已捕获回调异常，此处兜底防止队列消费中断
                print(f"[EventBus] 事件派发异常 (event={event_type}):",
                      file=sys.stderr)
                traceback.print_exc()
