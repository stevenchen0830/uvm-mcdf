#!/usr/bin/env python3
"""
Independent cycle-accurate oracle for the uvm-mcdf testbench (a model written from scratch in
Python; it shares no code with the RTL or the UVM testbench).

It re-implements, from a reading of the sources, (a) the MCDF RTL at clock-cycle granularity and
(b) the timing-relevant behaviour of the UVM testbench (bus driver, channel drivers, formatter
driver, monitors, reference model, scoreboard, arbiter checker) under IEEE 1800 scheduling.
Where IEEE 1800 leaves a race open (a clocking-block input read right after a raw
"@(posedge clk)") the model uses the "old sample" resolution that the testbench needs in order
to work, which is also what Verilator 5.054 does.

What the model has been checked against:
  - the Verilator flow: the time stamp of every scoreboard comparison in the case0 and case1
    logs is reproduced to the nanosecond (check_log_against_oracle.py does this comparison);
    for case2 the comparison counts, the register writes and the time stamps before the
    downstream FIFO starts to throttle are compared.
  - indirectly, the only VCS artefact in the repository, the author's Verdi session file
    ../../sim/signal.rc. It was saved at an older commit (before the register bus became
    APB-like). With the parameters of that commit the model gives the marker time stored in
    the file (825 ns) and an end time that is 1/1.01 of the stored viewport end, see
    oracle_vs_vcs_rc.py. No VCS log was available, so this is corroboration, not proof.
Because the race mentioned above is resolved by assumption, agreement with this model shows
that a simulator executes this testbench faithfully under that assumption; it does not prove
that another simulator's log is identical.

Time base: clk posedge k is at t = 5 + 10*k ns.  "cycle k" = [t_k, t_k+10).
  phase A  : #delay wake-ups that land on a posedge time (before the clock NBA)
  phase B  : processes woken by @(posedge clk); raw reads see pre-edge DUT register values
  DUT edge : clocked update
  phase C  : waits on DUT combinational outputs (fmt_req / fmt_start / ch_ready) wake up
  phase D  : (+1ns) clocking-block drives become DUT inputs of cycle k
  negedge  : (+5ns) @(negedge clk) waiters

Usage: python3 mcdf_oracle.py case0|case1|case2 [seed] [-v]
"""
import sys
import random

IDLE_S, SETUP_S, ENABLE_S = 0, 1, 2
FMT_REQ, FMT_WAIT_GRANT, FMT_START, FMT_SEND, FMT_END, FMT_IDLE = 0, 1, 3, 2, 6, 7
CMD_WRITE, CMD_READ, CMD_IDLE = 2, 1, 0


def tpos(k):
    return 5 + 10 * k


class Dut:
    """Cycle model of mcdf.v (ctrl_regs + 3 x slave_fifo + arbiter + formater)."""

    def __init__(self):
        self.reset()

    def reset(self):
        self.last_state = IDLE_S
        self.addr_r = 0
        self.mem = [7, 7, 7, 0x20, 0x20, 0x20]
        self.cmd = 0
        self.wr = [0] * 3
        self.rd = [0] * 3
        self.fmem = [[0] * 32 for _ in range(3)]
        self.val = [0] * 3
        self.dout = [0] * 3
        self.id_sel = 3
        self.pkglen_sel = 7  # not reset in RTL (x in a 4-state sim -> length decodes to 32)
        self.cnt_rec = 0
        self.cnt_sen = 0
        self.ffifo = [0xFFFFFFFF] * 32
        self.buf = [0xFFFFFFFF] * 3
        self.buf_val = [0] * 3
        self.c_state = FMT_IDLE

    # ---- combinational views of the current state
    def en(self, i):
        return self.mem[i] & 1

    def prio(self, i):
        return (self.mem[i] >> 1) & 3

    def pkglen(self, i):
        return (self.mem[i] >> 3) & 7

    def full(self, i):
        return ((self.wr[i] ^ 0x20) & 0x3F) == self.rd[i]

    def empty(self, i):
        return self.wr[i] == self.rd[i]

    def ready(self, i):
        return int((not self.full(i)) and self.en(i))

    def req(self, i):
        return int(not self.empty(i))

    def margin(self, i):
        return (32 - ((self.wr[i] - self.rd[i]) & 0x3F)) & 0x3F

    def length(self):
        return {0: 4, 1: 8, 2: 16, 3: 32}.get(self.pkglen_sel, 32)

    def a2f_val(self):
        return self.val[self.id_sel] if self.id_sel < 3 else 0

    def a2f_data(self):
        return self.dout[self.id_sel] if self.id_sel < 3 else 0xFFFFFFFF

    def fmt_out(self):
        s = self.c_state
        L = self.length()
        ack = idreq = req = start = end = send = 0
        if s == FMT_IDLE:
            ack = int(self.id_sel != 3)
            idreq = int(self.id_sel == 3)
        elif s == FMT_REQ:
            ack = int(not (self.cnt_rec >= L))
            req = 1
        elif s == FMT_WAIT_GRANT:
            req = 1
        elif s == FMT_START:
            start = 1
            send = 1
        elif s == FMT_SEND:
            send = 1
        elif s == FMT_END:
            end = 1
            send = 1
            idreq = 1
        return ack, idreq, req, start, end, send

    def n_state(self, grant):
        s = self.c_state
        L = self.length()
        if s == FMT_IDLE:
            return FMT_REQ if self.cnt_rec == ((L - 1) & 0x3F) else FMT_IDLE
        if s == FMT_REQ:
            return FMT_WAIT_GRANT if self.cnt_rec >= L else FMT_REQ
        if s == FMT_WAIT_GRANT:
            return FMT_START if grant else FMT_WAIT_GRANT
        if s == FMT_START:
            return FMT_SEND
        if s == FMT_SEND:
            return FMT_END if self.cnt_sen == ((L - 2) & 0x3F) else FMT_SEND
        return FMT_IDLE

    def fmt_req(self):
        return self.fmt_out()[2]

    def fmt_start(self):
        return self.fmt_out()[3]

    def fmt_data(self):
        return self.ffifo[self.cnt_sen & 31] if self.fmt_out()[5] else 0xFFFFFFFF

    def acks(self):
        ack = self.fmt_out()[0]
        return [ack if self.id_sel == i else 0 for i in range(3)]

    def current_state(self, psel, pen):
        if self.last_state == IDLE_S:
            return SETUP_S if psel else IDLE_S
        if self.last_state == SETUP_S:
            return ENABLE_S
        return SETUP_S if (psel and not pen) else IDLE_S

    def arb_choice(self):
        r = [self.req(i) for i in range(3)]
        p = [self.prio(i) for i in range(3)]
        code = (r[2] << 2) | (r[1] << 1) | r[0]
        if code == 0b001:
            return 0
        if code == 0b010:
            return 1
        if code == 0b011:
            return 0 if p[1] >= p[0] else 1
        if code == 0b100:
            return 2
        if code == 0b101:
            return 0 if p[2] >= p[0] else 2
        if code == 0b110:
            return 1 if p[2] >= p[1] else 2
        if code == 0b111:
            sel = None
            if p[2] >= p[0] and p[1] >= p[0]:
                sel = 0
            if p[2] >= p[0] and p[1] < p[0]:
                sel = 1
            if p[2] < p[0] and p[2] >= p[1]:
                sel = 1
            if p[2] < p[0] and p[2] < p[1]:
                sel = 2
            return sel
        return 3

    # ---- clocked update (rstn == 1)
    def posedge(self, inp):
        cs = self.current_state(inp['psel'], inp['pen'])
        ack, idreq, req, start, end, send = self.fmt_out()
        ns = self.n_state(inp['grant'])
        a2f_id = self.id_sel
        a2f_val = self.a2f_val()
        a2f_data = self.a2f_data()
        acks = [ack if self.id_sel == i else 0 for i in range(3)]

        # ctrl_regs
        new_addr = inp['paddr'] if cs == SETUP_S else self.addr_r
        new_mem = list(self.mem)
        for i in range(3):
            new_mem[3 + i] = self.margin(i)
        if cs == ENABLE_S and inp['pwr']:
            idx = {0: 0, 4: 1, 8: 2}.get(self.addr_r)
            if idx is not None:
                new_mem[idx] = inp['pwdata'] & 0x3F
        new_cmd = self.cmd
        if cs == SETUP_S and not inp['pwr']:
            idx = {0x00: 0, 0x04: 1, 0x08: 2, 0x10: 3, 0x14: 4, 0x18: 5}.get(inp['paddr'])
            if idx is not None:
                new_cmd = self.mem[idx]

        # slave fifos
        new_wr = list(self.wr)
        new_rd = list(self.rd)
        new_val = [0] * 3
        new_dout = list(self.dout)
        wmem = []
        accepted = []
        for i in range(3):
            if inp['valid'][i] and self.ready(i):
                wmem.append((i, self.wr[i] & 31, inp['data'][i]))
                new_wr[i] = (self.wr[i] + 1) & 0x3F
                accepted.append((i, inp['data'][i]))
            if acks[i] and not self.empty(i):
                new_rd[i] = (self.rd[i] + 1) & 0x3F
                new_val[i] = 1
                new_dout[i] = self.fmem[i][self.rd[i] & 31]

        # arbiter
        new_id = self.id_sel
        new_pk = self.pkglen_sel
        if idreq:
            new_id = self.arb_choice()
            new_pk = self.pkglen(new_id) if new_id < 3 else 7

        # formater
        new_cnt_rec = self.cnt_rec
        if idreq:
            new_cnt_rec = 0
        elif a2f_id < 3 and (a2f_val or self.buf_val[a2f_id]) and ack:
            new_cnt_rec = (self.cnt_rec + 1) & 0x3F
        new_buf = list(self.buf)
        new_bv = list(self.buf_val)
        fwrite = None
        if ack:
            if a2f_id < 3:
                if self.buf_val[a2f_id]:
                    fwrite = (self.cnt_rec, self.buf[a2f_id])
                    new_bv[a2f_id] = 0
                elif a2f_val:
                    fwrite = (self.cnt_rec, a2f_data)
        else:
            if a2f_id < 3 and a2f_val:
                if self.buf_val[a2f_id]:
                    raise RuntimeError('DUT model: formatter buffer overwrite (data loss)')
                new_buf[a2f_id] = a2f_data
                new_bv[a2f_id] = 1
        new_cnt_sen = self.cnt_sen
        if idreq:
            new_cnt_sen = 0
        elif send:
            new_cnt_sen = (self.cnt_sen + 1) & 0x3F

        # commit
        self.last_state = cs
        self.addr_r = new_addr
        self.mem = new_mem
        self.cmd = new_cmd
        for (i, a, d) in wmem:
            self.fmem[i][a] = d
        self.wr, self.rd, self.val, self.dout = new_wr, new_rd, new_val, new_dout
        self.id_sel, self.pkglen_sel = new_id, new_pk
        self.cnt_rec = new_cnt_rec
        if fwrite is not None:
            if fwrite[0] > 31:
                raise RuntimeError('DUT model: fmt_fifo index out of range')
            self.ffifo[fwrite[0]] = fwrite[1]
        self.buf, self.buf_val = new_buf, new_bv
        self.cnt_sen = new_cnt_sen
        self.c_state = ns
        return accepted


class Sim:
    def __init__(self, verbose=False, consume_first=True, seed=1):
        self.dut = Dut()
        self.k = -1               # index of last processed posedge
        self.t = 0
        self.phase = 'A'
        self.verbose = verbose
        self.rng = random.Random(seed)
        self.rstn = 0             # raw value visible to processes
        self.inp = dict(psel=0, pen=0, pwr=0, paddr=0, pwdata=0, valid=[0, 0, 0], data=[0, 0, 0], grant=0)
        self.staged = {}          # clocking drives issued in this time step
        self.pending_async = {}   # clocking drives issued off-edge (applied at next edge + 1ns)
        self.wq_pos_now = []
        self.wq_pos_next = []
        self.wq_neg = []
        self.wq_sig = []          # (name, gen)
        self.wq_wait = []         # (fn, gen)
        self.wq_nba = []
        self.delays = []          # (time, seq, gen)
        self._seq = 0
        self.hist = {}            # per-cycle history for monitors/checkers
        self.finished = False
        self.end_time = None
        self.log = []
        # fmt driver mailbox
        self.fifo_bound = 4096
        self.fifo_num = 0
        self.fifo_unbounded = True
        self.consume_period = 1
        self.consume_first = consume_first
        # scoreboard
        self.accepted = [[], [], []]     # words accepted by DUT per channel (== channel monitor stream)
        self.out_pkts = []               # (start_cycle, ch, length, [data])
        self._cap = None
        self.chk_errors = []
        self.sig_prev = {}
        self.prio = {}            # id(generator) -> ordering class inside phase B (0 first, 2 last)

    # ---- process management
    def spawn(self, gen):
        self._run(gen)

    def _run(self, gen):
        try:
            cmd = next(gen)
        except StopIteration:
            return
        self._dispatch(cmd, gen)

    def _dispatch(self, cmd, gen):
        op = cmd[0]
        if op == 'posedge':
            if self.phase == 'A':
                self.wq_pos_now.append(gen)
            else:
                self.wq_pos_next.append(gen)
        elif op == 'negedge':
            self.wq_neg.append(gen)
        elif op == 'sig':
            self.wq_sig.append((cmd[1], gen))
        elif op == 'wait':
            if cmd[1]():
                self._run(gen)
            else:
                self.wq_wait.append((cmd[1], gen))
        elif op == 'delay':
            self._seq += 1
            self.delays.append((self.t + cmd[1], self._seq, gen))
        elif op == 'nba':
            if self.phase == 'A':
                self.wq_nba.append(gen)
            else:
                self._run(gen)
        else:
            raise RuntimeError(op)

    def drive(self, **kw):
        """clocking-block output drive (skew 1ns)."""
        if self.phase in ('A', 'B', 'C'):
            self.staged.update(kw)
        else:
            self.pending_async.update(kw)

    def drive_ch(self, ch, valid, data):
        tgt = self.staged if self.phase in ('A', 'B', 'C') else self.pending_async
        tgt.setdefault('chv', {})[ch] = (valid, data)

    def _run_delays(self, t):
        due = sorted([d for d in self.delays if d[0] == t])
        self.delays = [d for d in self.delays if d[0] != t]
        for (_, _, g) in due:
            self._run(g)
        if any(d[0] < t for d in self.delays):
            raise RuntimeError('delay wake-up at unsupported time: %r' % [d[0] for d in self.delays if d[0] < t])

    def step(self):
        """advance one clock cycle: posedge k, then negedge."""
        self.k += 1
        k = self.k
        self.t = tpos(k)
        d = self.dut
        # phase A
        self.phase = 'A'
        self.staged = dict(self.pending_async)
        self.pending_async = {}
        self._run_delays(self.t)
        # phase B: posedge waiters (see pre-edge values)
        self.phase = 'B'
        procs = self.wq_pos_now + self.wq_pos_next
        self.wq_pos_now, self.wq_pos_next = [], []
        procs.sort(key=lambda g: self.prio.get(id(g), 1))   # stable: only tagged processes move
        for g in procs:
            self._run(g)
        nb = self.wq_nba
        self.wq_nba = []
        for g in nb:
            self._run(g)
        # DUT clocked update
        if self.rstn:
            acc = d.posedge(self.inp)
            for (ch, w) in acc:
                self.accepted[ch].append((k, w))
        else:
            d.reset()
        # tb_top: rstn <= 1 after the 10th posedge following t=10 (posedge k=10, t=105)
        if k == 10:
            self.rstn = 1
        # phase C: DUT comb outputs have their cycle-k values
        self.phase = 'C'
        self._record(k)
        self._wake_sig()
        # phase D: clocking drives applied at +1ns
        for key, v in self.staged.items():
            if key == 'chv':
                for ch, (vv, dd) in v.items():
                    self.inp['valid'][ch] = vv
                    self.inp['data'][ch] = dd
            else:
                self.inp[key] = v
        self.staged = {}
        self.hist[k].update(psel=self.inp['psel'], pen=self.inp['pen'], pwr=self.inp['pwr'],
                            paddr=self.inp['paddr'], pwdata=self.inp['pwdata'], grant=self.inp['grant'],
                            valid=list(self.inp['valid']), data=list(self.inp['data']))
        # negedge
        self.phase = 'N'
        self.t = tpos(k) + 5
        self._run_delays(self.t)
        procs = self.wq_neg
        self.wq_neg = []
        for g in procs:
            self._run(g)

    def _record(self, k):
        d = self.dut
        ack, idreq, req, start, end, send = d.fmt_out()
        idreq_v = idreq if self.rstn else 1
        self.hist[k] = dict(prdata=d.cmd, f2a_id_req=idreq_v, reqs=[d.req(i) if self.rstn else 0 for i in range(3)],
                            prios=[d.prio(i) for i in range(3)], acks=d.acks() if self.rstn else [0, 0, 0],
                            ready=[d.ready(i) for i in range(3)], en=[d.en(i) for i in range(3)],
                            fmt_start=start, fmt_req=req, fmt_end=end, fmt_data=d.fmt_data(),
                            fmt_len=d.length(), fmt_chid=d.id_sel, rstn=self.rstn)
        # packet capture straight from the DUT pins (cycle accurate)
        if start:
            self._cap = [k, d.id_sel, d.length(), []]
        if self._cap is not None and send:
            self._cap[3].append(d.fmt_data())
            if end:
                self.out_pkts.append(tuple(self._cap))
                self._cap = None

    def _wake_sig(self):
        d = self.dut
        cur = dict(fmt_req=d.fmt_req() if self.rstn else 0, fmt_start=d.fmt_start() if self.rstn else 0)
        waiters = self.wq_sig
        self.wq_sig = []
        for (name, g) in waiters:
            if cur[name] and not self.sig_prev.get(name, 0):
                self._run(g)
            else:
                self.wq_sig.append((name, g))
        self.sig_prev = cur
        ww = self.wq_wait
        self.wq_wait = []
        for (fn, g) in ww:
            if fn():
                self._run(g)
            else:
                self.wq_wait.append((fn, g))


# ----------------------------------------------------------------------------- testbench processes
def word(ch, pkt, i):
    return (0xC0000000 + (ch << 24) + (pkt << 8) + i) & 0xFFFFFFFF


def bus_driver(sim, ops, done):
    """driver_bus::do_drive + drive_one_pkt for a fixed list of (cmd, addr, data).  done(list_of_read_values)."""
    while True:                       # @(posedge vif.clk iff vif.rstn == 1)
        yield ('posedge',)
        if sim.rstn:
            break
    rd = []
    for (cmd, addr, data) in ops:
        t0 = sim.t
        if cmd == CMD_WRITE or cmd == CMD_READ:
            sim.drive(paddr=addr, pwr=int(cmd == CMD_WRITE), psel=1, pen=0, pwdata=data)
            yield ('posedge',)
            sim.drive(pen=1)
            yield ('posedge',)
            if cmd == CMD_READ:
                rd.append((sim.t, addr, sim.dut.cmd))      # tr.data = vif.prdata (raw, pre-edge)
            sim.drive(psel=0, pen=0, pwdata=0)
        else:
            sim.drive(psel=0, pen=0, pwdata=0)
        sim.log.append((t0, 'bus item cmd=%d addr=%02x data=%08x start' % (cmd, addr, data)))
        sim.log.append((sim.t, 'bus item cmd=%d addr=%02x done' % (cmd, addr)))
    done(rd, sim.t)


def monitor_bus(sim):
    """monitor_bus with 'old sample' semantics: at posedge j the clockvars hold cycle j-2 values."""
    while True:
        yield ('posedge',)
        j = sim.k
        if j < 2 or not sim.rstn:
            continue
        v = sim.hist[j - 2]
        if not (v['psel'] == 1 and v['pen'] == 0):
            continue
        addr = v['paddr']
        wr = v['pwr']
        yield ('posedge',)
        v1 = sim.hist[sim.k - 2]
        data = v1['pwdata'] if wr else v1['prdata']
        sim.log.append((sim.t, 'monitor_bus: monitored addr %02x, cmd %s, data 0x%08x' % (addr, 'write' if wr else 'read', data)))


def chan_seq(sim, ch, ntrans, data_size, data_nidles, pkt_nidles, done):
    """sequence_channel + driver_channel::drive_one_pkt (item hand-over costs an NBA delta, no time)."""
    yield ('nba',)
    for pkt in range(ntrans):
        for i in range(data_size):
            yield ('posedge',)
            sim.drive_ch(ch, 1, word(ch, pkt, i))
            yield ('negedge',)
            yield ('wait', lambda: sim.dut.ready(ch) == 1)
            for _ in range(data_nidles):
                yield ('posedge',)
                sim.drive_ch(ch, 0, 0)
        for _ in range(pkt_nidles):
            yield ('posedge',)
            sim.drive_ch(ch, 0, 0)
    done(sim.t)


def fmt_receive(sim):
    """driver_formatter::do_receive"""
    while True:
        yield ('sig', 'fmt_req')
        while True:
            yield ('posedge',)
            space = (1 << 60) if sim.fifo_unbounded else (sim.fifo_bound - sim.fifo_num)
            if space >= sim.dut.length():
                break
        sim.drive(grant=1)
        yield ('sig', 'fmt_start')
        L = sim.dut.length()

        def drop():
            yield ('posedge',)
            sim.drive(grant=0)
        sim.spawn(drop())
        for _ in range(L):
            yield ('negedge',)
            if (not sim.fifo_unbounded) and sim.fifo_num >= sim.fifo_bound:
                raise RuntimeError('fmt driver mailbox put would block')
            sim.fifo_num += 1


def fmt_consume(sim):
    """driver_formatter::do_consume"""
    while True:
        if sim.fifo_num > 0:
            sim.fifo_num -= 1
        n = sim.rng.randint(1, sim.consume_period)
        for _ in range(n):
            yield ('posedge',)


def arb_checker(sim):
    """scoreboard do_arbiter_priority_check with old-sample clockvars (cycle j-2 at posedge j)."""
    while True:
        yield ('posedge',)
        j = sim.k
        if j < 2 or not sim.rstn:
            continue
        v = sim.hist[j - 2]
        if v['f2a_id_req'] != 1:
            continue
        idx = -1
        prio = 999
        for i in range(3):
            if v['prios'][i] < prio and v['reqs'][i] == 1:
                idx = i
                prio = v['prios'][i]
        if idx >= 0:
            yield ('posedge',)
            v1 = sim.hist[sim.k - 2]
            if v1['acks'][idx] != 1:
                sim.chk_errors.append((sim.t, 'arbiter CHKERR ch%d' % idx))


def disable_checker(sim, ch):
    while True:
        yield ('posedge',)
        j = sim.k
        if j < 2 or not sim.rstn:
            continue
        v = sim.hist[j - 2]
        if v['en'][ch] == 0 and v['valid'][ch] == 1 and v['ready'][ch] == 1:
            sim.chk_errors.append((sim.t, 'channel disable CHKERR ch%d' % ch))


def fmt_config(sim, bound, period):
    sim.fifo_bound = bound
    sim.fifo_unbounded = False
    sim.fifo_num = 0          # this.fifo = new(bound)
    sim.consume_period = period


# ----------------------------------------------------------------------------- tests
def run_test(name, seed=1, verbose=False, consume_first=True, quiet=False, override=None):
    """override (case2 only): dict(vals=[v0,v1,v2], fmt=(bound, period), params=[(ntrans,size,dn,pn)]*3)"""
    override = override or {}
    sim = Sim(verbose=verbose, consume_first=consume_first, seed=seed)
    rng = random.Random(seed)
    st = dict(reads=None, cfg_done=None, main_start=None, ch_done={}, end=None, cfg=None)

    # ---- configure_phase sequence
    if name == 'case0':
        vals = [0x01, 0x09, 0x11]
        ops = [(CMD_WRITE, 0x00, vals[0]), (CMD_WRITE, 0x04, vals[1]), (CMD_WRITE, 0x08, vals[2]),
               (CMD_READ, 0x00, 0), (CMD_READ, 0x04, 0), (CMD_READ, 0x08, 0), (CMD_IDLE, 0, 0)]
        pre_idle_delay = 0
    elif name == 'case1':
        vals = [(1 << 3) + (0 << 1) + 1, (2 << 3) + (1 << 1) + 1, (3 << 3) + (3 << 1) + 1]
        ops = [(CMD_WRITE, 0x00, vals[0]), (CMD_READ, 0x00, 0), (CMD_WRITE, 0x04, vals[1]), (CMD_READ, 0x04, 0),
               (CMD_WRITE, 0x08, vals[2]), (CMD_READ, 0x08, 0), (CMD_IDLE, 0, 0)]
        pre_idle_delay = 0
    else:
        vals = [(rng.randint(0, 1) << 3) + (rng.randint(0, 3) << 1) + 1 for _ in range(3)]
        vals = override.get('vals', vals)
        # set()+update() three times.  uvm_reg_block::update() re-writes every register whose mirror
        # differs from the desired value.  The mirror is only refreshed by the explicit predictor when
        # monitor_bus publishes the transfer, 10 ns AFTER the frontdoor write returned, so the update()
        # issued immediately after a write still sees the previous register as stale and writes it again
        # (registers visited in creation order ch0, ch1, ch2):  00,00,04,04,08 when all three change.
        ops = []
        if vals[0] != 7:
            ops += [(CMD_WRITE, 0x00, vals[0])] * 2
        if vals[1] != 7:
            ops += [(CMD_WRITE, 0x04, vals[1])] * 2
        if vals[2] != 7:
            ops += [(CMD_WRITE, 0x08, vals[2])]
        pre_idle_delay = 100
    st['cfg'] = vals

    def cfg_seq():
        box = {}

        def done(rd, t):
            box['rd'] = rd
            box['t'] = t
        if name == 'case2':
            wr_ops = ops
            if wr_ops:
                sim.spawn(bus_driver(sim, wr_ops, done))
                while 't' not in box:
                    yield ('posedge',)
                    # (polling at posedges is only a modelling convenience; done() fires in phase B)
                t_w = box['t']
                # sequence continues at t_w; '#100ns' from there
                yield ('delay', 100 - (sim.t - t_w))
            else:
                yield ('delay', 100)
            st['mirror_time'] = sim.t
            st['mirror_values'] = [sim.dut.mem[i] for i in range(3)]
            # IDLE item: driver may still be waiting for the first posedge with rstn==1
            if not wr_ops:
                while True:
                    yield ('posedge',)
                    if sim.rstn:
                        break
            t_idle = sim.t
            st['cfg_done'] = t_idle
            yield ('delay', 500)
        else:
            sim.spawn(bus_driver(sim, ops, done))
            while 't' not in box:
                yield ('posedge',)
            st['reads'] = box['rd']
            st['cfg_done'] = box['t']
            yield ('delay', 500 - (sim.t - box['t']))
        st['main_start'] = sim.t
        sim.spawn(main_seq())

    # ---- main_phase sequence
    def main_seq():
        yield ('delay', 100)
        box = {}

        def mk_done(ch):
            def f(t):
                box[ch] = t
                st['ch_done'][ch] = t
            return f

        if name == 'case0':
            fmt_config(sim, 2048, 1)
            st['params'] = [(40, 8, 0, 1), (20, 16, 0, 2), (10, 32, 0, 4)]
            for ch in range(3):
                nt, ds, dn, pn = st['params'][ch]
                sim.spawn(chan_seq(sim, ch, nt, ds, dn, pn, mk_done(ch)))
                while ch not in box:
                    yield ('posedge',)
                if ch < 2:
                    yield ('delay', 500 - (sim.t - box[ch]))
            yield ('delay', 5000 - (sim.t - box[2]))
        else:
            if name == 'case1':
                fmt_config(sim, 512, 2)
                st['params'] = [(100, 8, 0, 1), (100, 16, 1, 4), (100, 32, 2, 8)]
            else:
                fb, pr = rng.choice([64, 2048]), rng.choice([8, 1])
                pp = [(rng.randint(400, 600), rng.choice([8, 16, 32]), rng.randint(0, 3),
                       rng.choice([1, 2, 4, 8])) for _ in range(3)]
                fb, pr = override.get('fmt', (fb, pr))
                fmt_config(sim, fb, pr)
                st['params'] = override.get('params', pp)
            for ch in range(3):
                nt, ds, dn, pn = st['params'][ch]
                sim.spawn(chan_seq(sim, ch, nt, ds, dn, pn, mk_done(ch)))
            while len(box) < 3:
                yield ('posedge',)
            tj = max(box.values()) + 500
            yield ('delay', tj + 10000 - sim.t)
        st['end'] = sim.t
        sim.finished = True

    sim.spawn(monitor_bus(sim))
    sim.spawn(arb_checker(sim))
    for ch in range(3):
        sim.spawn(disable_checker(sim, ch))
    gc = fmt_consume(sim)
    sim.prio[id(gc)] = 0 if consume_first else 2
    sim.spawn(gc)
    sim.spawn(fmt_receive(sim))
    sim.spawn(cfg_seq())

    max_fifo = 0
    while not sim.finished:
        sim.step()
        max_fifo = max(max_fifo, sim.fifo_num)
        if sim.k > 5_000_000:
            raise RuntimeError('model timeout')

    # ---- scoreboard emulation
    lens = [4 << ((v >> 3) & 7) for v in vals]
    exp = []
    for ch in range(3):
        ws = [w for (_, w) in sim.accepted[ch]]
        exp.append([ws[i:i + lens[ch]] for i in range(0, len(ws) - len(ws) % lens[ch], lens[ch])])
    cnt = [0, 0, 0]
    err = 0
    cmp_times = []
    for (s, ch, L, data) in sim.out_pkts:
        t_cmp = tpos(s) + 10 * (L + 1)
        if t_cmp >= st['end']:
            continue                      # monitor would not have finished before the objection drops
        e = exp[ch][cnt[ch]] if cnt[ch] < len(exp[ch]) else None
        if e is None or e != data or L != lens[ch]:
            err += 1
        cnt[ch] += 1
        cmp_times.append(t_cmp)
    res = dict(name=name, seed=seed, cfg=vals, lens=lens, params=st.get('params'), reads=st['reads'],
               cfg_done=st['cfg_done'], main_start=st['main_start'], ch_done=st['ch_done'], end=st['end'],
               counts=cnt, total=sum(cnt), cmp_err=err, chk_errors=sim.chk_errors,
               first_cmp=cmp_times[0] if cmp_times else None, last_cmp=cmp_times[-1] if cmp_times else None,
               words_in=[len(sim.accepted[i]) for i in range(3)], pkts_out_total=len(sim.out_pkts),
               leftover_fifo=[(sim.dut.wr[i] - sim.dut.rd[i]) & 0x3F for i in range(3)],
               leftover_buf=list(sim.dut.buf_val), fifo_bound=sim.fifo_bound, period=sim.consume_period,
               max_mailbox=max_fifo, mirror=st.get('mirror_values'), mirror_time=st.get('mirror_time'),
               expected_pkts=[len(e) for e in exp])
    if not quiet:
        print('==== %s (seed %d) ====' % (name, seed))
        for (t, m) in sim.log:
            if 'monitor_bus' in m or verbose:
                print('  %8d ns  %s' % (t, m))
        for k_ in ('cfg', 'lens', 'params', 'reads', 'mirror', 'mirror_time', 'cfg_done', 'main_start', 'ch_done',
                   'words_in', 'expected_pkts', 'counts', 'total', 'cmp_err', 'chk_errors', 'first_cmp', 'last_cmp',
                   'end', 'leftover_fifo', 'leftover_buf', 'fifo_bound', 'period', 'max_mailbox'):
            print('  %-14s %s' % (k_, res[k_]))
        if verbose:
            for (s, ch, L, data) in sim.out_pkts[:6]:
                print('  pkt start@%d ns ch%d len%d cmp@%d ns data=%s' % (tpos(s), ch, L, tpos(s) + 10 * (L + 1),
                                                                         ' '.join('%08x' % x for x in data[:4])))
    return res, sim


if __name__ == '__main__':
    args = [a for a in sys.argv[1:] if not a.startswith('-')]
    verbose = '-v' in sys.argv
    name = args[0] if args else 'case0'
    seed = int(args[1]) if len(args) > 1 else 1
    run_test(name, seed, verbose)
