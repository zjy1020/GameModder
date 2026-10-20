# -*- coding: utf-8 -*-
"""GUI 冒烟测试: 离线构建全部页面, 验证控件树与新增的指针/穿透页无异常。
不附加任何进程, 不弹模态框。用法: python test_gui.py
"""
import sys
import tkinter as tk

import GameModder as G

fails = []


def check(name, cond, detail=""):
    if cond:
        print("  OK   %s%s" % (name, ("  " + detail) if detail else ""))
    else:
        print("  FAIL %s%s" % (name, ("  " + detail) if detail else ""))
        fails.append(name)


def main():
    root = tk.Tk()
    root.withdraw()
    # 关掉自动附加/进程选择弹窗: 让 cfg 指向一个不存在的进程, 但重写 after 避免弹框
    G.ProcessPicker = lambda *a, **k: None
    app = G.GameModderGUI(root)
    root.update_idletasks()

    print("[1] 页面构建")
    tabs = app.nb.tabs()
    check("四个页签", len(tabs) == 4, str([app.nb.tab(t, "text") for t in tabs]))
    labels = [app.nb.tab(t, "text").strip() for t in tabs]
    check("含 指针/穿透 页", "指针/穿透" in labels, str(labels))
    check("含 数值扫描 页", "数值扫描" in labels)
    check("含 游戏模板 页", "游戏模板" in labels)
    check("含 手动读写 页", "手动读写" in labels)

    print("\n[2] 指针页控件齐全")
    for attr in ("ptr_addr", "ptr_type", "ptr_probe", "ptr_newval", "ptr_level",
                 "alias_tree", "chain_tree"):
        check("控件 %s" % attr, hasattr(app, attr))
    check("别名表有 3 列", len(app.alias_tree["columns"]) == 3)
    check("链表有 3 列", len(app.chain_tree["columns"]) == 3)
    check("类型下拉 4 项", len(app.ptr_type["values"]) == 4)
    check("层级下拉 4 项", len(app.ptr_level["values"]) == 4)
    check("探针值有默认", app.ptr_probe.get() != "")

    print("\n[3] 右键菜单含追查真值")
    labels = []
    m = app.tree_menu
    for i in range(m.index("end") + 1):
        try:
            labels.append(m.entrycget(i, "label"))
        except tk.TclError:
            labels.append("<sep>")
    check("菜单含 追查真值", any("追查真值" in x for x in labels), str(labels))

    print("\n[4] 指针页动作在未附加进程时不崩")
    app.ptr_addr.delete(0, "end")
    app.ptr_addr.insert(0, "0x1234")
    try:
        app.on_find_alias()
        app.on_probe()
        app.on_ptr_write()
        app.on_scan_chain()
        app.on_save_chain()
        ok = True
    except Exception as e:
        ok = False
        print("       异常: %r" % (e,))
    check("未附加时动作安全返回", ok)

    print("\n[5] 地址解析边界")
    check("非法地址返回 None", app._ptr_addr_val() is None if
          (app.ptr_addr.delete(0, "end"), app.ptr_addr.insert(0, "zz")) else False)
    app.ptr_addr.delete(0, "end")
    app.ptr_addr.insert(0, "1A2B")
    check("无 0x 前缀也能解析", app._ptr_addr_val() == 0x1A2B, hex(app._ptr_addr_val() or 0))
    app.ptr_addr.delete(0, "end")
    app.ptr_addr.insert(0, "0x1A2B")
    check("带 0x 前缀解析", app._ptr_addr_val() == 0x1A2B)

    print("\n[6] 指针链存档读写")
    rows = [{"name": "test|a.dll+0x10", "process": "x.exe", "module": "a.dll",
             "base_offset": 0x10, "offsets": [0x20, 0x30], "vtype": "int32"}]
    G.save_chains(rows)
    got = G.load_chains()
    check("落盘后能读回", len(got) == 1 and got[0]["module"] == "a.dll", repr(got))
    check("偏移链保真", got and got[0]["offsets"] == [0x20, 0x30])
    G.save_chains([])

    print("\n[7] ChainPicker 能构建")
    picked = []
    try:
        G.ChainPicker(root, ["a (int32)", "b (double)"], picked.append)
        ok = True
    except Exception as e:
        ok = False
        print("       异常: %r" % (e,))
    check("ChainPicker 构建", ok)

    print("\n[8] 候选搜索框支持显示精度不匹配")
    # 内存里是真值 422.72369384765625, 用户只在搜索框里输 422.72 —— 旧版搜不到, 现在靠容差命中
    app.scanner.scan_vtype = "double"
    app.scanner.candidates = [
        (0x1000, G.pack_value("double", 422.72369384765625)),
        (0x2000, G.pack_value("double", 999.0)),
    ]
    app.addr_filter_var.set("")
    app.val_filter_var.set("422.72")
    app._render_candidates()
    rows = app.tree.get_children()
    check("422.72 能搜到 422.72369...", len(rows) == 1,
          str([app.tree.item(r, "values") for r in rows]))
    app.val_filter_var.set("999")
    app._render_candidates()
    check("整数值照常命中", len(app.tree.get_children()) == 1)
    app.val_filter_var.set("422.9")
    app._render_candidates()
    check("差太远不误命中", len(app.tree.get_children()) == 0)
    app.val_filter_var.set("")
    app.scanner.candidates = []
    app._render_candidates()

    root.destroy()
    print("\n" + "=" * 54)
    if fails:
        print("失败 %d 项: %s" % (len(fails), ", ".join(fails)))
        return 1
    print("全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
