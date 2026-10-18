"""audio.py - 按进程静音/取消静音(Core Audio,纯 ctypes 实现,无第三方依赖)。

遍历所有渲染设备的音频会话,找到属于目标 PID 的会话,
调用 ISimpleAudioVolume::SetMute。这就是系统音量合成器里
每个应用那一栏的静音,不影响程序运行。

注意 CLSID_MMDeviceEnumerator 是 {BCDE0395-E52F-467C-8E3D-C4579291692E}
(第三段 467C,见 mmdeviceapi.h;曾误记为 467E 导致 CoCreateInstance
返回 REGDB_E_CLASSNOTREG,排查许久)。
"""
from __future__ import annotations

import ctypes
import uuid
from ctypes import wintypes

from .trayhide import GUID

ole32 = ctypes.windll.ole32

# ---- GUID ----
IID_IMMDeviceEnumerator = GUID.from_uuid(uuid.UUID("a95664d2-9614-4f35-a746-de8db63617e6"))
CLSID_MMDeviceEnumerator = GUID.from_uuid(uuid.UUID("bcde0395-e52f-467c-8e3d-c4579291692e"))
IID_IAudioSessionManager2 = GUID.from_uuid(uuid.UUID("77aa99a0-1bd6-484f-8bc7-2c654c9a9b6f"))
IID_IAudioSessionEnumerator = GUID.from_uuid(uuid.UUID("e2f5bb11-0570-40ca-acdd-3aa01277dee8"))
IID_IAudioSessionControl2 = GUID.from_uuid(uuid.UUID("bfb7ff88-7239-4fc9-8fa2-07c950be9c6d"))
IID_ISimpleAudioVolume = GUID.from_uuid(uuid.UUID("87ce5498-68d6-44e5-9215-6da47ef883d8"))

# vtable 方法索引(IUnknown 占 0-2)
IDX_ENUM_AUDIO_ENDPOINTS = 3      # IMMDeviceEnumerator
IDX_ITEM = 4                      # IMMDeviceCollection
IDX_GET_COUNT = 3                 # IMMDeviceCollection / IAudioSessionEnumerator
IDX_ACTIVATE = 3                  # IMMDevice
IDX_GET_SESSION_ENUMERATOR = 5    # IAudioSessionManager2(前面有 2 个方法,实测索引 5)
IDX_GET_SESSION = 4               # IAudioSessionEnumerator
# IAudioSessionControl 占 3..11(共 9 个方法),
# IAudioSessionControl2 追加: GetSessionIdentifier=12,
# GetSessionInstanceIdentifier=13, GetProcessId=14
IDX_GET_PROCESS_ID = 14           # IAudioSessionControl2
IDX_SET_MUTE = 5                  # ISimpleAudioVolume
IDX_GET_MUTE = 6                  # ISimpleAudioVolume

CLSCTX_ALL = 0x17
COINIT_APARTMENTTHREADED = 0
RPC_E_CHANGED_MODE = 0x80010106
EDATA_FLOW_RENDER = 0
DEVICE_STATE_ACTIVE = 0x00000001


def _com_method(iface, index, restype, *argtypes):
    """按 vtable 索引调用 COM 方法。"""
    # iface 是 c_void_p(接口指针);双重解引用: 接口 -> vtable -> 第 index 个函数指针
    vtbl = ctypes.cast(iface, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
    fn = ctypes.CFUNCTYPE(restype, ctypes.c_void_p, *argtypes)(vtbl[index])
    return fn


def _query_interface(iface, iid: GUID):
    out = ctypes.c_void_p()
    fn = _com_method(iface, 0, ctypes.c_long, ctypes.POINTER(GUID), ctypes.c_void_p)
    hr = fn(iface, ctypes.byref(iid), ctypes.byref(out))
    if hr != 0 or not out.value:
        return None
    return out


def _release(iface) -> None:
    if iface:
        fn = _com_method(iface, 2, ctypes.c_ulong)
        fn(iface)


def _co_create_instance(clsid: GUID, iid: GUID):
    """创建 COM 对象;失败返回 None。"""
    proto = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.POINTER(GUID), ctypes.c_void_p,
                               ctypes.c_ulong, ctypes.POINTER(GUID),
                               ctypes.POINTER(ctypes.c_void_p))
    out = ctypes.c_void_p()
    hr = proto(("CoCreateInstance", ole32))(ctypes.byref(clsid), None, CLSCTX_ALL,
                                            ctypes.byref(iid), ctypes.byref(out))
    if hr != 0 or not out.value:
        return None
    return out


def _ensure_com_initialized() -> None:
    """STA 初始化;已是其它单元(RPC_E_CHANGED_MODE/S_FALSE)时忽略。"""
    ole32.CoInitializeEx(None, COINIT_APARTMENTTHREADED)


def _iter_session_controls():
    """产出所有渲染设备上的音频会话控制接口:(pid, ctrl)。

    ctrl 是 IAudioSessionControl,调用方负责 Release。
    用 Activate(而非 QueryInterface)拿会话管理器 —— 文档推荐路径。
    """
    _ensure_com_initialized()
    enumerator = _co_create_instance(CLSID_MMDeviceEnumerator, IID_IMMDeviceEnumerator)
    if not enumerator:
        return

    try:
        collection = ctypes.c_void_p()
        fn = _com_method(enumerator, IDX_ENUM_AUDIO_ENDPOINTS, ctypes.c_long,
                         ctypes.c_int, wintypes.DWORD, ctypes.c_void_p)
        hr = fn(enumerator, EDATA_FLOW_RENDER, DEVICE_STATE_ACTIVE, ctypes.byref(collection))
        if hr != 0 or not collection.value:
            return

        try:
            count = wintypes.UINT(0)
            fn = _com_method(collection, IDX_GET_COUNT, ctypes.c_long, ctypes.c_void_p)
            fn(collection, ctypes.byref(count))

            for i in range(count.value):
                device = ctypes.c_void_p()
                fn = _com_method(collection, IDX_ITEM, ctypes.c_long, ctypes.c_uint, ctypes.c_void_p)
                hr = fn(collection, i, ctypes.byref(device))
                if hr != 0 or not device.value:
                    continue

                mgr = ctypes.c_void_p()
                fn = _com_method(device, IDX_ACTIVATE, ctypes.c_long,
                                 ctypes.POINTER(GUID), wintypes.DWORD,
                                 ctypes.c_void_p, ctypes.c_void_p)
                hr = fn(device, ctypes.byref(IID_IAudioSessionManager2),
                        CLSCTX_ALL, None, ctypes.byref(mgr))
                _release(device)
                if hr != 0 or not mgr.value:
                    continue

                try:
                    sessions = ctypes.c_void_p()
                    fn = _com_method(mgr, IDX_GET_SESSION_ENUMERATOR, ctypes.c_long, ctypes.c_void_p)
                    hr = fn(mgr, ctypes.byref(sessions))
                    if hr != 0 or not sessions.value:
                        continue

                    try:
                        scount = wintypes.UINT(0)
                        fn = _com_method(sessions, IDX_GET_COUNT, ctypes.c_long, ctypes.c_void_p)
                        fn(sessions, ctypes.byref(scount))

                        for j in range(scount.value):
                            ctrl = ctypes.c_void_p()
                            fn = _com_method(sessions, IDX_GET_SESSION, ctypes.c_long,
                                             ctypes.c_int, ctypes.c_void_p)
                            hr = fn(sessions, j, ctypes.byref(ctrl))
                            if hr != 0 or not ctrl.value:
                                continue
                            ctrl2 = _query_interface(ctrl, IID_IAudioSessionControl2)
                            if not ctrl2:
                                _release(ctrl)
                                continue
                            try:
                                pid = wintypes.DWORD(0)
                                fn = _com_method(ctrl2, IDX_GET_PROCESS_ID,
                                                 ctypes.c_long, ctypes.c_void_p)
                                fn(ctrl2, ctypes.byref(pid))
                            finally:
                                _release(ctrl2)
                            # 注意:ctrl 的所有权随 yield 转移给调用方,
                            # 由调用方 Release。这里绝不能再 release ——
                            # 否则调用方拿到的就是已释放指针(COM 双重
                            # 释放会直接搞垮音频服务)。调用方若中途 break
                            # 未耗尽迭代,最坏漏一个引用,好过双重释放。
                            yield int(pid.value), ctrl
                    finally:
                        _release(sessions)
                finally:
                    _release(mgr)
        finally:
            _release(collection)
    finally:
        _release(enumerator)


def _iter_session_volumes(target_pid: int):
    """产出目标进程的所有 ISimpleAudioVolume 接口指针(调用方负责 Release)。"""
    for pid, ctrl in _iter_session_controls():
        try:
            if pid != target_pid:
                continue
            vol = _query_interface(ctrl, IID_ISimpleAudioVolume)
            if vol:
                yield vol
        finally:
            _release(ctrl)


def session_pids() -> dict[int, int]:
    """诊断用:当前所有渲染设备上的音频会话,返回 {pid: 会话数}。"""
    counts: dict[int, int] = {}
    try:
        for pid, ctrl in _iter_session_controls():
            try:
                if pid:
                    counts[pid] = counts.get(pid, 0) + 1
            finally:
                _release(ctrl)
    except Exception:
        pass
    return counts


def _pids_with_descendants(pid: int) -> list[int]:
    """目标 pid + 其所有子孙进程。

    Chromium 系浏览器(Edge/Chrome)的音频在 audio service 子进程里放,
    只按主窗口 pid 找会话什么都静不了(用户实测:浏览器静不了、游戏能静,
    因为游戏是主进程直接放音频)。快照失败时退化为单进程。
    """
    from . import win32
    pids = [pid]
    try:
        pids.extend(sorted(win32.process_descendants(pid)))
    except Exception:
        pass
    return pids


def session_pids() -> dict[int, int]:
    """诊断用:当前所有渲染设备上的音频会话,返回 {pid: 会话数}。"""
    counts: dict[int, int] = {}
    try:
        # 复用 _iter_session_volumes 的枚举链路:对每个已枚举到的会话
        # 记录 pid(不过滤目标进程)
        _ensure_com_initialized()
        enumerator = _co_create_instance(CLSID_MMDeviceEnumerator, IID_IMMDeviceEnumerator)
        if not enumerator:
            return counts
        try:
            collection = ctypes.c_void_p()
            fn = _com_method(enumerator, IDX_ENUM_AUDIO_ENDPOINTS, ctypes.c_long,
                             ctypes.c_int, wintypes.DWORD, ctypes.c_void_p)
            hr = fn(enumerator, EDATA_FLOW_RENDER, DEVICE_STATE_ACTIVE, ctypes.byref(collection))
            if hr != 0 or not collection.value:
                return counts
            try:
                count = wintypes.UINT(0)
                fn = _com_method(collection, IDX_GET_COUNT, ctypes.c_long, ctypes.c_void_p)
                fn(collection, ctypes.byref(count))
                for i in range(count.value):
                    device = ctypes.c_void_p()
                    fn = _com_method(collection, IDX_ITEM, ctypes.c_long, ctypes.c_uint, ctypes.c_void_p)
                    hr = fn(collection, i, ctypes.byref(device))
                    if hr != 0 or not device.value:
                        continue
                    mgr = ctypes.c_void_p()
                    fn = _com_method(device, IDX_ACTIVATE, ctypes.c_long,
                                     ctypes.POINTER(GUID), wintypes.DWORD,
                                     ctypes.c_void_p, ctypes.c_void_p)
                    hr = fn(device, ctypes.byref(IID_IAudioSessionManager2),
                            CLSCTX_ALL, None, ctypes.byref(mgr))
                    _release(device)
                    if hr != 0 or not mgr.value:
                        continue
                    try:
                        sessions = ctypes.c_void_p()
                        fn = _com_method(mgr, IDX_GET_SESSION_ENUMERATOR, ctypes.c_long, ctypes.c_void_p)
                        hr = fn(mgr, ctypes.byref(sessions))
                        if hr != 0 or not sessions.value:
                            continue
                        try:
                            scount = wintypes.UINT(0)
                            fn = _com_method(sessions, IDX_GET_COUNT, ctypes.c_long, ctypes.c_void_p)
                            fn(sessions, ctypes.byref(scount))
                            for j in range(scount.value):
                                ctrl = ctypes.c_void_p()
                                fn = _com_method(sessions, IDX_GET_SESSION, ctypes.c_long,
                                                 ctypes.c_int, ctypes.c_void_p)
                                hr = fn(sessions, j, ctypes.byref(ctrl))
                                if hr != 0 or not ctrl.value:
                                    continue
                                try:
                                    ctrl2 = _query_interface(ctrl, IID_IAudioSessionControl2)
                                    if not ctrl2:
                                        continue
                                    try:
                                        pid = wintypes.DWORD(0)
                                        fn = _com_method(ctrl2, IDX_GET_PROCESS_ID,
                                                         ctypes.c_long, ctypes.c_void_p)
                                        fn(ctrl2, ctypes.byref(pid))
                                        p = int(pid.value)
                                        if p:
                                            counts[p] = counts.get(p, 0) + 1
                                    finally:
                                        _release(ctrl2)
                                finally:
                                    _release(ctrl)
                        finally:
                            _release(sessions)
                    finally:
                        _release(mgr)
            finally:
                _release(collection)
        finally:
            _release(enumerator)
    except Exception:
        pass
    return counts


def set_process_mute(pid: int, mute: bool) -> int:
    """静音/取消静音目标进程(含子孙进程)。返回被操作的音频会话数量。"""
    n = 0
    try:
        for p in _pids_with_descendants(pid):
            for vol in _iter_session_volumes(p):
                try:
                    fn = _com_method(vol, IDX_SET_MUTE, ctypes.c_long,
                                     wintypes.BOOL, ctypes.c_void_p)
                    fn(vol, bool(mute), None)
                    n += 1
                finally:
                    _release(vol)
    except Exception:
        pass
    return n


def is_process_muted(pid: int) -> bool | None:
    """查询目标进程(含子孙进程)是否所有音频会话都处于静音状态。

    0 个会话返回 None;任一会话未静音返回 False。
    """
    try:
        total = 0
        muted = 0
        for p in _pids_with_descendants(pid):
            for vol in _iter_session_volumes(p):
                try:
                    fn = _com_method(vol, IDX_GET_MUTE, ctypes.c_long, ctypes.c_void_p)
                    flag = wintypes.BOOL(0)
                    fn(vol, ctypes.byref(flag))
                    total += 1
                    if flag.value:
                        muted += 1
                finally:
                    _release(vol)
        if total == 0:
            return None
        return muted == total
    except Exception:
        return None
