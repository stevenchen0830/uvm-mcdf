# 用开源工具运行 uvm-mcdf（Verilator 流程）

原工程的 `sim/Makefile` 需要 Synopsys VCS + Verdi。这个目录提供一套**完全免费、不需要 sudo** 的替代流程：
用 [Verilator](https://verilator.org) 5.054 编译仿真，UVM 库用 Accellera 的 IEEE 1800.2-2020.3.2 参考实现，
在 Windows 的 WSL2（Ubuntu）或 x86-64 Linux 上运行。

原工程的三个用例 `case0` / `case1` / `case2` 全部通过；另外补了两个用例 `case3`（通道关闭）和 `case4`（寄存器），
用来抓原来三个用例漏掉的 bug。`dut/` 没有任何改动，`testbench/` 的原有文件只在两个文件里新增了 26 行（见[改了什么](#改了什么)）。

## 准备

- **Windows 用户**需要 WSL2 和一个 Ubuntu：在管理员 PowerShell 里执行 `wsl --install -d Ubuntu`，重启后按提示建好用户即可。
- 安装工具链时要能访问 github.com 和 conda.anaconda.org（下载约 280 MB）。
- 磁盘：工具链约 1.7 GB，编译缓存每种配置约 1 GB（普通 / 带覆盖率各一份）。
- 进入本目录的方法：在 Windows 资源管理器里打开 `sim_verilator` 文件夹，在地址栏输入 `wsl` 回车，就会在这个目录打开 WSL 终端。
  也可以在 WSL 里手动 `cd`：`C:\Users\你\uvm-mcdf` 对应 `/mnt/c/Users/你/uvm-mcdf`。

## 快速开始

**1. 安装工具链（只需一次，全部装在 `~/eda` 下）**

在 WSL / Linux 终端里进入本目录后执行：

```bash
./setup_toolchain.sh
```

它会下载 micromamba，并从 conda-forge 装好 Verilator、GCC、make、z3（约束求解器）等。不改系统、不需要 root。

**2. 跑回归**

WSL / Linux 终端：

```bash
./run.sh regress
```

或者在 Windows 的 PowerShell / cmd 里（会自动通过 WSL 执行）：

```bat
.\run.cmd regress
```

第一次会先编译，期间没有输出：在 32 线程、24 GB 内存的机器上约 45 秒，并行数只有 6 时约 1 分钟，核数少、内存小的笔记本可能要几分钟。
然后依次跑五个用例（共约半分钟），最后打印汇总：

```
==================== regression summary ====================
  case0    PASSED   (total comparison count: 140 )
  case1    PASSED   (total comparison count: 300 )
  case2    PASSED   (total comparison count: 5613 )
  case3    PASSED   (total comparison count: 196 )
  case4    PASSED   (total comparison count: 14 )
============================================================
```

`case2` 是随机用例，比较次数随种子变化（上面是 `SEED=1` 的结果）；其余四个是确定的：140、300、196、14。

| 用例 | 内容 | 来源 |
|---|---|---|
| case0 | 三个通道依次发数据，基本功能 | 原工程 |
| case1 | 三个通道同时发数据，寄存器写后回读 | 原工程 |
| case2 | 随机配置、随机数据，寄存器后门检查 | 原工程 |
| case3 | 轮流关闭一个通道（关闭期间照样向它发数据）；两个、三个通道同时发数据时按优先级检查出包顺序（包括优先级相同）；最后一轮下游变慢，检查 grant 握手 | 后补 |
| case4 | 寄存器和寄存器总线：复位值、全部取值读写、保留位、只读、非法地址、发给别的从设备的传输；FIFO 里存着数据时的状态寄存器（总线忙、通道关闭时也要对） | 后补 |

每个用例的讲解见 [`../doc/verification_notes_zh.md`](../doc/verification_notes_zh.md) 第 3 节。

## 常用命令

`run.sh`（或 `run.cmd`）后面的参数原样传给 `make`。如果已经 `source ~/eda/env.sh`，也可以直接用 `make`。

| 命令 | 作用 | 对应原 VCS 流程 |
|---|---|---|
| `./run.sh` | 编译并运行 case0 | `make` |
| `./run.sh TESTNAME=case1` | 运行指定用例（没有改动就不重新编译） | `make TESTNAME=case1` |
| `./run.sh sim TESTNAME=case2` | 同上（模型不存在或源码变了会先编译） | `make sim TESTNAME=case2` |
| `./run.sh regress` | 跑完全部 5 个用例并汇总（只跑一部分：`./run.sh regress TESTS="case3 case4"`） | `./runlist` |
| `./run.sh COV=1 regress cov` | 带覆盖率编译、跑回归、合并并出报告 | `./runlist COV=1` + `make cov` |
| `./run.sh DUMP=1 TESTNAME=case0` | 额外导出波形 `case0_sim_dir/case0.fst` | 自动导出 fsdb |
| `./run.sh SEED=1234 TESTNAME=case2` | 用指定种子复现一次运行 | — |
| `./run.sh VERBOSITY=UVM_HIGH` | 打印更详细的 UVM 信息（拓扑、每个事务） | `make VERBOSITY=UVM_HIGH` |
| `./run.sh check` | 用独立的 Python 周期模型核对 case0 / case1 / case2 最近一次的日志（模型不包含后补的 case3 / case4） | — |
| `./run.sh clean` / `cleanall` | 删除编译结果 / 连同日志、波形、覆盖率一起删除 | `make clean` / `cleanall` |
| `./run.sh help` | 打印用法 | — |

**随机种子**：不指定 `SEED` 时每次运行自动取一个新的随机种子（相当于 VCS 的 `+ntb_random_seed_automatic`）。
种子打印在终端的 `[sim] case2  seed=...` 这一行，也记录在日志文件的第一行，出问题时用 `SEED=` 复现。`SEED` 的取值范围是 1 ~ 2147483647。

**通过的判定**比原测试平台自己的 `TestCase Passed` 严格一些，一次运行要同时满足：仿真器正常退出、日志里有 `TestCase Passed`、
没有 `UVM_FATAL`、没有随机化失败（例如找不到 z3）、scoreboard 至少比较了 1 个包。
最后一条是因为原平台只看 `UVM_ERROR` 的个数，一个包都没比较也会打印 `TestCase Passed`。
自己写的用例如果本来就不发数据，加 `MIN_COMPARE=0`。

## 输出文件

| 文件 | 内容 |
|---|---|
| `<用例>_sim_dir/sim_<用例>.log` | 仿真日志（和 VCS 流程同名），第一行是种子 |
| `<用例>_sim_dir/<用例>.fst` | 波形（`DUMP=1` 时生成） |
| `sim_result.log` | 每次运行追加一条记录（时间戳 + 通过 / 失败），原测试平台自己写的。以 `UVM_FATAL` 结束的运行（例如超时）不会留下记录 |
| `compile_nocov.log` / `compile_cov.log` | 编译日志 |
| `coverage/<用例>.dat` | 单个用例的覆盖率数据（`COV=1` 时生成） |
| `coverage/report/coverage_report.html` | 合并后的覆盖率报告，用浏览器打开 |
| `coverage/report/coverage_report.txt` | 同一份报告的纯文本版 |

编译产物不放在工程目录里，而是放在 `~/.cache/uvm-mcdf-verilator/<工程路径的哈希>/`：
在 Windows 盘上编译几千个 C++ 文件非常慢，而且这样工程路径里可以有空格。`./run.sh clean` 删除当前工程位置对应的那一份。

### 在 Windows 和 WSL 之间找到这些文件

- 工程放在 Windows 盘上：报告和波形就是 `sim_verilator\coverage\report\coverage_report.html` 和 `sim_verilator\case0_sim_dir\case0.fst`，
  在资源管理器里双击或拖进查看器即可。
- 工程放在 WSL 里（`~/...`）：在 WSL 终端进入相应目录后执行 `explorer.exe .`，会用 Windows 资源管理器打开这个目录
  （地址形如 `\\wsl.localhost\Ubuntu\home\<用户名>\...`），然后同样双击 HTML，或把 `.fst` 拖进查看器。

## 看波形

`DUMP=1` 生成的是 FST 格式（比 VCD 小很多），两个免费查看器都能打开：

- **Surfer**：打开 <https://app.surfer-project.org>，把 `.fst` 文件拖进去即可，不用安装。
- **GTKWave**：在 Windows 上装一个 GTKWave，直接打开 `.fst`。
  也可以装到 WSL 里：`WITH_GTKWAVE=1 ./setup_toolchain.sh`，窗口通过 Windows 11 的 WSLg 显示。
  （这条路径只确认过能装上、`gtkwave --version` 能运行，没有实际打开过窗口；不行的话请用 Surfer 或 Windows 版 GTKWave。）

`./run.sh wave TESTNAME=case0` 会用 WSL 里找到的查看器打开波形；找不到查看器时打印这个文件的 Windows 路径。

## 覆盖率

```bash
./run.sh COV=1 SEED=1 regress cov
```

对应原流程的 `-cm line+cond+fsm+branch+tgl` 加 `+define+COV_EN`：

- **代码覆盖率**只统计 DUT（范围相当于 `cov.cfg` 里的 `+tree tb_top.dut`）：行、分支、表达式（VCS 叫 cond）、翻转。
  同一个模块的多个实例是合并统计的：三个 `slave_fifo` 只要有一个覆盖到某一点就算覆盖；VCS / URG 可以按实例分别查看。
- **功能覆盖率**来自 `testbench/component/coverage/coverage_pkg.sv` 里的 6 个 covergroup。

上面的命令（种子 1）跑完五个用例后的合并结果，以及只算原来三个用例时的数字：

| | 行 | 分支 | 表达式 | 翻转 | 功能覆盖率 |
|---|---|---|---|---|---|
| 五个用例 | 98.0% (100/102) | 100% (88/88) | 92.9% (118/127) | 67.2% (1756/2614) | 100% |
| 只有 case0 / case1 / case2 | 94.1% (96/102) | 94.3% (83/88) | 90.6% (115/127) | 58.0% (1516/2614) | 73.3% |

不写 `SEED=1` 时 case2 每次随机到的配置不同，数字会有出入。例如 `cg_formatter_grant` 的三个 bin，种子 1 下要靠 case2（随机到了慢速下游）
和 case3 的慢速下游一轮一起才全部命中；case2 换一个种子，合并结果可能只有 66.7%，功能覆盖率相应变成 94.4%。
这些百分比都是 Verilator 按自己的统计点算出来的，没有和 VCS / URG 的数值对比过。

`cov` 会合并 `coverage/` 下所有的 `.dat` 文件。想只看其中几个用例，先删掉 `coverage/` 目录，再用 `TESTS=` 指定，例如
`./run.sh COV=1 SEED=1 regress cov TESTS="case0 case1 case2"`。

HTML 报告里可以看到每个 covergroup 的每个 bin 是否命中、没覆盖到的行 / 分支，以及带标注的 DUT 源码。

## 改了什么

这些改动目前是 git 分支 `verilator-port` 上**尚未提交**的工作区修改。`git status` 会列出被修改的文件和新增的 `sim_verilator/`、
`doc/verification_notes_zh.md`，以及 `testbench/component/case/` 下的 `case3.sv`、`case4.sv`、`case_common.sv`；`git diff` 显示对原有文件的修改。
（请在 Windows 的 Git Bash / PowerShell 里运行 git。这份代码是 Windows 版 git 以 CRLF 换行检出的，在 WSL 里直接 `git diff` 会显示所有文件都被改了，
要写成 `git -c core.autocrlf=true diff`。）

**修改的原有文件 4 个**：`README.md`（加了一小节说明）、`sim/runlist`（加了两行，把新用例也列进原 VCS 流程的回归脚本），以及下面 2 个测试平台文件。
测试平台原有文件的改动共 +26 行，没有删除或改动任何原有的行。其中 24 行用 `` `ifdef VERILATOR `` 包住，VCS 流程走原来的代码；
另外 2 行是 `tb_top.sv` 里两个新用例的 `` `include ``：

| 文件 | 原因 |
|---|---|
| `testbench/tb_top.sv` | `$fsdbDumpfile` / `$fsdbDumpvars` 是 Verdi 专有系统任务。Verilator 下改为 `+DUMP` 时用 `$dumpfile` / `$dumpvars` 导出 FST。另外 `` `include `` 了 `case3.sv` 和 `case4.sv` |
| `testbench/component/coverage/coverage_pkg.sv` | Verilator 没有实现 `$get_coverage()`。改为 6 个 covergroup 的 `get_coverage()` 取平均（这 6 个是平台里全部的 covergroup，权重相同） |

**新增的两个用例**（`testbench/component/case/case3.sv`、`case4.sv`，加上它们共用的 `case_common.sv`）是移植完成、做完覆盖率和变异测试之后补的：
`case3` 测通道关闭和优先级，`case4` 测寄存器，讲解见 `../doc/verification_notes_zh.md` 第 3.2 节。
`case_common.sv` 里有一个检查整形器输出的小组件（出包顺序、grant 握手、`fmt_end`）和两个基类，只给这两个用例用。
它们用平台里已有的组件，是普通的 UVM 写法，没有改动任何原有组件，但只在 Verilator 上跑过。

**新增的流程文件（都在本目录）：**

| 文件 | 作用 |
|---|---|
| `Makefile` | Verilator 版的编译 / 仿真 / 回归 / 覆盖率流程，用法对齐 `../sim/Makefile` |
| `mcdf.vlt` | Verilator 配置：让寄存器数组对 VPI 可见（case2 的后门访问需要）、代码覆盖率只统计 DUT、已审查告警的豁免 |
| `uvm/` | UVM 1800.2-2020.3.2 库（Apache-2.0，取自 Verilator v5.054 源码树，许可证全文见 `uvm/LICENSE`）和一个空的 `uvm_macros.svh` 占位文件 |
| `setup_toolchain.sh` | 一次性安装工具链 |
| `run.sh` / `run.cmd` | Linux / Windows 入口脚本 |
| `cov_report.sh` / `cov_report.py` | 合并覆盖率并生成文本和 HTML 报告 |
| `oracle/` | 独立的 MCDF 周期级 Python 模型和日志核对脚本（`make check`） |
| `mutation/` | 变异测试脚本：往 DUT 副本里注入 bug，看测试平台能否发现 |

测试平台写的是 UVM-1.1 风格（`starting_phase`、`uvm_top`、`default_sequence` 等），
UVM 2020.3.2 保留了这些接口，所以**没有任何一行是因为 UVM 版本而改的**。

## 和 VCS 的差别

先说明一点：**手头没有 VCS，下面凡是说到"VCS 下会怎样"的地方都是按 IEEE 1800 和 UVM 源码推断的，不是实测。**
实测到的是：case0 / case1 的比较次数、寄存器读写值和每一次比较的时刻，与独立的 Python 模型逐纳秒一致；
case2 的比较次数和寄存器写入序列一致。没有发现结果上的差别。下面是工具本身带来的不同，查日志或波形时可能会注意到：

| 现象 | 说明 |
|---|---|
| UVM 库版本不同 | VCS 流程用自带的 UVM 1.1（`-ntb_opts uvm-1.1`），这里是 1800.2-2020.3.2：库内部消息的文件路径、报告汇总的排版不同，对用例结果没有影响 |
| 日志里 `UVM_WARNING` 为 0 | Verilator 是两态仿真器，`rstn` 一开始就是 0 而不是 X，所以 10 ns 处没有下降沿，测试平台里等 `@(negedge rstn)` 的复位线程不会执行。按 UVM 源码推断，VCS 下此时会多出 1 条 `[RegModel]` 警告。对结果没有影响：DUT 在第一个时钟沿照常复位；case0 / case1 / case3 会把三个控制寄存器都写一遍，case2 / case4 先调用 `p_rm.reset()` |
| 波形是 `.fst` 不是 `.fsdb` | Verdi 的 FSDB 是私有格式 |
| 波形里没有 X | 两态仿真，未复位的寄存器显示为 0 |
| 覆盖率类型名和粒度不同 | VCS 的 cond 对应这里的 expr；同一模块的多个实例合并统计；FSM 覆盖率目前提取不出这个 DUT 的状态机，显示为 0/0 |
| `illegal_bins illegal = default` 不报错 | Verilator 不检查这种写法的非法 bin（正常运行不会命中）。而命中 `illegal_bins illegal = {0}` 会直接终止仿真 |
| `$error` 会终止仿真 | VCS 打印后继续跑。测试平台里的 `assert(rsp.rsp) else $error` 只在平台自身出错时触发 |
| 随机序列不同 | 约束由 z3 求解，同一个种子在 VCS 和 Verilator 下产生的激励不同，取值分布也不均匀（合法但偏向大值） |
| 每次改代码要整体重编译 | 一次 1 分钟左右（取决于机器）；VCS 可以增量编译 |

必须使用 **Verilator 5.054**（这是唯一验证过的版本，Makefile 会拒绝更旧的；更新的版本没有测过，升级后请先跑 `./run.sh regress check`）。
5.052 才开始正式支持 UVM，5.054 修复了时钟块驱动晚一拍的问题，这个测试平台的寄存器总线时序依赖它。

## 这个移植是怎么验证的

1. **回归**：五个用例通过，比较次数与按配置手算的一致（case0：80/40/20；case1：100/100/100；case3：112/56/28；case4：7/4/3）。
2. **独立模型核对**（`./run.sh check`）：`oracle/mcdf_oracle.py` 是对照 RTL 和测试平台源码、用 Python 从头重写的周期级模型（不复用任何 RTL / UVM 代码）。
   case0 的 140 次、case1 的 300 次比较的时间戳与 Verilator 日志逐纳秒一致；case2 核对比较次数、寄存器写入序列和下游反压出现之前的时间戳。
   要注意它的局限：在 IEEE 1800 没有规定结果的那个竞争点上（见 `../doc/verification_notes_zh.md` 第 6 节第 8 条），模型直接采用了测试平台赖以工作的假设，
   所以它能说明"Verilator 忠实地执行了这份代码"，不能证明"VCS 的日志逐纳秒相同"。
3. **种子扫描**：原有三个用例 66 次运行（case2 的 40 个种子、覆盖率构建下另 20 个种子，case0 / case1 各 3 个）全部通过，并全部通过第 2 条的核对。
   后补的 case3 / case4 各换了 8 个种子，全部通过；比较结果、寄存器总线上的每一次访问和用例自己的检查消息都完全相同
   （日志里随种子变化的只有一个 IDLE 总线命令里没有用到的随机地址和数据，以及 case3 最后一轮慢速下游的时间）。
4. **变异测试**（`mutation/run_mutation.sh`）：往 DUT 里注入 bug，看回归会不会失败，证明"通过"不是空跑。
   最初的 12 个 bug 里原来三个用例报出 9 个（包括 case2 的寄存器后门检查），漏掉的 3 个是原测试平台本身的验证盲区。
   case3 / case4 就是为这 3 个补的。之后又请独立评审只读代码找漏洞，做了两轮：第一轮的 4 个 bug 里有 3 个确实被第一版漏掉，
   第二轮的 10 个 bug 在第二版上全部漏掉，用例据此各改了一版。现在一共 31 个 bug，五个用例报出 30 个，只用原来三个用例是 9 / 31。
   没报出的 m24 需要在包长码为 4~7 时发数据，而原平台的参考模型算不对这种包长，属于已知缺口。
   结果在 `mutation/mutation_report.txt`，逐个的分析见 `../doc/verification_notes_zh.md` 第 5 节。
5. **语言特性探针**：时钟块驱动与采样、约束随机、covergroup、VPI 后门等逐项用小例子在 Verilator 上确认过行为。
6. **一条间接旁证**：仓库里唯一的 VCS 痕迹是作者保存的 Verdi 会话文件 `../sim/signal.rc`，它保存于寄存器总线改成 APB 之前的旧版本。
   把模型的参数换成那个旧版本后（`python3 oracle/oracle_vs_vcs_rc.py`），能算出文件里的 marker = 825 ns，以及"仿真结束时间 × 1.01 = 27305.35"
   （1% 是对 Verdi 全屏缩放留白的猜测）。对现在的代码，这两个数对应 845 ns 和 27065 ns，和 `signal.rc` 对不上是正常的。

### 没有验证过的内容

- 没有任何 VCS 运行结果可以对照。推断的内容包括：`@(posedge clk)` 之后读时钟块输入读到的是上一拍的采样（测试平台只有这样才能工作）、
  10 ns 处的那条 `[RegModel]` 警告、case2 在 VCS + UVM 1.1 下的寄存器写次数、带 `type_option.weight = 0` 的 covergroup 在 VCS 下的得分。
- 现有用例没有走到的路径（仿真中途复位、序列在阶段结束时被强行终止等）没有在 Verilator 上跑过。
- 后补的 case3 / case4 只在 Verilator 上跑过，也没有独立的 Python 模型核对它们；它们的依据是变异测试（注入的 bug 确实被它们报出来）
  和独立评审。换到别的仿真器上，case3 重新打开通道的那一拍有可能被原 scoreboard 误报一次 `[CHKERR]`（原因见 `../doc/verification_notes_zh.md` 第 6 节测试平台第 10 条）。
- FST 波形的内容用脚本解析核对过关键时刻，没有在图形查看器里人工看过。

## 常见问题

**`verilator not found` / `make: command not found`**
工具链不在 PATH 里。用 `./run.sh ...` 代替 `make ...`，或者先 `source ~/eda/env.sh`。

**`./run.sh: Permission denied`**
脚本没有可执行权限（例如从 Windows 提交后在 Linux 上重新克隆）。用 `bash run.sh regress`、`bash setup_toolchain.sh`，或者 `chmod +x *.sh mutation/*.sh`。

**`ERROR: Verilator x.xxx ... is too old`**
PATH 里有一个旧的 verilator（例如 `sudo apt install verilator` 装的）。用 `./run.sh`，它会优先使用 `~/eda` 里的工具链。

**编译时内存不够、WSL 卡死**
每个 C++ 编译进程约占 1.5 GB，Makefile 会按空闲内存自动限制并行数。可以手动再降：`./run.sh JOBS=4`，
或者 `./run.sh OPT=-O0`（编译更快更省内存，仿真慢一些）。不要同时开多个编译。

**仿真一直不结束**
单次仿真默认最多跑 1800 秒墙钟时间（`WALL_LIMIT`），超时判为失败。
case3 / case4 自己另设了 1 ms 仿真时间的超时，卡住时几秒钟内就会以 `UVM_FATAL [TIMEOUT]` 结束。

**编译报 `BADVLTPRAGMA: Unknown verilator comment`**
Verilator 会把以 `Verilator` / `verilator` 开头的注释当成给它的指令。自己加注释时不要用这个词开头，例如写成 `// 注：Verilator 下……`。

**不要加 `+verilator+rand+reset+1` 或 `+2`**
这是 Verilator 用来查复位问题的选项，但在这个测试平台上 `rstn` 可能从 1 开始，寄存器总线 driver 会在复位期间就开始驱动，第一次寄存器写丢失，结果随种子变化。默认设置（不加）是安全的。

**想恢复原状**
`rm -rf ~/eda ~/.cache/uvm-mcdf-verilator ~/.cache/uvm-mcdf-mutation` 删除工具链、编译缓存和变异测试的临时目录。
代码方面：这些改动是工作区里尚未提交的修改，`git checkout master` 去不掉它们。想留一份备份再还原，用 `git stash -u`（以后可以 `git stash pop` 取回）；
想直接丢弃，用 `git checkout -- README.md sim/runlist testbench/` 还原 4 个被改动的原有文件，再手动删除 `sim_verilator/`、`doc/verification_notes_zh.md`
和 `testbench/component/case/` 下的 `case3.sv`、`case4.sv`、`case_common.sv`。
