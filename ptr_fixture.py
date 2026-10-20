# -*- coding: utf-8 -*-
"""指针引擎测试夹具: 分配已知内容的内存并一直持有, 供 test_ptr.py 从外部扫描。
DLL 侧无静态符号可用, 因此把地址写入 fixture.json 供测试脚本读取。
用法: python ptr_fixture.py [秒数]
"""
import ctypes
import json
import os
import struct
import sys
import time

VALUE = 424242
VAL_OFF_BUF = 0x40        # 真值在 buf 内的偏移
PTR_FIELD_IN_HOLDER = 0x18  # holder 里指向 buf 的指针字段
MIRROR_FIELD_IN_HOLDER = 0x28  # holder 里的镜像副本 (值相同, 不是真身)

# 数据块: 前 0x40 噪声, +0x40 处放目标值
buf = ctypes.create_string_buffer(0x100)
ctypes.memset(ctypes.addressof(buf), 0, 0x100)
for i in range(0, VAL_OFF_BUF, 4):
    struct.pack_into("<i", buf, i, 1000 + i)
struct.pack_into("<i", buf, VAL_OFF_BUF, VALUE)

# 结构体: 一个指向 buf 的指针字段 + 一个镜像值字段
holder = ctypes.create_string_buffer(0x80)
ctypes.memset(ctypes.addressof(holder), 0, 0x80)
struct.pack_into("<Q", holder, PTR_FIELD_IN_HOLDER, ctypes.addressof(buf))
struct.pack_into("<i", holder, MIRROR_FIELD_IN_HOLDER, VALUE)

info = {
    "pid": os.getpid(),
    "buf": ctypes.addressof(buf),
    "holder": ctypes.addressof(holder),
    "value": VALUE,
    "target": ctypes.addressof(buf) + VAL_OFF_BUF,
    "ptr_field": ctypes.addressof(holder) + PTR_FIELD_IN_HOLDER,
    "mirror": ctypes.addressof(holder) + MIRROR_FIELD_IN_HOLDER,
}
with open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                       "fixture.json"), "w", encoding="utf-8") as f:
    json.dump(info, f, indent=2)

for k, v in info.items():
    print("%s=%s" % (k, hex(v) if isinstance(v, int) and k != "pid" and k != "value" else v))
sys.stdout.flush()

secs = float(sys.argv[1]) if len(sys.argv) > 1 else 120
time.sleep(secs)
