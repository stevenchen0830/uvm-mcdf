#!/usr/bin/env python3
"""Coverage report for the uvm-mcdf Verilator flow.

Reads Verilator coverage databases (the *.dat files written by a model that was built
with --coverage) and writes two reports into the output directory:

    coverage_report.txt    plain-text summary (also printed on the terminal)
    coverage_report.html   self-contained HTML report, open it in any browser

Usage:
    cov_report.py --out DIR --merged MERGED.dat [--test NAME=FILE.dat ...]

Only the Python standard library is used.
"""
import argparse
import collections
import datetime
import html
import os
import sys

CODE_TYPES = ["line", "branch", "expr", "toggle", "fsm_state", "fsm_arc"]
TYPE_ZH = {
    "line": "行 line",
    "branch": "分支 branch",
    "expr": "表达式 expr",
    "toggle": "翻转 toggle",
    "fsm_state": "状态机状态 fsm_state",
    "fsm_arc": "状态机跳转 fsm_arc",
}


# ----------------------------------------------------------------------------- parsing
def parse_dat(path):
    """Return the coverage points of a Verilator coverage file as a list of dicts.

    A record looks like   C '<SOH>key<STX>value<SOH>key<STX>value...' <count>
    """
    points = []
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        for raw in f:
            if not raw.startswith("C '"):
                continue
            body, sep, count = raw.rstrip("\n").rpartition("' ")
            if not sep:
                continue
            rec = {}
            for kv in body[3:].split("\x01"):
                if "\x02" in kv:
                    key, value = kv.split("\x02", 1)
                    rec[key] = value
            try:
                rec["count"] = int(count)
            except ValueError:
                continue
            points.append(rec)
    return points


def pct(covered, total):
    return 100.0 * covered / total if total else None


def fmt_pct(value):
    return "  n/a " if value is None else "%5.1f%%" % value


# ----------------------------------------------------------------------- code coverage
def code_stats(points):
    """{file: {type: [covered, total]}} plus the same for all files under the key None."""
    stats = collections.defaultdict(lambda: collections.defaultdict(lambda: [0, 0]))
    for p in points:
        t = p.get("t")
        if t not in CODE_TYPES:
            continue
        for key in (None, p.get("f", "?")):
            cell = stats[key][t]
            cell[1] += 1
            if p["count"] > 0:
                cell[0] += 1
    return stats


def span_lines(point):
    """Source lines a line-coverage point stands for (the S field, e.g. '112-114,120')."""
    lines = set()
    for part in (point.get("S") or point.get("l", "")).split(","):
        part = part.strip()
        if not part:
            continue
        lo, _, hi = part.partition("-")
        try:
            lo_i = int(lo)
            hi_i = int(hi) if hi else lo_i
        except ValueError:
            continue
        lines.update(range(lo_i, hi_i + 1))
    return lines


# ----------------------------------------------------------------- functional coverage
def func_model(points):
    """Ordered {covergroup: {"name", "weight", "items": {item: {"cross", "weight", "bins": [...]}}}}.

    The weights and thresholds come from the database itself, the same numbers
    verilator_coverage uses: w = weight of the coverpoint / cross (0 for the coverpoints that
    only feed a cross), Gw = weight of the covergroup, s = option.at_least of the bin.
    """
    model = collections.OrderedDict()
    for p in points:
        if p.get("t") != "covergroup":
            continue
        full = p.get("page", "").split("/", 1)[-1]          # e.g. $unit::coverage_mcdf::cg_x
        name = full.split("::")[-1]
        hier = p.get("h", "")
        rest = hier[len(full) + 1:] if hier.startswith(full + ".") else hier
        item, _, bin_name = rest.partition(".")
        group = model.setdefault(full, {"name": name, "file": p.get("f", ""), "weight": 0,
                                        "items": collections.OrderedDict()})
        group["weight"] = max(group["weight"], int(p.get("Gw") or 1))    # type_option.weight of the covergroup
        entry = group["items"].setdefault(item, {"cross": p.get("C") == "1", "weight": 0, "bins": []})
        entry["weight"] = max(entry["weight"], int(p.get("w") or 1))     # weight of the coverpoint / cross
        entry["bins"].append({
            "name": bin_name or p.get("B", "?"),
            "count": p["count"],
            "at_least": int(p.get("s") or 1),                             # option.at_least
            "kind": p.get("Bt", ""),                      # "illegal" / "ignore" / ""
            "line": p.get("l", ""),
        })

    def first_line(bins):
        nums = [int(b["line"]) for b in bins if b["line"].isdigit()]
        return min(nums) if nums else 0

    # show covergroups, their coverpoints/crosses and the bins in source order
    ordered = collections.OrderedDict()
    for name in sorted(model, key=lambda n: min(first_line(i["bins"]) for i in model[n]["items"].values())):
        group = model[name]
        items = collections.OrderedDict()
        for item in sorted(group["items"], key=lambda k: first_line(group["items"][k]["bins"])):
            items[item] = group["items"][item]
        group["items"] = items
        ordered[name] = group
    return ordered


def func_scores(model):
    """Add hit/total/coverage to every item and a score to every covergroup; return overall."""
    scores = []
    for cg_name, group in model.items():
        num = den = 0.0
        for item_name, item in group["items"].items():
            normal = [b for b in item["bins"] if not b["kind"]]
            item["hit"] = sum(1 for b in normal if b["count"] >= b["at_least"])
            item["total"] = len(normal)
            item["cov"] = pct(item["hit"], item["total"])
            if item["weight"] and item["cov"] is not None:
                num += item["weight"] * item["cov"]
                den += item["weight"]
        group["cov"] = num / den if den else None
        group["hit"] = sum(i["hit"] for i in group["items"].values() if i["weight"])
        group["total"] = sum(i["total"] for i in group["items"].values() if i["weight"])
        if group["cov"] is not None and group["weight"]:
            scores.append((group["weight"], group["cov"]))
    total = sum(w for w, _ in scores)
    return sum(w * c for w, c in scores) / total if total else None


def analyse(path):
    points = parse_dat(path)
    model = func_model(points)
    overall = func_scores(model)
    return {"points": points, "code": code_stats(points), "func": model, "func_overall": overall}


# ------------------------------------------------------------------------- text report
def text_report(merged, tests, merged_path):
    out = []
    w = out.append
    w("=" * 78)
    w("uvm-mcdf coverage report (Verilator)      %s" % datetime.datetime.now().strftime("%Y-%m-%d %H:%M"))
    w("merged database: %s   tests: %s" % (merged_path, ", ".join(n for n, _ in tests) or "-"))
    w("=" * 78)
    types = [t for t in CODE_TYPES if merged["code"][None][t][1]]

    w("")
    w("[1] Code coverage of the DUT (merged)")
    w("    %-22s %s" % ("file", "".join("%-22s" % t for t in types)))
    for key in [None] + sorted(k for k in merged["code"] if k):
        row = merged["code"][key]
        cells = "".join("%-22s" % ("%s (%d/%d)" % (fmt_pct(pct(*row[t])), row[t][0], row[t][1])) for t in types)
        w("    %-22s %s" % ("TOTAL" if key is None else os.path.basename(key), cells))
    w("    (instances of the same module are counted together: slave_fifo.v is the union of the three channel FIFOs)")

    w("")
    w("[2] Functional coverage (covergroups, merged)      overall: %s" % fmt_pct(merged["func_overall"]))
    for cg_name, group in merged["func"].items():
        w("    %-32s %s  (%d/%d bins)" % (group["name"], fmt_pct(group["cov"]), group["hit"], group["total"]))
        for item_name, item in group["items"].items():
            tag = "cross" if item["cross"] else "point"
            note = "  [weight 0: not counted]" if not item["weight"] else ""
            w("        %-5s %-22s %s  (%d/%d)%s" % (tag, item_name, fmt_pct(item["cov"]), item["hit"], item["total"], note))
            missed = [b["name"] for b in item["bins"] if not b["kind"] and b["count"] < b["at_least"]]
            if missed and item["weight"]:
                w("              not hit: %s" % ", ".join(missed))
            for b in item["bins"]:
                if b["kind"] == "illegal" and b["count"] > 0:
                    w("              ILLEGAL BIN HIT: %s x%d" % (b["name"], b["count"]))

    if tests:
        w("")
        w("[3] Per test")
        w("    %-10s %s%-12s" % ("test", "".join("%-10s" % t for t in types), "functional"))
        for name, data in tests + [("MERGED", merged)]:
            row = data["code"][None]
            w("    %-10s %s%-12s" % (name, "".join("%-10s" % fmt_pct(pct(*row[t])) for t in types), fmt_pct(data["func_overall"])))

    w("")
    w("[4] Uncovered line / branch / expression points of the DUT")
    missing = [p for p in merged["points"] if p.get("t") in ("line", "branch", "expr") and p["count"] == 0]
    missing.sort(key=lambda p: (p.get("f", ""), int(p.get("l", "0") or 0), p.get("t", "")))
    for p in missing:
        w("    %-16s line %-4s %-6s %s" % (os.path.basename(p.get("f", "?")), p.get("l", "?"), p["t"], p.get("o", "")))
    if not missing:
        w("    (none)")
    w("")
    return "\n".join(out)


# ------------------------------------------------------------------------- HTML report
CSS = """
:root{--bg:#fafaf8;--fg:#1f2328;--muted:#667085;--card:#fff;--line:#e4e4df;--good:#1a7f37;--warn:#9a6700;--bad:#cf222e;
--goodbg:#e6f4ea;--warnbg:#fff4d6;--badbg:#ffe7e9;--accent:#0b5cad;--code:#f3f3ef}
@media (prefers-color-scheme:dark){:root{--bg:#15171a;--fg:#e6e6e6;--muted:#9aa4b2;--card:#1d2024;--line:#33373d;--good:#56d364;
--warn:#e3b341;--bad:#ff7b72;--goodbg:#12301c;--warnbg:#3a2d0c;--badbg:#42191c;--accent:#79b8ff;--code:#22262b}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);font:15px/1.55 system-ui,"Segoe UI","Microsoft YaHei",sans-serif}
main{max-width:1180px;margin:0 auto;padding:24px 16px 64px}h1{font-size:26px;margin:0 0 4px}h2{font-size:19px;margin:36px 0 10px}
h3{font-size:16px;margin:20px 0 6px}.sub{color:var(--muted);margin:0 0 18px}.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:12px}
.card{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:12px 14px}.card .k{color:var(--muted);font-size:13px}
.card .v{font-size:26px;font-weight:650}.card .d{color:var(--muted);font-size:12px}
.wrap{overflow-x:auto;border:1px solid var(--line);border-radius:10px;background:var(--card)}table{border-collapse:collapse;width:100%;font-size:14px}
th,td{padding:6px 10px;text-align:left;border-bottom:1px solid var(--line);white-space:nowrap}th{color:var(--muted);font-weight:600}
tr:last-child td{border-bottom:0}td.num{text-align:right;font-variant-numeric:tabular-nums}
.bar{display:inline-block;width:90px;height:8px;border-radius:4px;background:var(--line);vertical-align:middle;margin-right:8px;overflow:hidden}
.bar i{display:block;height:100%}.g{color:var(--good)}.w{color:var(--warn)}.b{color:var(--bad)}
.bg-g{background:var(--good)}.bg-w{background:var(--warn)}.bg-b{background:var(--bad)}
.pill{display:inline-block;border-radius:999px;padding:0 8px;font-size:12px;border:1px solid var(--line);color:var(--muted)}
.pill.miss{background:var(--badbg);color:var(--bad);border-color:transparent}.pill.hit{background:var(--goodbg);color:var(--good);border-color:transparent}
.pill.ill{background:var(--warnbg);color:var(--warn);border-color:transparent}
details{background:var(--card);border:1px solid var(--line);border-radius:10px;margin:8px 0;padding:0 12px}
summary{cursor:pointer;padding:9px 0;font-weight:600}details[open]{padding-bottom:12px}
.bins{display:flex;flex-wrap:wrap;gap:6px;margin:4px 0 10px}
pre.src{margin:0;font:12.5px/1.45 ui-monospace,Consolas,"Cascadia Mono",monospace;overflow-x:auto}
pre.src span.l{display:block;padding:0 8px;white-space:pre}pre.src .n{display:inline-block;width:3.5em;color:var(--muted);text-align:right;margin-right:1em;user-select:none}
pre.src .c{display:inline-block;width:6.5em;color:var(--muted);text-align:right;margin-right:1em;user-select:none}
pre.src .miss{background:var(--badbg)}pre.src .part{background:var(--warnbg)}pre.src .note{color:var(--bad);font-style:italic}
.muted{color:var(--muted)}code{background:var(--code);padding:1px 5px;border-radius:4px;font-size:13px}
a{color:var(--accent);text-decoration:none}a:hover{text-decoration:underline}
"""

# Links such as #src-arbiter.v-74 point into collapsed <details>: open them before scrolling.
SCRIPT = """
function openTarget(){var el=document.getElementById(decodeURIComponent(location.hash.slice(1)));if(!el)return;
var d=el.closest('details');if(d)d.open=true;el.scrollIntoView({block:'center'});}
window.addEventListener('hashchange',openTarget);window.addEventListener('load',openTarget);
"""


def cls(value):
    if value is None:
        return ""
    return "g" if value >= 90 else ("w" if value >= 60 else "b")


def bar(value):
    if value is None:
        return '<span class="muted">n/a</span>'
    c = cls(value)
    return '<span class="bar"><i class="bg-%s" style="width:%.0f%%"></i></span><span class="%s">%.1f%%</span>' % (c, value, c, value)


def html_report(merged, tests, src_dir):
    e = html.escape
    h = []
    w = h.append
    types = [t for t in CODE_TYPES if merged["code"][None][t][1]]
    total = merged["code"][None]
    w("<!doctype html><html lang='zh-CN'><head><meta charset='utf-8'>")
    w("<meta name='viewport' content='width=device-width,initial-scale=1'><title>MCDF 覆盖率报告</title>")
    w("<style>%s</style></head><body><main>" % CSS)
    w("<h1>MCDF 覆盖率报告</h1>")
    w("<p class='sub'>Verilator 流程 · 合并用例：%s · 生成于 %s</p>" % (
        e(", ".join(n for n, _ in tests) or "-"), datetime.datetime.now().strftime("%Y-%m-%d %H:%M")))

    w("<div class='cards'>")
    for t in types:
        v = pct(*total[t])
        w("<div class='card'><div class='k'>%s</div><div class='v %s'>%s</div><div class='d'>%d / %d</div></div>" % (
            e(TYPE_ZH[t]), cls(v), "n/a" if v is None else "%.1f%%" % v, total[t][0], total[t][1]))
    fv = merged["func_overall"]
    fh = sum(g["hit"] for g in merged["func"].values())
    ft = sum(g["total"] for g in merged["func"].values())
    w("<div class='card'><div class='k'>功能覆盖率 covergroup</div><div class='v %s'>%s</div><div class='d'>%d / %d bins</div></div>" % (
        cls(fv), "n/a" if fv is None else "%.1f%%" % fv, fh, ft))
    w("</div>")

    if tests:
        w("<h2>各用例对比</h2><div class='wrap'><table><tr><th>用例</th>%s<th>功能覆盖率</th></tr>" % "".join(
            "<th>%s</th>" % e(TYPE_ZH[t]) for t in types))
        for name, data in tests + [("合并 merged", merged)]:
            row = data["code"][None]
            w("<tr><td>%s</td>%s<td>%s</td></tr>" % (e(name), "".join("<td>%s</td>" % bar(pct(*row[t])) for t in types),
                                                    bar(data["func_overall"])))
        w("</table></div>")

    w("<h2>DUT 代码覆盖率（按文件）</h2><div class='wrap'><table><tr><th>文件</th>%s</tr>" % "".join(
        "<th>%s</th>" % e(TYPE_ZH[t]) for t in types))
    for key in sorted(k for k in merged["code"] if k):
        row = merged["code"][key]
        w("<tr><td><a href='#src-%s'>%s</a></td>%s</tr>" % (e(os.path.basename(key)), e(os.path.basename(key)), "".join(
            "<td>%s <span class='muted'>%d/%d</span></td>" % (bar(pct(*row[t])), row[t][0], row[t][1]) for t in types)))
    w("</table></div>")
    w("<p class='muted'>同一个模块的多个实例是合并统计的：<code>slave_fifo.v</code> 一行是三个通道 FIFO 的并集"
      "（任一通道覆盖到就算覆盖）。<code>mcdf.v</code> 只有连线，所以只有翻转覆盖率。</p>")

    w("<h2>功能覆盖率（covergroup）</h2>")
    w("<p class='muted'>covergroup 得分 = 其中各 coverpoint / cross 覆盖率的加权平均；标注「权重 0」的 coverpoint 只用于构成 cross，"
      "不计入得分（IEEE 1800-2017 19.11）。总分 = 各 covergroup 得分的平均值。</p>")
    for cg_name, group in merged["func"].items():
        w("<details%s><summary>%s &nbsp; %s <span class='muted'>%d/%d bins</span></summary>" % (
            "" if (group["cov"] or 0) >= 100 else " open", e(group["name"]), bar(group["cov"]), group["hit"], group["total"]))
        for item_name, item in group["items"].items():
            w("<h3>%s <code>%s</code> &nbsp; %s <span class='muted'>%d/%d%s</span></h3><div class='bins'>" % (
                "cross" if item["cross"] else "coverpoint", e(item_name), bar(item["cov"]), item["hit"], item["total"],
                "" if item["weight"] else " · 权重 0，不计入得分"))
            for b in item["bins"]:
                if b["kind"]:
                    k = "ill"
                    label = "%s（%s bin）×%d" % (b["name"], b["kind"], b["count"])
                else:
                    k = "hit" if b["count"] >= b["at_least"] else "miss"
                    label = "%s ×%d" % (b["name"], b["count"])
                w("<span class='pill %s'>%s</span>" % (k, e(label)))
            w("</div>")
        w("</details>")

    w("<h2>未覆盖的行 / 分支 / 表达式</h2>")
    missing = [p for p in merged["points"] if p.get("t") in ("line", "branch", "expr") and p["count"] == 0]
    missing.sort(key=lambda p: (p.get("f", ""), int(p.get("l", "0") or 0), p.get("t", "")))
    if missing:
        w("<div class='wrap'><table><tr><th>文件</th><th>行号</th><th>类型</th><th>说明</th></tr>")
        for p in missing:
            base = os.path.basename(p.get("f", "?"))
            w("<tr><td>%s</td><td class='num'><a href='#src-%s-%s'>%s</a></td><td>%s</td><td><code>%s</code></td></tr>" % (
                e(base), e(base), e(p.get("l", "")), e(p.get("l", "?")), e(p["t"]), e(p.get("o", ""))))
        w("</table></div>")
    else:
        w("<p>全部覆盖。</p>")

    w("<h2>带覆盖率标注的 DUT 源码</h2>")
    w("<p class='muted'>左列为行号，第二列为该行所在语句块的执行次数。红色 = 该行或其分支从未执行；"
      "黄色 = 该行上有未覆盖的表达式或信号翻转。</p>")
    by_file = collections.defaultdict(list)
    for p in merged["points"]:
        if p.get("t") in CODE_TYPES:
            by_file[p.get("f", "?")].append(p)
    for path in sorted(by_file):
        base = os.path.basename(path)
        line_cnt = {}
        notes = collections.defaultdict(list)
        partial = set()
        for p in by_file[path]:
            t = p["t"]
            if t == "line" or (t == "branch" and p.get("S")):
                for ln in span_lines(p):
                    line_cnt[ln] = min(line_cnt.get(ln, p["count"]), p["count"])
            if t != "line" and p["count"] == 0:
                try:
                    ln = int(p.get("l", "0"))
                except ValueError:
                    continue
                if t == "branch":
                    notes[ln].append("分支 %s 未执行" % p.get("o", ""))
                elif t == "expr":
                    partial.add(ln)
                    notes[ln].append("表达式 %s 未出现" % p.get("o", ""))
                elif t == "toggle":
                    partial.add(ln)
        toggle_miss = collections.Counter()
        for p in by_file[path]:
            if p["t"] == "toggle" and p["count"] == 0:
                toggle_miss[int(p.get("l", "0") or 0)] += 1
        for ln, n in toggle_miss.items():
            notes[ln].append("%d 个位翻转未出现" % n)
        row = merged["code"][path]
        try:
            with open(os.path.join(src_dir, path), "r", encoding="utf-8", errors="replace") as f:
                src = f.read().splitlines()
        except OSError:
            src = []
        rows = []                 # the lines sit in a <pre>: no line breaks between the spans
        if not src:
            rows.append("<span class='l muted'>(找不到源文件 %s)</span>" % e(path))
        for i, text in enumerate(src, 1):
            c = ""
            if i in line_cnt and line_cnt[i] == 0 or any("分支" in n for n in notes.get(i, [])):
                c = "miss"
            elif i in partial:
                c = "part"
            cnt = "" if i not in line_cnt else str(line_cnt[i])
            note = "   // " + "; ".join(notes[i]) if notes.get(i) else ""
            rows.append("<span class='l %s' id='src-%s-%d'><span class='n'>%d</span><span class='c'>%s</span>%s<span class='note'>%s</span></span>" % (
                c, e(base), i, i, cnt, e(text.expandtabs(4)), e(note)))
        w("<details id='src-%s'><summary>%s &nbsp; <span class='muted'>%s</span></summary><pre class='src'>%s</pre></details>" % (
            e(base), e(base), " · ".join("%s %s" % (t, fmt_pct(pct(*row[t])).strip()) for t in types), "".join(rows)))

    w("<p class='muted' style='margin-top:36px'>数据来源：Verilator <code>--coverage</code> 生成的 coverage/*.dat，经 "
      "<code>verilator_coverage --write</code> 合并。代码覆盖率只统计 DUT（见 mcdf.vlt）。</p>")
    w("</main><script>%s</script></body></html>" % SCRIPT)
    return "\n".join(h)


# ------------------------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--merged", required=True, help="merged coverage database")
    ap.add_argument("--test", action="append", default=[], metavar="NAME=FILE", help="per-test database (repeatable)")
    ap.add_argument("--out", required=True, help="output directory")
    ap.add_argument("--src-dir", default=os.path.dirname(os.path.abspath(__file__)),
                    help="directory the source paths in the database are relative to "
                         "(default: the directory of this script, i.e. the simulation directory)")
    args = ap.parse_args()

    merged = analyse(args.merged)
    tests = []
    for spec in args.test:
        name, _, path = spec.partition("=")
        tests.append((name, analyse(path)))
    report_html = html_report(merged, tests, args.src_dir)

    os.makedirs(args.out, exist_ok=True)
    text = text_report(merged, tests, args.merged)
    with open(os.path.join(args.out, "coverage_report.txt"), "w", encoding="utf-8") as f:
        f.write(text)
    with open(os.path.join(args.out, "coverage_report.html"), "w", encoding="utf-8") as f:
        f.write(report_html)
    sys.stdout.write(text)
    sys.stdout.write("HTML report: %s\n" % os.path.join(args.out, "coverage_report.html"))


if __name__ == "__main__":
    main()
