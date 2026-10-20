# -*- coding: utf-8 -*-
"""指针引擎离线自测: 直接 import GameModder 的引擎层, 对真实进程跑一遍。
用法: python test_ptr.py <进程名> [数值]
"""
import sys
import time
import struct

import GameModder as G


def main():
    name = sys.argv[1] if len(sys.argv) > 1 else "Roadside Research.exe"
    pid = G.MemoryScanner.find_pid(name)
    if not pid:
        print("未找到进程 %r, 正在运行的候选:" % name)
        for p, e in G.MemoryScanner.find_processes():
            if p and "Game" in e or "Roadside" in e:
                print("  ", p, e)
        return 1
    sc = G.MemoryScanner()
    sc.attach(pid, name)
    print("附加 %s (PID %d), 可读 %.0f MB" %
          (name, pid, sc.total_bytes / 1024 / 1024))

    t0 = time.time()
    mods = sc.list_modules()
    print("\n[模块] %d 个 (%.2fs)" % (len(mods), time.time() - t0))
    for m in mods[:8]:
        print("   %-30s base=0x%X size=0x%X" % m)
    if len(mods) > 8:
        print("   ... 其余 %d 个" % (len(mods) - 8))

    # 模块归属测试: 自己进程的模块基址必须命中第一项
    if mods:
        nm, base, size = mods[0]
        got = sc.module_of(base + 0x100)
        assert got and got[0] == nm, "module_of 自检失败: %r" % (got,)
        print("[自检] module_of(%s+0x100) = %r  OK" % (nm, got))
        print("[自检] fmt_static = %s" % sc.fmt_static(base + 0x1234))

    # 找一个小整数做目标
    val = int(sys.argv[2]) if len(sys.argv) > 2 else 1
    vtype = "int32"
    t0 = time.time()
    hits = sc.scan_exact(val, vtype, full=True)
    print("\n[扫描] %s=%d 命中 %d 个 (%.2fs)" % (vtype, val, len(hits), time.time() - t0))
    if not hits:
        print("跳过指针测试 (无命中)")
        return 0
    target = hits[0][0]
    print("取第一个命中 0x%X" % target)

    t0 = time.time()
    ptrs = sc.find_pointers_to(target, progress_cb=None)
    print("\n[反向指针] 指向 0x%X 的槽位 %d 个 (%.2fs)" % (target, len(ptrs), time.time() - t0))
    for slot, v in ptrs[:6]:
        print("   槽 0x%X (=%s) -> 0x%X" % (slot, sc.fmt_static(slot), v))

    t0 = time.time()
    al = sc.find_aliases(target, vtype)
    print("\n[镜像别名] 附近指向同值的指针字段 %d 个 (%.2fs)" % (len(al), time.time() - t0))
    for a in al[:6]:
        print("   字段 0x%X -> 目标 0x%X (+0x%X)" % (a["field"], a["target"], a["hit_offset"]))

    if ptrs:
        t0 = time.time()
        chains = sc.pointer_scan(target, max_level=2, per_level=20)
        print("\n[指针链] 2 层回溯得到 %d 条静态路径 (%.2fs)" % (len(chains), time.time() - t0))
        for c in chains[:6]:
            addr, err = sc.resolve_chain(c["module"], c["base_offset"], c["offsets"])
            print("   %s+0x%X 链%s -> %s" %
                  (c["module"], c["base_offset"],
                   [hex(o) for o in c["offsets"]],
                   ("0x%X" % addr) if addr else ("失败: %s" % err)))

    sc.detach()
    print("\n全部自测通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
