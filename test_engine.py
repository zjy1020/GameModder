# -*- coding: utf-8 -*-
"""指针引擎自测: 起一个内存夹具进程, 从外部用 GameModder 引擎逐个验证
find_pointers_to / find_aliases / probe_survive / list_modules / resolve_chain。

用法: python test_engine.py
退出码 0 = 全部通过。
"""
import json
import os
import struct
import subprocess
import sys
import time

import GameModder as G

HERE = os.path.dirname(os.path.abspath(__file__))
FIXTURE = os.path.join(HERE, "fixture.json")

fails = []


def check(name, cond, detail=""):
    if cond:
        print("  OK   %s%s" % (name, ("  " + detail) if detail else ""))
    else:
        print("  FAIL %s%s" % (name, ("  " + detail) if detail else ""))
        fails.append(name)


def start_fixture():
    if os.path.exists(FIXTURE):
        os.remove(FIXTURE)
    py = sys.executable
    p = subprocess.Popen([py, os.path.join(HERE, "ptr_fixture.py"), "240"],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(100):
        if os.path.exists(FIXTURE):
            time.sleep(0.3)
            with open(FIXTURE, "r", encoding="utf-8") as f:
                return p, json.load(f)
        time.sleep(0.1)
    p.kill()
    raise RuntimeError("夹具进程未写出 fixture.json")


def main():
    proc, info = start_fixture()
    try:
        print("夹具 PID %d, 目标值 %d @ 0x%X" %
              (info["pid"], info["value"], info["target"]))
        sc = G.MemoryScanner()
        sc.attach(info["pid"], "python.exe")
        print("附加成功, 可读 %.1f MB, 区域 %d 个\n" %
              (sc.total_bytes / 1024 / 1024, len(sc.regions)))

        # ---- 1. 模块枚举 ----
        print("[1] 模块枚举")
        mods = sc.list_modules()
        check("模块列表非空", len(mods) > 0, "%d 个模块" % len(mods))
        names = [m[0].lower() for m in mods]
        check("含 python DLL", any("python" in n for n in names),
              ", ".join(m[0] for m in mods[:4]))
        mods_sorted = all(mods[i][1] <= mods[i + 1][1] for i in range(len(mods) - 1))
        check("模块按基址升序", mods_sorted)

        # ---- 2. 模块归属 ----
        print("\n[2] 模块归属 module_of / fmt_static")
        nm, base, size = mods[0]
        got = sc.module_of(base + 0x50)
        check("module_of 命中首模块", got is not None and got[0] == nm, repr(got))
        check("module_of 偏移正确", got is not None and got[2] == 0x50)
        check("module_of 越界返回 None", sc.module_of(0x10) is None)
        fs = sc.fmt_static(base + 0x1234)
        check("fmt_static 输出 模块+偏移", "+0x1234" in fs, fs)
        check("fmt_static 非模块地址用裸十六进制",
              sc.fmt_static(0x10) == "0x10", sc.fmt_static(0x10))

        # ---- 3. 读写基线 ----
        print("\n[3] 读写基线")
        v = sc.read_value(info["target"], "int32")
        check("读目标值", v == info["value"], "读到 %r" % v)
        check("读镜像值相同", sc.read_value(info["mirror"], "int32") == info["value"])

        # ---- 4. 反向指针 ----
        print("\n[4] find_pointers_to (反向指针扫描)")
        t0 = time.time()
        ptrs = sc.find_pointers_to(info["buf"])
        dt = time.time() - t0
        slots = [s for s, _ in ptrs]
        check("命中 holder 里的指针字段", info["ptr_field"] in slots,
              "共 %d 个槽, %.2fs" % (len(ptrs), dt))
        check("所有槽 8 字节对齐", all(s % 8 == 0 for s in slots))
        check("指向值都等于 buf", all(x == info["buf"] for _, x in ptrs))

        # 带偏移范围的反向扫描
        ptrs2 = sc.find_pointers_to(info["buf"], max_off=0x80)
        check("max_off 分支也能命中", info["ptr_field"] in [s for s, _ in ptrs2],
              "%d 个" % len(ptrs2))

        # ---- 5. 镜像别名 ----
        print("\n[5] find_aliases (镜像/别名透视)")
        al = sc.find_aliases(info["mirror"], "int32", span=0x200)
        tgts = [a["target"] for a in al]
        check("从镜像字段反查到 buf", info["buf"] in tgts or
              (info["buf"] + 0) in [a["target"] for a in al],
              "%d 个别名: %s" % (len(al), [hex(t) for t in tgts]))
        hit_off_ok = any(a["hit_offset"] == 0x40 for a in al
                         if a["target"] == info["buf"])
        check("别名命中偏移 = 0x40", hit_off_ok,
              repr([a["hit_offset"] for a in al]))
        # 真值本身附近没有指向自己的指针 -> 应为空
        al2 = sc.find_aliases(info["target"], "int32", span=0x100)
        check("真值地址不再反查出自己", len(al2) == 0, "%d 个" % len(al2))

        # ---- 6. 存活探测 (真身 vs 镜像) ----
        print("\n[6] probe_survive (真身/镜像判定)")
        # 夹具进程不刷值, 两个地址写完都会保持 -> 都判 live (证明探测逻辑本身正确)
        rows = sc.probe_survive([info["target"], info["mirror"]], "int32", 77777)
        states = [r["state"] for r in rows]
        check("两个地址都判 live (夹具不刷值)", states == ["live", "live"], repr(states))
        check("probe 记录 before/after",
              all("before" in r and "after" in r for r in rows))
        # 失效地址必须判 dead
        rows2 = sc.probe_survive([0x10], "int32", 1)
        check("非法地址判 dead", rows2[0]["state"] == "dead", repr(rows2[0]))
        # 复位的值
        sc.write_value(info["target"], "int32", info["value"])
        sc.write_value(info["mirror"], "int32", info["value"])

        # ---- 7. 指针链 ----
        print("\n[7] pointer_scan + resolve_chain")
        t0 = time.time()
        chains = sc.pointer_scan(info["buf"], max_level=2, per_level=10)
        dt = time.time() - t0
        check("回溯得到结果", len(chains) > 0, "%d 条, %.2fs" % (len(chains), dt))
        statics = [c for c in chains if c["static"]]
        print("       static=%d 非static=%d" % (len(statics), len(chains) - len(statics)))
        ok_all = True
        for c in chains[:10]:
            addr, err = sc.resolve_chain(c["module"], c["base_offset"], c["offsets"])
            if not err:
                # 解析出的地址必须能读回 buf (末层不再解引用)
                if addr != info["buf"]:
                    ok_all = False
                    print("       FAIL 解析地址不符: %s != 0x%X" % (hex(addr), info["buf"]))
        check("resolve_chain 解析结果一致 (末层=目标地址)", ok_all)
        if statics:
            c = statics[0]
            check("static 链的模块名非空", bool(c["module"]), c["module"])
            check("static 链落在模块内", c["base_offset"] < 0x8000000,
                  "%s+0x%X offsets=%s" % (c["module"], c["base_offset"],
                                          [hex(o) for o in c["offsets"]]))
        # 错误的模块名必须报错而不是崩
        bad, err = sc.resolve_chain("NoSuchModule.dll", 0x10, [0])
        check("未知模块返回错误", bad is None and err is not None, repr(err))
        # 空指针槽: kernel32 基址处不可能是指向对象的值, 但更容易的是用一个可读的零区
        zero_slot = 0
        for base_, rsize_ in sc.regions:
            blk = sc.read(base_, min(0x1000, rsize_))
            if blk is not None and blk.strip(b"\x00") == b"":
                zero_slot = base_ + 0x100
                break
        if zero_slot:
            bad2, err2 = sc.resolve_chain("", zero_slot, [0])
            check("空指针槽返回错误", bad2 is None and err2 is not None, repr(err2))

        sc.detach()
    finally:
        proc.kill()

    print("\n" + "=" * 54)
    if fails:
        print("失败 %d 项: %s" % (len(fails), ", ".join(fails)))
        return 1
    print("全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
