"""Detection du type de disque (SSD ou disque dur) qui contient les donnees de Phoque Al."""
import os
import sys


def is_ssd(path):
    """True = SSD, False = disque dur, None = inconnu."""
    if sys.platform != "win32":
        return None
    import ctypes
    from ctypes import wintypes

    drive = os.path.splitdrive(os.path.abspath(str(path)))[0]
    if not drive:
        return None
    k32 = ctypes.windll.kernel32
    k32.CreateFileW.restype = wintypes.HANDLE
    handle = k32.CreateFileW("\\\\.\\" + drive, 0, 3, None, 3, 0, None)
    if handle in (None, wintypes.HANDLE(-1).value):
        return None

    class Query(ctypes.Structure):
        _fields_ = [("PropertyId", wintypes.DWORD), ("QueryType", wintypes.DWORD),
                    ("Extra", ctypes.c_ubyte * 1)]

    class SeekPenalty(ctypes.Structure):
        _fields_ = [("Version", wintypes.DWORD), ("Size", wintypes.DWORD),
                    ("IncursSeekPenalty", ctypes.c_ubyte)]

    query = Query(7, 0)      # StorageDeviceSeekPenaltyProperty, PropertyStandardQuery
    result = SeekPenalty()
    returned = wintypes.DWORD(0)
    try:
        ok = k32.DeviceIoControl(handle, 0x002D1400, ctypes.byref(query), ctypes.sizeof(query),
                                 ctypes.byref(result), ctypes.sizeof(result), ctypes.byref(returned), None)
    finally:
        k32.CloseHandle(handle)
    if not ok:
        return None
    return not bool(result.IncursSeekPenalty)
