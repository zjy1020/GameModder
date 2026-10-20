# -*- coding: utf-8 -*-
import struct
import GameModder as G

fails = []
def check(name, cond, detail=""):
    print(("  OK   " if cond else "  FAIL ") + name + (("  " + detail) if detail else ""))
    if not cond:
        fails.append(name)

print("[1] 显示精度: 最短可往返, 不再截 6 位")
d = G.pack_value("double", 422.72369384765625)
check("double 全精度显示", G.fmt_value("double", d) == "422.72369384765625", G.fmt_value("double", d))
f = G.pack_value("float", 422.72369384765625)
check("float 7 位有效数字", G.fmt_value("float", f) == "422.7237", G.fmt_value("float", f))
check("double 3.141592653589793", G.fmt_value("double", G.pack_value("double", 3.141592653589793)) == "3.141592653589793")
check("整数值仍显示 .0", G.fmt_value("double", G.pack_value("double", 1234567.0)) == "1234567.0")
check("小数不再退化成科学计数", G.fmt_value("double", G.pack_value("double", 1234567.891)) == "1234567.891",
      G.fmt_value("double", G.pack_value("double", 1234567.891)))
check("小数值完整", G.fmt_value("double", G.pack_value("double", 0.000123456789)) == "0.000123456789",
      G.fmt_value("double", G.pack_value("double", 0.000123456789)))

print("\n[2] 显示值可直接回填精确扫描 (round-trip)")
for vtype in ("double", "float"):
    for v in (422.72369384765625, 0.1, 3.141592653589793, 1234567.891, 1e20, -6.25e-7):
        shown = G.fmt_value(vtype, G.pack_value(vtype, v))
        back = G.pack_value(vtype, G.parse_number(shown))
        check("%s 显示 %-22s 回填即命中" % (vtype, shown), back == G.pack_value(vtype, v))

print("\n[3] 小数位数与半格容差")
for txt, want in (("0", 0), ("100", 0), ("4.2", 1), ("422.72", 2), ("422.7237", 4),
                  ("1.5K", 1), ("2.3M", 1), ("1.23456789", 8),
                  ("1.2e-3", 4), ("1.2e3", 0), ("5e-7", 7)):
    got = G.decimals_of(txt)
    check("decimals_of(%r)=%d" % (txt, want), got == want, "got %d" % got)
check("half_step(2)=0.005", abs(G.half_step(2) - 0.005) < 1e-18, repr(G.half_step(2)))
check("half_step(4)=5e-05", abs(G.half_step(4) - 5e-05) < 1e-18, repr(G.half_step(4)))
check("half_step(0)=0", G.half_step(0) == 0.0)

print("\n[4] 容差覆盖: 界面 422.724 -> 真值落在半格区间内")
v = 422.72369384765625
for txt in ("422.7", "422.72", "422.724"):
    tol = G.half_step(G.decimals_of(txt))
    check("%s 半格 %.4g 覆盖真值" % (txt, tol), float(txt) - tol <= v <= float(txt) + tol)

print("\n[5] 整数类型四舍五入 (不再截断)")
class FakeApp:
    parse_val = G.GameModderGUI.parse_val
a = FakeApp()
check("3.9 -> 4", a.parse_val("3.9", "int32") == 4, str(a.parse_val("3.9", "int32")))
check("-3.9 -> -4", a.parse_val("-3.9", "int64") == -4, str(a.parse_val("-3.9", "int64")))
check("2.5 -> 2 (banker)", a.parse_val("2.5", "int32") in (2, 3), str(a.parse_val("2.5", "int32")))
check("double 保留小数", a.parse_val("3.9", "double") == 3.9)

print("\n[6] float32 溢出给出清晰提示")
try:
    G.pack_value("float", 1e39)
    check("1e39 float 应报错", False)
except ValueError as e:
    check("1e39 float 报 ValueError", "上限" in str(e), str(e))
check("double 1e39 正常", G.pack_value("double", 1e39) != b"")
check("float 3e38 正常", G.pack_value("float", 3e38) != b"")

print("\n[7] fmt_abbrev 不再截断小数")
check("1234.5678 -> repr", G.fmt_abbrev(1234.5678) == "1.2K", G.fmt_abbrev(1234.5678))
check("422.72369384765625 保留", G.fmt_abbrev(422.72369384765625) == "422.72369384765625",
      G.fmt_abbrev(422.72369384765625))

print("\n[8] filter_equal 容差模式")
class Stub(G.MemoryScanner):
    def __init__(self, val):
        G.MemoryScanner.__init__(self)
        self._v = val
        self.candidates = [(0x1000, b"")]
        self.scan_vtype = "double"
    def read(self, addr, size):
        return G.pack_value("double", self._v)
s = Stub(422.72369384765625)
check("容差 0.005 命中", len(s.filter_equal(422.724, "double", 0.005)) == 1)
check("容差 0 不命中", len(s.filter_equal(422.724, "double", 0.0)) == 0)
s2 = Stub(422.72)
check("容差 0.005 命中 422.72", len(s2.filter_equal(422.72, "double", 0.005)) == 1)
s3 = Stub(float("nan"))
check("nan 永不命中", len(s3.filter_equal(float("nan"), "double", 1.0)) == 0)

print("\n" + "=" * 54)
if fails:
    print("失败 %d 项: %s" % (len(fails), ", ".join(fails)))
    raise SystemExit(1)
print("全部通过")

